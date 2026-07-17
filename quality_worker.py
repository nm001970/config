from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.config_writer import (
    save_temp_config,
    delete_temp_config,
)
from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config
from worker.quality_checker import test_proxy_quality

from database.repositories.config_repository import (
    get_config_by_id,
)
from database.repositories.quality_repository import (
    update_quality_result,
)


MIN_QUALITY_DOWNLOAD_MBPS = 1.5
MIN_QUALITY_UPLOAD_MBPS = 0.3
MAX_QUALITY_PING_AVG_MS = 2500.0


def _to_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def _normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def _is_ping_acceptable(ping_avg):
    return (
        ping_avg is not None
        and ping_avg <= MAX_QUALITY_PING_AVG_MS
    )


def _is_download_acceptable(download):
    return (
        download is not None
        and download >= MIN_QUALITY_DOWNLOAD_MBPS
    )


def _is_upload_failure_only(
    result,
    ping_avg,
    download,
    upload,
):
    """
    Detect an optional Upload endpoint failure that must not reject an
    otherwise valid Ping + Download result.
    """
    failed_stage = str(
        result.get("failed_stage") or ""
    ).strip().lower()

    return (
        failed_stage == "upload"
        and upload is None
        and _is_ping_acceptable(ping_avg)
        and _is_download_acceptable(download)
    )


def normalize_quality_result(result):
    """
    Normalize one raw Quality result.

    Mandatory:
        - Quality connectivity / HTTP ping.
        - Average ping <= configured maximum.
        - Successful Download with speed >= configured minimum.

    Optional:
        - Upload measurement is attempted after Download.
        - Upload endpoint failure or timeout does not reject the config.
        - A successfully measured numeric Upload below the configured
          minimum rejects the config.
    """
    result = dict(result or {})

    raw_ping_avg = result.get(
        "ping_avg_ms"
    )
    raw_download = result.get(
        "download_mbps"
    )
    raw_upload = result.get(
        "upload_mbps"
    )

    ping_avg = _to_float(
        raw_ping_avg
    )
    download = _to_float(
        raw_download
    )
    upload = _to_float(
        raw_upload
    )

    result["ping_avg_ms"] = ping_avg
    result["download_mbps"] = download
    result["upload_mbps"] = upload
    result["upload_verified"] = (
        upload is not None
    )

    # Enforce project policy even if the lower-level checker reports an
    # optional Upload endpoint failure as alive=False.
    if (
        not result.get("alive")
        and _is_upload_failure_only(
            result=result,
            ping_avg=ping_avg,
            download=download,
            upload=upload,
        )
    ):
        result["alive"] = True
        result["failed_stage"] = None
        result["quality_reject_reason"] = None
        result["error"] = None
        result["upload_verified"] = False
        result["upload_mbps"] = None

        return result

    # A lower-level mandatory stage already failed. Preserve that
    # failure and make its result contract stable.
    if not result.get("alive"):
        reject_reason = (
            result.get(
                "quality_reject_reason"
            )
            or result.get("error")
            or result.get(
                "download_error"
            )
            or result.get("ping_error")
            or "quality_stage_failed"
        )

        if not result.get("failed_stage"):
            if result.get(
                "download_error"
            ):
                result["failed_stage"] = (
                    "download"
                )

            elif result.get(
                "ping_error"
            ):
                result["failed_stage"] = (
                    "ping"
                )

            else:
                result["failed_stage"] = (
                    "quality"
                )

        result["alive"] = False
        result["quality_reject_reason"] = (
            str(reject_reason)
        )
        result["error"] = str(
            reject_reason
        )

        return result

    reject_reasons = []

    # Ping is mandatory.
    if raw_ping_avg is None:
        reject_reasons.append(
            "missing_ping_avg"
        )

    elif ping_avg is None:
        reject_reasons.append(
            f"invalid_ping_avg:{raw_ping_avg}"
        )

    elif (
        ping_avg
        > MAX_QUALITY_PING_AVG_MS
    ):
        reject_reasons.append(
            f"ping_too_high:{ping_avg}"
        )

    # Download is mandatory.
    if raw_download is None:
        reject_reasons.append(
            "missing_download"
        )

        download_error = result.get(
            "download_error"
        )

        if download_error:
            reject_reasons.append(
                "download_error:"
                f"{download_error}"
            )

    elif download is None:
        reject_reasons.append(
            f"invalid_download:{raw_download}"
        )

    elif (
        download
        < MIN_QUALITY_DOWNLOAD_MBPS
    ):
        reject_reasons.append(
            f"download_too_low:{download}"
        )

    # Upload is optional.
    #
    # Missing/invalid Upload or upload_error is monitoring information
    # only. A numeric measured Upload below the threshold rejects.
    if raw_upload is not None:
        if upload is None:
            result["upload_verified"] = (
                False
            )
            result["upload_mbps"] = None

        elif (
            upload
            < MIN_QUALITY_UPLOAD_MBPS
        ):
            reject_reasons.append(
                f"upload_too_low:{upload}"
            )

    if reject_reasons:
        reject_reason = " | ".join(
            reject_reasons
        )

        if any(
            reason.startswith(
                (
                    "missing_download",
                    "invalid_download",
                    "download_too_low",
                    "download_error",
                )
            )
            for reason in reject_reasons
        ):
            failed_stage = "download"

        elif any(
            reason.startswith(
                (
                    "missing_ping_avg",
                    "invalid_ping_avg",
                    "ping_too_high",
                )
            )
            for reason in reject_reasons
        ):
            failed_stage = "ping"

        elif any(
            reason.startswith(
                "upload_too_low"
            )
            for reason in reject_reasons
        ):
            failed_stage = "upload"

        else:
            failed_stage = "quality"

        result["alive"] = False
        result["failed_stage"] = (
            failed_stage
        )
        result["quality_reject_reason"] = (
            reject_reason
        )
        result["error"] = reject_reason

        return result

    result["alive"] = True
    result["failed_stage"] = None
    result["quality_reject_reason"] = None
    result["error"] = None

    return result


