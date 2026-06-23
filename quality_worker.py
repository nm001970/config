from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.config_writer import save_temp_config, delete_temp_config

from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config

from worker.quality_checker import test_proxy_quality

from database.repositories.config_repository import get_config_by_id
from database.repositories.quality_repository import update_quality_result

def test_quality(config_id):
    xray_process= None
    temp_path= None

    try:
        row = get_config_by_id(config_id)

        if not row:
            print(f"[QUALITY TEST] ID={config_id}")
            return
        
        print(f"[QUALITY TEST] ID={row.id} PROTOCOL={row.protocol}")

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

        xray_process, alive, xray_error= start_xray(temp_path)

        if not alive:
            result ={
                "alive":False,
                "error": f"XRAY_FAILED: {xray_error}"
            }

            update_quality_result(row.id, result)

            print(
                f"[QUALITY REJECTED] ID={row.id}"
                f"XRAY_FAILED"
            )
            return
        
        result= test_proxy_quality(socks_port)
        print("FU;; RESULT:", result)

        update_quality_result(row.id, result)

        if result.get("alive"):
            print(
                f"[QUALITY DONE] ID={row.id}"
                f"PING={result.get('ping_avg_ms')}"
                f"DOWN={result.get('download_mbps')}"
                f"UP={result.get('upload_mbps')}"
            )

        else:
            print(
                f"[QUALITY REJECTED] ID={row.id}"
                f"ERROR={result.get('error')}"
            )

    except Exception as e:
        result = {
            "alive": False,
            "error": repr(e)
        }

        update_quality_result(config_id, result)

        print(f"[QUALITY ERROR] ID={config_id} {repr(e)}")

    finally:
        if xray_process and xray_process.poll() is None:
            xray_process.terminate()
        
        if temp_path:
            delete_temp_config(temp_path)