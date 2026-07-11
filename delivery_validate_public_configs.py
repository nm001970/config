import json
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.config_writer import save_temp_config, delete_temp_config
from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config


DEFAULT_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")

WORKERS = int(os.getenv("DELIVERY_VALIDATION_WORKERS", "5"))
TIMEOUT = float(os.getenv("DELIVERY_VALIDATION_TIMEOUT", "8"))
STARTUP_TIMEOUT = float(os.getenv("DELIVERY_VALIDATION_STARTUP_TIMEOUT", "4"))
RETEST_DELAY = float(os.getenv("DELIVERY_VALIDATION_RETEST_DELAY", "5"))

MIN_SUCCESSFUL_ENDPOINTS = int(os.getenv("DELIVERY_VALIDATION_MIN_SUCCESS", "2"))
REQUIRE_RETEST = os.getenv("DELIVERY_VALIDATION_REQUIRE_RETEST", "true").lower() == "true"

MIN_HOT = int(os.getenv("DELIVERY_VALIDATION_MIN_HOT", os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "5")))

REQUIRE_EXIT_IP = (
    os.getenv("DELIVERY_VALIDATION_REQUIRE_EXIT_IP", "true").lower() == "true"
)

REQUIRE_SMALL_DOWNLOAD = (
    os.getenv("DELIVERY_VALIDATION_REQUIRE_SMALL_DOWNLOAD", "true").lower() == "true"
)

SMALL_DOWNLOAD_BYTES = int(
    os.getenv("DELIVERY_VALIDATION_DOWNLOAD_BYTES", "262144")
)

MIN_SMALL_DOWNLOAD_MBPS = float(
    os.getenv("DELIVERY_VALIDATION_MIN_DOWNLOAD_MBPS", "0.5")
)

SMALL_DOWNLOAD_URL = os.getenv(
    "DELIVERY_VALIDATION_DOWNLOAD_URL",
    f"https://speed.cloudflare.com/__down?bytes={SMALL_DOWNLOAD_BYTES}",
)

ENDPOINTS = [
    {
        "name": "ipify",
        "url": "https://api.ipify.org?format=json",
        "kind": "json_ip",
    },
    {
        "name": "gstatic_204",
        "url": "https://www.gstatic.com/generate_204",
        "kind": "204",
    },
    {
        "name": "cloudflare_204",
        "url": "https://cp.cloudflare.com/generate_204",
        "kind": "204",
    },
]


def get_direct_ip():
    try:
        response = requests.get(
            "https://api.ipify.org?format=json",
            timeout=TIMEOUT,
        )

        if response.status_code != 200:
            return None

        payload = response.json()
        return payload.get("ip") or payload.get("origin")

    except Exception:
        return None


def wait_for_port(port, timeout=STARTUP_TIMEOUT):
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.15)

    return False


def validate_endpoint_response(endpoint, response):
    kind = endpoint["kind"]

    if kind == "204":
        return response.status_code == 204, None

    if kind == "json_ip":
        if response.status_code != 200:
            return False, None

        try:
            payload = response.json()
        except Exception:
            return False, None

        ip = payload.get("ip") or payload.get("origin")

        if not ip:
            return False, None

        return True, ip

    return False, None

def build_proxies(socks_port):
    return {
        "http": f"socks5h://127.0.0.1:{socks_port}",
        "https": f"socks5h://127.0.0.1:{socks_port}",
    }


def run_endpoint_checks(socks_port, direct_ip=None):
    proxies = build_proxies(socks_port)

    passed = 0
    exit_ip = None
    errors = []

    for endpoint in ENDPOINTS:
        try:
            response = requests.get(
                endpoint["url"],
                proxies=proxies,
                timeout=TIMEOUT,
                allow_redirects=False,
            )

            ok, ip = validate_endpoint_response(endpoint, response)

            if not ok:
                errors.append(
                    f"{endpoint['name']}:bad_response:{response.status_code}"
                )
                continue

            if ip:
                exit_ip = ip

            passed += 1

        except Exception as exc:
            errors.append(f"{endpoint['name']}:{repr(exc)}")

    if passed < MIN_SUCCESSFUL_ENDPOINTS:
        return {
            "ok": False,
            "passed": passed,
            "exit_ip": exit_ip,
            "error": (
                f"not_enough_successful_endpoints:"
                f"{passed}<{MIN_SUCCESSFUL_ENDPOINTS}; "
                + "; ".join(errors)
            ),
        }

    if REQUIRE_EXIT_IP and not exit_ip:
        return {
            "ok": False,
            "passed": passed,
            "exit_ip": exit_ip,
            "error": "missing_exit_ip",
        }

    if direct_ip and exit_ip and exit_ip == direct_ip:
        return {
            "ok": False,
            "passed": passed,
            "exit_ip": exit_ip,
            "error": "exit_ip_equals_direct_ip",
        }

    return {
        "ok": True,
        "passed": passed,
        "exit_ip": exit_ip,
        "error": "; ".join(errors),
    }

