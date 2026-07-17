from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.proxy_checker import test_proxy
from worker.config_writer import (
    save_temp_config,
    delete_temp_config,
)
from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config

from database.repositories.config_repository import (
    get_config_by_id,
)
from worker.db_queries import update_result


def _normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def _to_non_negative_float(
    value,
    default=0.0,
):
    try:
        number = float(value)

    except (TypeError, ValueError):
        return float(default)

    if number < 0:
        return float(default)

    return number


def _normalize_proxy_result(result):
    """
    Normalize the result returned by worker.proxy_checker.test_proxy().
    """
    if not isinstance(result, dict):
        return {
            "alive": False,
            "latency": 0.0,
            "ip": None,
            "error": (
                "invalid_proxy_result:"
                f"{type(result).__name__}"
            ),
        }

    alive = bool(
        result.get("alive")
    )
    latency = _to_non_negative_float(
        result.get("latency"),
        default=0.0,
    )

    ip = result.get("ip")

    if ip is not None:
        ip = str(ip).strip() or None

    error = result.get("error")

    if error is not None:
        error = str(error)

    if alive and latency <= 0:
        return {
            "alive": False,
            "latency": 0.0,
            "ip": None,
            "error": (
                "invalid_alive_latency:"
                f"{result.get('latency')}"
            ),
        }

    if alive:
        error = None

    elif not error:
        error = "proxy_test_failed"

    return {
        "alive": alive,
        "latency": latency if alive else 0.0,
        "ip": ip if alive else None,
        "error": error,
    }


def _build_xray_failed_result(
    xray_error,
):
    return {
        "status": "dead",
        "alive": False,
        "latency": 0.0,
        "ip": None,
        "error": (
            "XRAY_FAILED: "
            f"{xray_error or 'unknown error'}"
        ),
    }


def _build_exception_result(
    exception,
):
    return {
        "status": "error",
        "alive": False,
        "latency": 0.0,
        "ip": None,
        "error": repr(exception),
    }


def _stop_xray_process(
    xray_process,
):
    if xray_process is None:
        return

    if xray_process.poll() is not None:
        return

    try:
        xray_process.terminate()
        xray_process.wait(
            timeout=3
        )

    except Exception:
        try:
            xray_process.kill()
            xray_process.wait(
                timeout=3
            )

        except Exception:
            pass


def _cleanup_temp_config(
    temp_path,
):
    if not temp_path:
        return

    try:
        delete_temp_config(
            temp_path
        )

    except Exception as exception:
        print(
            "[CONNECTIVITY CLEANUP ERROR] "
            f"PATH={temp_path} "
            f"ERROR={repr(exception)}"
        )


def _persist_connectivity_result(
    config_id,
    result,
):
    """
    Persist exactly one final Connectivity result.

    Database exceptions are intentionally not swallowed. They must
    propagate to v2_live_worker.py and then to the Smart cycle.
    """
    status = str(
        result.get("status") or "error"
    )

    update_result(
        config_id,
        status,
        result.get("latency") or 0,
        error=result.get("error"),
        exist_ip=result.get("ip"),
    )


def _print_connectivity_result(
    config_id,
    result,
    socks_port=None,
):
    status = result.get("status")

    if status == "alive":
        print(
            f"[ALIVE] ID={config_id} "
            f"LATENCY={result.get('latency')} "
            f"IP={result.get('ip')} "
            f"PORT={socks_port}"
        )
        return

    if status == "dead":
        print(
            f"[DEAD] ID={config_id} "
            f"ERROR={result.get('error')}"
        )
        return

    print(
        f"[ERROR] ID={config_id} "
        f"{result.get('error')}"
    )


def test_config(config_id):
    """
    Test Connectivity for one config and persist the final result.

    Returns:
        dict:
            status, alive, latency, ip, error

        None:
            when the config does not exist.
    """
    config_id = _normalize_config_id(
        config_id
    )

    row = get_config_by_id(
        config_id
    )

    if row is None:
        print(
            "[CONNECTIVITY ERROR] "
            f"ID={config_id} "
            "CONFIG_NOT_FOUND"
        )
        return None

    print(
        f"[TEST] ID={row.id} "
        f"PROTOCOL={row.protocol}"
    )

    xray_process = None
    temp_path = None
    socks_port = None

    try:
        parsed_config = parse_config(
            row.config_text
        )

        socks_port = get_free_port()

        xray_json = build_xray_config(
            parsed_config,
            socks_port,
        )

        temp_path = save_temp_config(
            xray_json,
            socks_port,
        )

        if temp_path is None:
            raise RuntimeError(
                "save_temp_config returned None"
            )

        print(
            f"[TEMP] {temp_path}"
        )

        (
            xray_process,
            xray_alive,
            xray_error,
        ) = start_xray(
            temp_path
        )

        if not xray_alive:
            result = (
                _build_xray_failed_result(
                    xray_error
                )
            )

        else:
            try:
                raw_result = test_proxy(
                    socks_port
                )

            except Exception as exception:
                raw_result = {
                    "alive": False,
                    "latency": 0.0,
                    "ip": None,
                    "error": (
                        "proxy_checker_crashed:"
                        f"{repr(exception)}"
                    ),
                }

            normalized = (
                _normalize_proxy_result(
                    raw_result
                )
            )

            result = {
                "status": (
                    "alive"
                    if normalized["alive"]
                    else "dead"
                ),
                **normalized,
            }

    except Exception as exception:
        result = _build_exception_result(
            exception
        )

    finally:
        _stop_xray_process(
            xray_process
        )
        _cleanup_temp_config(
            temp_path
        )

    _persist_connectivity_result(
        row.id,
        result,
    )

    _print_connectivity_result(
        row.id,
        result,
        socks_port=socks_port,
    )

    return result


__all__ = [
    "test_config",
]