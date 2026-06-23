from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.proxy_checker import test_proxy
from worker.config_writer import save_temp_config, delete_temp_config

from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config

from database.repositories.config_repository import get_config_by_id
from worker.db_queries import update_result


def test_config(config_id):

    xray_process = None
    temp_path = None

    try:
        row = get_config_by_id(config_id)

        if not row:
            print(f"[ERROR] Config not found: {config_id}")
            return

        print(f"[TEST] ID={row.id} PROTOCOL={row.protocol}")

        parsed_config = parse_config(row.config_text)

        socks_port = get_free_port()

        xray_json = build_xray_config(
            parsed_config,
            socks_port
        )

        temp_path = save_temp_config(
            xray_json,
            socks_port
        )

        if temp_path is None:
            raise Exception("save_temp_config returned None")

        print(f"[TEMP] {temp_path}")

        xray_process, xray_alive, xray_error = start_xray(temp_path)

        if not xray_alive:
            update_result(
                row.id,
                "dead",
                0,
                error=f"XRAY_FAILED: {xray_error}",
                exist_ip=None
            )

            print(
                f"[DEAD] ID={row.id} "
                f"XRAY_FAILED={xray_error}"
            )
            return

        try:
            result = test_proxy(socks_port)

        except Exception as e:
            result = {
                "alive": False,
                "latency": 0,
                "ip": None,
                "error": f"proxy_checker_crashed: {repr(e)}"
            }

        alive = result.get("alive", False)
        latency = result.get("latency") or 0
        ip = result.get("ip")
        error = result.get("error")

        if alive:
            update_result(
                row.id,
                "alive",
                latency,
                error=None,
                exist_ip=ip
            )

            print(
                f"[ALIVE] ID={row.id} "
                f"LATENCY={latency} "
                f"IP={ip} "
                f"PORT={socks_port}"
            )

        else:
            update_result(
                row.id,
                "dead",
                0,
                error=error,
                exist_ip=None
            )

            print(
                f"[DEAD] ID={row.id} "
                f"ERROR={error}"
            )

    except Exception as e:
        update_result(
            config_id,
            "error",
            0,
            error=repr(e),
            exist_ip=None
        )

        print(f"[ERROR] ID={config_id} {repr(e)}")

    finally:
        if xray_process and xray_process.poll() is None:
            xray_process.terminate()

        if temp_path:
            delete_temp_config(temp_path)