def run_small_download(socks_port):
    if not REQUIRE_SMALL_DOWNLOAD:
        return {
            "ok": True,
            "bytes": 0,
            "mbps": None,
            "error": None,
        }

    proxies = build_proxies(socks_port)

    started = time.perf_counter()
    total_bytes = 0

    try:
        response = requests.get(
            SMALL_DOWNLOAD_URL,
            proxies=proxies,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=False,
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "bytes": total_bytes,
                "mbps": None,
                "error": f"small_download_bad_status:{response.status_code}",
            }

        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue

            total_bytes += len(chunk)

            if total_bytes >= SMALL_DOWNLOAD_BYTES:
                break

        elapsed = max(time.perf_counter() - started, 0.001)
        mbps = (total_bytes * 8) / elapsed / 1_000_000

        if total_bytes < SMALL_DOWNLOAD_BYTES:
            return {
                "ok": False,
                "bytes": total_bytes,
                "mbps": round(mbps, 2),
                "error": (
                    f"small_download_incomplete:"
                    f"{total_bytes}<{SMALL_DOWNLOAD_BYTES}"
                ),
            }

        if mbps < MIN_SMALL_DOWNLOAD_MBPS:
            return {
                "ok": False,
                "bytes": total_bytes,
                "mbps": round(mbps, 2),
                "error": (
                    f"small_download_too_slow:"
                    f"{round(mbps, 2)}<{MIN_SMALL_DOWNLOAD_MBPS}"
                ),
            }

        return {
            "ok": True,
            "bytes": total_bytes,
            "mbps": round(mbps, 2),
            "error": None,
        }

    except Exception as exc:
        return {
            "ok": False,
            "bytes": total_bytes,
            "mbps": None,
            "error": f"small_download_error:{repr(exc)}",
        }
    
def run_validation_round(socks_port, direct_ip=None):
    endpoint_result = run_endpoint_checks(
        socks_port,
        direct_ip=direct_ip,
    )

    if not endpoint_result.get("ok"):
        return {
            "ok": False,
            "stage": "endpoints",
            "passed": endpoint_result.get("passed"),
            "exit_ip": endpoint_result.get("exit_ip"),
            "download_mbps": None,
            "error": endpoint_result.get("error"),
        }

    download_result = run_small_download(socks_port)

    if not download_result.get("ok"):
        return {
            "ok": False,
            "stage": "small_download",
            "passed": endpoint_result.get("passed"),
            "exit_ip": endpoint_result.get("exit_ip"),
            "download_mbps": download_result.get("mbps"),
            "error": download_result.get("error"),
        }

    return {
        "ok": True,
        "stage": "complete",
        "passed": endpoint_result.get("passed"),
        "exit_ip": endpoint_result.get("exit_ip"),
        "download_mbps": download_result.get("mbps"),
        "error": None,
    }

def validate_config_text(config_text, direct_ip=None):
    xray_process = None
    temp_path = None

    try:
        parsed_config = parse_config(config_text)
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
            return {
                "ok": False,
                "error": "save_temp_config returned None",
            }

        xray_process, xray_alive, xray_error = start_xray(temp_path)

        if not xray_alive:
            return {
                "ok": False,
                "error": f"XRAY_FAILED: {xray_error}",
            }

        if not wait_for_port(socks_port):
            return {
                "ok": False,
                "error": "socks_port_not_ready",
            }

        first = run_validation_round(
            socks_port,
            direct_ip=direct_ip,
        )

        if not first["ok"]:
            return {
                "ok": False,
                "error": "first_check_failed: " + str(first.get("error")),
                "passed": first.get("passed"),
                "exit_ip": first.get("exit_ip"),
            }

        if REQUIRE_RETEST:
            time.sleep(RETEST_DELAY)

            second = run_validation_round(
                socks_port,
                direct_ip=direct_ip,
            )

            if not second["ok"]:
                return {
                    "ok": False,
                    "error": "retest_failed: " + str(second.get("error")),
                    "passed": second.get("passed"),
                    "exit_ip": second.get("exit_ip"),
                }

            return {
                "ok": True,
                "error": None,
                "passed": second.get("passed"),
                "exit_ip": second.get("exit_ip") or first.get("exit_ip"),
                "download_mbps": second.get("download_mbps") or first.get("download_mbps"),
            }

        return {
            "ok": True,
            "error": None,
            "passed": first.get("passed"),
            "exit_ip": first.get("exit_ip"),
            "download_mbps": first.get("download_mbps"),
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": repr(exc),
        }

    finally:
        if xray_process and xray_process.poll() is None:
            try:
                xray_process.terminate()
                xray_process.wait(timeout=2)
            except Exception:
                try:
                    xray_process.kill()
                except Exception:
                    pass

        if temp_path:
            delete_temp_config(temp_path)