def _build_xray_failed_result(
    xray_error,
):
    error = (
        "XRAY_FAILED: "
        f"{xray_error or 'unknown error'}"
    )

    return {
        "alive": False,
        "ip": None,
        "failed_stage": "xray",
        "ping_min_ms": None,
        "ping_avg_ms": None,
        "download_mbps": None,
        "upload_mbps": None,
        "ping_error": None,
        "download_error": None,
        "upload_error": None,
        "upload_verified": False,
        "quality_reject_reason": error,
        "error": error,
    }


def _build_exception_result(
    exception,
):
    error = repr(exception)

    return {
        "alive": False,
        "ip": None,
        "failed_stage": "exception",
        "ping_min_ms": None,
        "ping_avg_ms": None,
        "download_mbps": None,
        "upload_mbps": None,
        "ping_error": None,
        "download_error": None,
        "upload_error": None,
        "upload_verified": False,
        "quality_reject_reason": error,
        "error": error,
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
            "[QUALITY CLEANUP ERROR] "
            f"PATH={temp_path} "
            f"ERROR={repr(exception)}"
        )


def _print_quality_result(
    config_id,
    result,
):
    if result.get("alive"):
        print(
            f"[QUALITY DONE] ID={config_id} "
            f"PING={result.get('ping_avg_ms')} "
            f"DOWN={result.get('download_mbps')} "
            f"UP={result.get('upload_mbps')} "
            "UPLOAD_VERIFIED="
            f"{result.get('upload_verified')}"
        )

        return

    print(
        f"[QUALITY REJECTED] ID={config_id} "
        f"STAGE={result.get('failed_stage')} "
        "ERROR="
        f"{result.get('quality_reject_reason')}"
    )


def test_quality(config_id):
    """
    Run one independent Quality validation.

    Test and cleanup failures become a normalized Quality result.
    Database persistence failure is not swallowed; it propagates to
    v2_live_worker/process cycle so the failure is visible.
    """
    config_id = _normalize_config_id(
        config_id
    )

    row = get_config_by_id(
        config_id
    )

    if row is None:
        print(
            f"[QUALITY ERROR] ID={config_id} "
            "CONFIG_NOT_FOUND"
        )
        return None

    print(
        f"[QUALITY TEST] ID={row.id} "
        f"PROTOCOL={row.protocol}"
    )

    xray_process = None
    temp_path = None

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
            raw_result = (
                test_proxy_quality(
                    socks_port
                )
            )

            result = (
                normalize_quality_result(
                    raw_result
                )
            )

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

    print(
        "QUALITY RESULT:",
        result,
    )

    # Persist exactly once. A persistence exception must propagate rather
    # than being converted into another synthetic Quality result.
    update_quality_result(
        row.id,
        result,
    )

    _print_quality_result(
        row.id,
        result,
    )

    return result


__all__ = [
    "normalize_quality_result",
    "test_quality",
]