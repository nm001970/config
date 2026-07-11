from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.config_writer import save_temp_config, delete_temp_config

from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config

from worker.quality_checker import test_proxy_quality

from database.repositories.config_repository import get_config_by_id
from database.repositories.quality_repository import update_quality_result

MIN_QUALITY_DOWNLOAD_MBPS = 1.0
MIN_QUALITY_UPLOAD_MBPS = 0.3
MAX_QUALITY_PING_AVG_MS = 2500


def normalize_quality_result(result):
    result = dict(result or {})

    if not result.get("alive"):
        result["alive"] = False
        result["quality_reject_reason"] = result.get("error") or "quality_connectivity_failed"
        return result

    ping_avg = result.get("ping_avg_ms")
    download = result.get("download_mbps")
    upload = result.get("upload_mbps")

    download_error = result.get("download_error")
    upload_error = result.get("upload_error")

    reject_reasons = []

    if ping_avg is None:
        reject_reasons.append("missing_ping_avg")
    elif float(ping_avg) > MAX_QUALITY_PING_AVG_MS:
        reject_reasons.append(f"ping_too_high:{ping_avg}")

    if download is None:
        reject_reasons.append("missing_download")
    elif float(download) < MIN_QUALITY_DOWNLOAD_MBPS:
        reject_reasons.append(f"download_too_low:{download}")

    if upload is None:
        reject_reasons.append("missing_upload")
    elif float(upload) < MIN_QUALITY_UPLOAD_MBPS:
        reject_reasons.append(f"upload_too_low:{upload}")

    if download_error:
        reject_reasons.append(f"download_error:{download_error}")

    if upload_error:
        reject_reasons.append(f"upload_error:{upload_error}")

    if reject_reasons:
        result["alive"] = False
        result["quality_reject_reason"] = " | ".join(reject_reasons)
        result["error"] = result.get("error") or result["quality_reject_reason"]

    return result

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
        
        result = test_proxy_quality(socks_port)
        result = normalize_quality_result(result)

        print("QUALITY RESULT:", result)

        update_quality_result(row.id, result)

        if result.get("alive"):
            print(
                f"[QUALITY DONE] ID={row.id} "
                f"PING={result.get('ping_avg_ms')} "
                f"DOWN={result.get('download_mbps')} "
                f"UP={result.get('upload_mbps')}"
            )

        else:
            print(
                f"[QUALITY REJECTED] ID={row.id} "
                f"ERROR={result.get('quality_reject_reason') or result.get('error')}"
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