def item_key(item):
    if isinstance(item, dict):
        return str(item.get("source_id") or item.get("server") or item.get("config_text")[:40])
    return str(item)[:40]


def get_config_text(item):
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        return (item.get("config_text") or "").strip()

    return ""


def validate_items(items, section_name, direct_ip=None):
    items = list(items or [])
    accepted = []
    rejected = []

    print()
    print("=" * 70)
    print(f"DELIVERY VALIDATION SECTION: {section_name}")
    print("TOTAL:", len(items))
    print("WORKERS:", WORKERS)
    print("=" * 70)

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_map = {}

        for item in items:
            config_text = get_config_text(item)

            if not config_text:
                rejected.append(
                    {
                        "item": item,
                        "error": "empty_config_text",
                    }
                )
                continue

            future = executor.submit(
                validate_config_text,
                config_text,
                direct_ip,
            )

            future_map[future] = item

        for future in as_completed(future_map):
            item = future_map[future]
            key = item_key(item)

            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "ok": False,
                    "error": repr(exc),
                }

            if result.get("ok"):
                accepted.append(item)
                print(
                    f"[PASS] {section_name} {key} "
                    f"passed={result.get('passed')} "
                    f"exit_ip={result.get('exit_ip')} "
                    f"download={result.get('download_mbps')}"
                )
            else:
                rejected.append(
                    {
                        "item": item,
                        "error": result.get("error"),
                    }
                )
                print(
                    f"[FAIL] {section_name} {key} "
                    f"error={result.get('error')}"
                )

    print()
    print(f"{section_name} ACCEPTED:", len(accepted))
    print(f"{section_name} REJECTED:", len(rejected))

    return accepted, rejected


def load_payload(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def save_payload(path, payload):
    temp_path = str(path) + ".delivery_tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    os.replace(temp_path, path)


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FILE)

    if not path.exists():
        raise FileNotFoundError(path)

    print("=" * 70)
    print("DELIVERY VALIDATION STARTED")
    print("FILE:", path)
    print("MIN_HOT:", MIN_HOT)
    print("MIN_SUCCESSFUL_ENDPOINTS:", MIN_SUCCESSFUL_ENDPOINTS)
    print("REQUIRE_RETEST:", REQUIRE_RETEST)
    print("=" * 70)

    payload = load_payload(path)

    direct_ip = get_direct_ip()
    print("DIRECT_IP:", direct_ip or "UNKNOWN")

    hot = payload.get("hot") or []
    reserve = payload.get("reserve") or []

    valid_hot, rejected_hot = validate_items(
        hot,
        "hot",
        direct_ip=direct_ip,
    )

    valid_reserve, rejected_reserve = validate_items(
        reserve,
        "reserve",
        direct_ip=direct_ip,
    )

    promoted_from_reserve = []

    if len(valid_hot) < MIN_HOT:
        needed = MIN_HOT - len(valid_hot)

        promoted_from_reserve = valid_reserve[:needed]
        valid_hot = valid_hot + promoted_from_reserve
        valid_reserve = valid_reserve[needed:]

        print()
        print("=" * 70)
        print("HOT FILL FROM RESERVE")
        print("NEEDED:", needed)
        print("PROMOTED:", len(promoted_from_reserve))
        print("HOT AFTER PROMOTION:", len(valid_hot))
        print("RESERVE AFTER PROMOTION:", len(valid_reserve))
        print("=" * 70)

    if len(valid_hot) < MIN_HOT:
        raise RuntimeError(
            f"delivery validation failed: hot passed {len(valid_hot)} < required {MIN_HOT}"
        )

    payload["hot"] = valid_hot
    payload["reserve"] = valid_reserve
    payload["configs"] = valid_hot
    payload["hot_count"] = len(valid_hot)
    payload["reserve_count"] = len(valid_reserve)

    payload["delivery_validation"] = {
        "enabled": True,
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "min_hot": MIN_HOT,
        "direct_ip": direct_ip,
        "hot_passed": len(valid_hot),
        "hot_rejected": len(rejected_hot),
        "reserve_passed": len(valid_reserve),
        "reserve_rejected": len(rejected_reserve),
        "promoted_from_reserve": len(promoted_from_reserve),
    }

    save_payload(path, payload)

    print()
    print("=" * 70)
    print("DELIVERY VALIDATION FINISHED SUCCESSFULLY")
    print("HOT:", len(valid_hot))
    print("RESERVE:", len(valid_reserve))
    print("=" * 70)


if __name__ == "__main__":
    main()