import hashlib
import ipaddress
import json
import os
import socket
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import (
    ThreadPoolExecutor,
    as_completed,
)
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from worker.port_manager import get_free_port
from worker.xray_tester import start_xray
from worker.config_writer import (
    save_temp_config,
    delete_temp_config,
)
from worker.parsers.factory import parse_config
from worker.builders.factory import (
    build_xray_config,
)


DEFAULT_FILE = os.getenv(
    "PUBLIC_CONFIGS_FILE",
    "public_configs.json",
)

WORKERS = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_WORKERS",
            "5",
        )
    ),
    1,
)

TIMEOUT = max(
    float(
        os.getenv(
            "DELIVERY_VALIDATION_TIMEOUT",
            "8",
        )
    ),
    1.0,
)

STARTUP_TIMEOUT = max(
    float(
        os.getenv(
            "DELIVERY_VALIDATION_STARTUP_TIMEOUT",
            "4",
        )
    ),
    1.0,
)

RETEST_DELAY = max(
    float(
        os.getenv(
            "DELIVERY_VALIDATION_RETEST_DELAY",
            "5",
        )
    ),
    0.0,
)

MIN_SUCCESSFUL_ENDPOINTS = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_MIN_SUCCESS",
            "2",
        )
    ),
    1,
)

REQUIRE_RETEST = (
    os.getenv(
        "DELIVERY_VALIDATION_REQUIRE_RETEST",
        "true",
    ).strip().lower()
    == "true"
)

MIN_HOT = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_MIN_HOT",
            os.getenv(
                "PUBLIC_HOT_LIMIT",
                "5",
            ),
        )
    ),
    1,
)

FINAL_RESERVE_LIMIT = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_FINAL_RESERVE_LIMIT",
            "5",
        )
    ),
    0,
)

# The public delivery policy is 5 Hot + 5 Reserve by default.
MIN_FINAL_RESERVE = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_MIN_RESERVE",
            "5",
        )
    ),
    0,
)

REQUIRE_EXIT_IP = (
    os.getenv(
        "DELIVERY_VALIDATION_REQUIRE_EXIT_IP",
        "true",
    ).strip().lower()
    == "true"
)

# Download is mandatory in the current project policy. This legacy
# environment switch is retained only for explicit diagnostics; setting
# it to false causes validation to fail rather than bypassing Download.
REQUIRE_SMALL_DOWNLOAD = (
    os.getenv(
        "DELIVERY_VALIDATION_REQUIRE_SMALL_DOWNLOAD",
        "true",
    ).strip().lower()
    == "true"
)

SMALL_DOWNLOAD_BYTES = max(
    int(
        os.getenv(
            "DELIVERY_VALIDATION_DOWNLOAD_BYTES",
            "1048576",
        )
    ),
    1,
)

OFFICIAL_MIN_DOWNLOAD_MBPS = 1.5

MIN_SMALL_DOWNLOAD_MBPS = max(
    float(
        os.getenv(
            "DELIVERY_VALIDATION_MIN_DOWNLOAD_MBPS",
            str(
                OFFICIAL_MIN_DOWNLOAD_MBPS
            ),
        )
    ),
    OFFICIAL_MIN_DOWNLOAD_MBPS,
)

SMALL_DOWNLOAD_URL = os.getenv(
    "DELIVERY_VALIDATION_DOWNLOAD_URL",
    (
        "https://speed.cloudflare.com/"
        f"__down?bytes={SMALL_DOWNLOAD_BYTES}"
    ),
)

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

ENDPOINTS = [
    {
        "name": "ipify",
        "url": (
            "https://api.ipify.org"
            "?format=json"
        ),
        "kind": "json_ip",
    },
    {
        "name": "icanhazip",
        "url": "https://icanhazip.com",
        "kind": "text_ip",
    },
    {
        "name": "ifconfig_me",
        "url": "https://ifconfig.me/ip",
        "kind": "text_ip",
    },
    {
        "name": "gstatic_204",
        "url": (
            "https://www.gstatic.com/"
            "generate_204"
        ),
        "kind": "204",
    },
    {
        "name": "cloudflare_204",
        "url": (
            "https://cp.cloudflare.com/"
            "generate_204"
        ),
        "kind": "204",
    },
]


def _new_session():
    session = requests.Session()

    # Do not inherit HTTP_PROXY/HTTPS_PROXY/NO_PROXY from the host.
    # Proxy validation must use only the explicitly supplied SOCKS proxy.
    session.trust_env = False

    return session


def _normalize_ip(value):
    """
    Return one canonical IP string or None.

    Some endpoints return comma-separated origins. The first valid IP is
    selected, but all reported values are checked separately by callers.
    """
    text = str(
        value or ""
    ).strip()

    if not text:
        return None

    candidates = []

    for line in text.splitlines():
        candidates.extend(
            part.strip()
            for part in line.split(",")
            if part.strip()
        )

    for candidate in candidates:
        try:
            return str(
                ipaddress.ip_address(
                    candidate
                )
            )

        except ValueError:
            continue

    return None


def _extract_ip_values(value):
    text = str(
        value or ""
    ).strip()

    if not text:
        return []

    normalized = []
    seen = set()

    for line in text.splitlines():
        for part in line.split(","):
            candidate = part.strip()

            if not candidate:
                continue

            try:
                ip = str(
                    ipaddress.ip_address(
                        candidate
                    )
                )

            except ValueError:
                continue

            if ip in seen:
                continue

            seen.add(ip)
            normalized.append(ip)

    return normalized


def _response_ip_values(
    response,
    kind,
):
    if kind == "json_ip":
        try:
            payload = response.json()

        except Exception:
            return []

        return _extract_ip_values(
            payload.get("ip")
            or payload.get("origin")
        )

    if kind == "text_ip":
        return _extract_ip_values(
            response.text
        )

    return []


def _cache_busted_url(url):
    parts = urlsplit(
        str(url)
    )
    query = dict(
        parse_qsl(
            parts.query,
            keep_blank_values=True,
        )
    )
    query["_cb"] = str(
        time.time_ns()
    )

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query),
            parts.fragment,
        )
    )


def get_direct_ip():
    """
    Detect the host's direct public IP without honoring proxy environment
    variables. The value is used in memory for leak detection and is not
    persisted in the public payload.
    """
    endpoints = [
        {
            "url": (
                "https://api.ipify.org"
                "?format=json"
            ),
            "kind": "json_ip",
        },
        {
            "url": (
                "https://icanhazip.com"
            ),
            "kind": "text_ip",
        },
        {
            "url": (
                "https://ifconfig.me/ip"
            ),
            "kind": "text_ip",
        },
    ]

    with _new_session() as session:
        for endpoint in endpoints:
            try:
                with session.get(
                    endpoint["url"],
                    timeout=TIMEOUT,
                    headers=REQUEST_HEADERS,
                    allow_redirects=False,
                ) as response:
                    if (
                        response.status_code
                        != 200
                    ):
                        continue

                    values = (
                        _response_ip_values(
                            response,
                            endpoint["kind"],
                        )
                    )

                    if values:
                        return values[0]

            except Exception:
                continue

    return None


def wait_for_port(
    port,
    timeout=STARTUP_TIMEOUT,
):
    port = int(port)
    timeout = max(
        float(timeout),
        0.1,
    )
    deadline = (
        time.monotonic()
        + timeout
    )

    while (
        time.monotonic()
        < deadline
    ):
        try:
            with socket.create_connection(
                ("127.0.0.1", port),
                timeout=0.3,
            ):
                return True

        except OSError:
            time.sleep(0.15)

    return False


def validate_endpoint_response(
    endpoint,
    response,
):
    kind = endpoint["kind"]

    if kind == "204":
        return (
            response.status_code
            == 204,
            [],
        )

    if kind in {
        "json_ip",
        "text_ip",
    }:
        if response.status_code != 200:
            return False, []

        values = _response_ip_values(
            response,
            kind,
        )

        return bool(values), values

    return False, []


def build_proxies(socks_port):
    socks_port = int(socks_port)

    if socks_port <= 0:
        raise ValueError(
            "socks_port must be greater "
            "than zero"
        )

    proxy_url = (
        "socks5h://127.0.0.1:"
        f"{socks_port}"
    )

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def run_endpoint_checks(
    socks_port,
    direct_ip=None,
):
    proxies = build_proxies(
        socks_port
    )
    direct_ip = _normalize_ip(
        direct_ip
    )

    passed = 0
    reported_ips = []
    errors = []

    with _new_session() as session:
        for endpoint in ENDPOINTS:
            try:
                with session.get(
                    endpoint["url"],
                    proxies=proxies,
                    timeout=TIMEOUT,
                    allow_redirects=False,
                    headers=REQUEST_HEADERS,
                ) as response:
                    ok, ip_values = (
                        validate_endpoint_response(
                            endpoint,
                            response,
                        )
                    )

                    if not ok:
                        errors.append(
                            f"{endpoint['name']}:"
                            "bad_response:"
                            f"{response.status_code}"
                        )
                        continue

                    passed += 1
                    reported_ips.extend(
                        ip_values
                    )

            except Exception as exception:
                errors.append(
                    f"{endpoint['name']}:"
                    f"{repr(exception)}"
                )

    unique_ips = list(
        dict.fromkeys(
            reported_ips
        )
    )

    exit_ip = (
        Counter(
            reported_ips
        ).most_common(1)[0][0]
        if reported_ips
        else None
    )

    base = {
        "passed": passed,
        "exit_ip": exit_ip,
        "exit_ips": unique_ips,
        "warnings": errors,
    }

    if (
        passed
        < MIN_SUCCESSFUL_ENDPOINTS
    ):
        return {
            **base,
            "ok": False,
            "error": (
                "not_enough_successful_"
                "endpoints:"
                f"{passed}<"
                f"{MIN_SUCCESSFUL_ENDPOINTS}"
                + (
                    "; "
                    + "; ".join(errors)
                    if errors
                    else ""
                )
            ),
        }

    if (
        REQUIRE_EXIT_IP
        and not exit_ip
    ):
        return {
            **base,
            "ok": False,
            "error": "missing_exit_ip",
        }

    if (
        direct_ip
        and direct_ip in unique_ips
    ):
        return {
            **base,
            "ok": False,
            "error": (
                "exit_ip_equals_direct_ip"
            ),
        }

    return {
        **base,
        "ok": True,
        "error": None,
    }


def run_small_download(
    socks_port,
):
    if not REQUIRE_SMALL_DOWNLOAD:
        return {
            "ok": False,
            "bytes": 0,
            "mbps": None,
            "error": (
                "small_download_disabled_"
                "but_required"
            ),
        }

    proxies = build_proxies(
        socks_port
    )
    started = time.perf_counter()
    total_bytes = 0

    try:
        with _new_session() as session:
            with session.get(
                _cache_busted_url(
                    SMALL_DOWNLOAD_URL
                ),
                proxies=proxies,
                timeout=(
                    min(TIMEOUT, 5.0),
                    max(TIMEOUT, 15.0),
                ),
                stream=True,
                allow_redirects=False,
                headers=REQUEST_HEADERS,
            ) as response:
                if (
                    response.status_code
                    != 200
                ):
                    return {
                        "ok": False,
                        "bytes": 0,
                        "mbps": None,
                        "error": (
                            "small_download_bad_"
                            "status:"
                            f"{response.status_code}"
                        ),
                    }

                for chunk in (
                    response.iter_content(
                        chunk_size=(
                            64 * 1024
                        )
                    )
                ):
                    if not chunk:
                        continue

                    total_bytes += len(
                        chunk
                    )

                    if (
                        total_bytes
                        >= SMALL_DOWNLOAD_BYTES
                    ):
                        break

        elapsed = max(
            time.perf_counter()
            - started,
            0.001,
        )

        mbps = (
            total_bytes * 8
        ) / elapsed / 1_000_000
        rounded_mbps = round(
            mbps,
            2,
        )

        if (
            total_bytes
            < SMALL_DOWNLOAD_BYTES
        ):
            return {
                "ok": False,
                "bytes": total_bytes,
                "mbps": rounded_mbps,
                "error": (
                    "small_download_"
                    "incomplete:"
                    f"{total_bytes}<"
                    f"{SMALL_DOWNLOAD_BYTES}"
                ),
            }

        if (
            mbps
            < MIN_SMALL_DOWNLOAD_MBPS
        ):
            return {
                "ok": False,
                "bytes": total_bytes,
                "mbps": rounded_mbps,
                "error": (
                    "small_download_too_"
                    "slow:"
                    f"{rounded_mbps}<"
                    f"{MIN_SMALL_DOWNLOAD_MBPS}"
                ),
            }

        return {
            "ok": True,
            "bytes": total_bytes,
            "mbps": rounded_mbps,
            "error": None,
        }

    except Exception as exception:
        return {
            "ok": False,
            "bytes": total_bytes,
            "mbps": None,
            "error": (
                "small_download_error:"
                f"{repr(exception)}"
            ),
        }


def run_validation_round(
    socks_port,
    direct_ip=None,
):
    endpoint_result = (
        run_endpoint_checks(
            socks_port,
            direct_ip=direct_ip,
        )
    )

    if (
        endpoint_result.get("ok")
        is not True
    ):
        return {
            "ok": False,
            "stage": "endpoints",
            "passed": (
                endpoint_result.get(
                    "passed"
                )
            ),
            "exit_ip": (
                endpoint_result.get(
                    "exit_ip"
                )
            ),
            "exit_ips": (
                endpoint_result.get(
                    "exit_ips"
                )
            ),
            "download_mbps": None,
            "error": (
                endpoint_result.get(
                    "error"
                )
            ),
            "warnings": (
                endpoint_result.get(
                    "warnings"
                )
            ),
        }

    download_result = (
        run_small_download(
            socks_port
        )
    )

    if (
        download_result.get("ok")
        is not True
    ):
        return {
            "ok": False,
            "stage": "small_download",
            "passed": (
                endpoint_result.get(
                    "passed"
                )
            ),
            "exit_ip": (
                endpoint_result.get(
                    "exit_ip"
                )
            ),
            "exit_ips": (
                endpoint_result.get(
                    "exit_ips"
                )
            ),
            "download_mbps": (
                download_result.get(
                    "mbps"
                )
            ),
            "error": (
                download_result.get(
                    "error"
                )
            ),
            "warnings": (
                endpoint_result.get(
                    "warnings"
                )
            ),
        }

    return {
        "ok": True,
        "stage": "complete",
        "passed": (
            endpoint_result.get(
                "passed"
            )
        ),
        "exit_ip": (
            endpoint_result.get(
                "exit_ip"
            )
        ),
        "exit_ips": (
            endpoint_result.get(
                "exit_ips"
            )
        ),
        "download_mbps": (
            download_result.get(
                "mbps"
            )
        ),
        "error": None,
        "warnings": (
            endpoint_result.get(
                "warnings"
            )
        ),
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
            "[DELIVERY CLEANUP ERROR] "
            f"PATH={temp_path} "
            f"ERROR={repr(exception)}"
        )


def _validation_failure(
    stage,
    error,
    **values,
):
    return {
        "ok": False,
        "stage": stage,
        "error": str(
            error
            or "validation_failed"
        ),
        "passed": values.get(
            "passed"
        ),
        "exit_ip": values.get(
            "exit_ip"
        ),
        "exit_ips": values.get(
            "exit_ips",
            [],
        ),
        "download_mbps": values.get(
            "download_mbps"
        ),
        "warnings": values.get(
            "warnings",
            [],
        ),
    }


def validate_config_text(
    config_text,
    direct_ip=None,
):
    """
    Validate one config with one Xray process:

        Parse -> Build -> Start -> Endpoints -> Download -> Retest

    Download is mandatory and cannot pass below 1.5 Mbps.
    """
    config_text = str(
        config_text or ""
    ).strip()

    if not config_text:
        return _validation_failure(
            "input",
            "invalid config: empty_config_text",
        )

    xray_process = None
    temp_path = None

    try:
        try:
            parsed_config = (
                parse_config(
                    config_text
                )
            )

        except Exception as exception:
            return _validation_failure(
                "parse",
                "parse error: "
                f"{repr(exception)}",
            )

        socks_port = get_free_port()

        try:
            xray_json = (
                build_xray_config(
                    parsed_config,
                    socks_port,
                )
            )

        except Exception as exception:
            return _validation_failure(
                "build",
                "builder error: "
                f"{repr(exception)}",
            )

        try:
            temp_path = (
                save_temp_config(
                    xray_json,
                    socks_port,
                )
            )

        except Exception as exception:
            return _validation_failure(
                "temp_config",
                "temp config error: "
                f"{repr(exception)}",
            )

        if temp_path is None:
            return _validation_failure(
                "temp_config",
                (
                    "temp config error: "
                    "save_temp_config "
                    "returned None"
                ),
            )

        try:
            (
                xray_process,
                xray_alive,
                xray_error,
            ) = start_xray(
                temp_path
            )

        except Exception as exception:
            return _validation_failure(
                "xray",
                "XRAY_FAILED: "
                f"{repr(exception)}",
            )

        if not xray_alive:
            return _validation_failure(
                "xray",
                "XRAY_FAILED: "
                f"{xray_error or 'unknown error'}",
            )

        if not wait_for_port(
            socks_port
        ):
            return _validation_failure(
                "startup",
                "socks_port_not_ready",
            )

        first = run_validation_round(
            socks_port,
            direct_ip=direct_ip,
        )

        if first.get("ok") is not True:
            return _validation_failure(
                first.get(
                    "stage",
                    "first_check",
                ),
                "first_check_failed: "
                f"{first.get('error')}",
                passed=first.get(
                    "passed"
                ),
                exit_ip=first.get(
                    "exit_ip"
                ),
                exit_ips=first.get(
                    "exit_ips"
                ),
                download_mbps=first.get(
                    "download_mbps"
                ),
                warnings=first.get(
                    "warnings"
                ),
            )

        rounds = [first]

        if REQUIRE_RETEST:
            if RETEST_DELAY > 0:
                time.sleep(
                    RETEST_DELAY
                )

            second = (
                run_validation_round(
                    socks_port,
                    direct_ip=direct_ip,
                )
            )

            if (
                second.get("ok")
                is not True
            ):
                return _validation_failure(
                    second.get(
                        "stage",
                        "retest",
                    ),
                    "retest_failed: "
                    f"{second.get('error')}",
                    passed=second.get(
                        "passed"
                    ),
                    exit_ip=second.get(
                        "exit_ip"
                    ),
                    exit_ips=second.get(
                        "exit_ips"
                    ),
                    download_mbps=(
                        second.get(
                            "download_mbps"
                        )
                    ),
                    warnings=second.get(
                        "warnings"
                    ),
                )

            rounds.append(
                second
            )

        downloads = [
            float(
                item["download_mbps"]
            )
            for item in rounds
            if (
                item.get(
                    "download_mbps"
                )
                is not None
            )
        ]

        exit_ips = []

        for item in rounds:
            for value in (
                item.get("exit_ips")
                or []
            ):
                if value not in exit_ips:
                    exit_ips.append(
                        value
                    )

        selected_exit_ip = (
            Counter(
                item.get("exit_ip")
                for item in rounds
                if item.get("exit_ip")
            ).most_common(1)[0][0]
            if any(
                item.get("exit_ip")
                for item in rounds
            )
            else None
        )

        if not downloads:
            return _validation_failure(
                "small_download",
                "missing_download_measurement",
                exit_ip=selected_exit_ip,
                exit_ips=exit_ips,
            )

        # Conservative value: every successful internal round has passed
        # the threshold, and the minimum is persisted for Gate/Health.
        download_mbps = round(
            min(downloads),
            2,
        )

        return {
            "ok": True,
            "stage": "complete",
            "error": None,
            "passed": min(
                int(
                    item.get("passed")
                    or 0
                )
                for item in rounds
            ),
            "exit_ip": (
                selected_exit_ip
            ),
            "exit_ips": exit_ips,
            "download_mbps": (
                download_mbps
            ),
            "validation_rounds": len(
                rounds
            ),
            "warnings": [
                warning
                for item in rounds
                for warning in (
                    item.get("warnings")
                    or []
                )
            ],
        }

    except Exception as exception:
        return _validation_failure(
            "exception",
            repr(exception),
        )

    finally:
        _stop_xray_process(
            xray_process
        )
        _cleanup_temp_config(
            temp_path
        )


def get_config_text(item):
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        return str(
            item.get("config_text")
            or ""
        ).strip()

    return ""


def item_key(item):
    """
    Return a non-sensitive stable identifier for logs.
    """
    if isinstance(item, dict):
        config_id = item.get(
            "config_id"
        )

        if config_id is not None:
            return (
                "config_id="
                f"{config_id}"
            )

    config_text = get_config_text(
        item
    )

    if not config_text:
        return "empty"

    digest = hashlib.sha256(
        config_text.encode(
            "utf-8"
        )
    ).hexdigest()[:12]

    return f"sha256={digest}"


def validate_items(
    items,
    section_name,
    direct_ip=None,
):
    items = list(
        items or []
    )
    outcomes = [
        None
    ] * len(items)

    print()
    print("=" * 70)
    print(
        "DELIVERY VALIDATION "
        "CANDIDATES:",
        section_name,
    )
    print("TOTAL:", len(items))
    print("WORKERS:", WORKERS)
    print("=" * 70)

    with ThreadPoolExecutor(
        max_workers=WORKERS
    ) as executor:
        future_map = {}

        for index, item in enumerate(
            items
        ):
            config_text = (
                get_config_text(
                    item
                )
            )

            if not config_text:
                outcomes[index] = {
                    "item": item,
                    "result": (
                        _validation_failure(
                            "input",
                            "empty_config_text",
                        )
                    ),
                }
                continue

            future = executor.submit(
                validate_config_text,
                config_text,
                direct_ip,
            )

            future_map[future] = (
                index,
                item,
            )

        for future in as_completed(
            future_map
        ):
            index, item = (
                future_map[future]
            )

            try:
                result = future.result()

            except Exception as exception:
                result = (
                    _validation_failure(
                        "worker_exception",
                        repr(exception),
                    )
                )

            outcomes[index] = {
                "item": item,
                "result": result,
            }

    accepted = []
    accepted_results = []
    rejected = []

    # Candidate order, not thread completion order, remains authoritative.
    for outcome in outcomes:
        if outcome is None:
            continue

        item = outcome["item"]
        result = dict(
            outcome["result"]
            or {}
        )
        key = item_key(
            item
        )

        if result.get("ok") is True:
            accepted.append(
                item
            )
            accepted_results.append(
                result
            )

            print(
                f"[PASS] {section_name} "
                f"{key} "
                "passed="
                f"{result.get('passed')} "
                "exit_ip_present="
                f"{bool(result.get('exit_ip'))} "
                "download="
                f"{result.get('download_mbps')}"
            )

        else:
            rejected.append(
                {
                    "item": item,
                    "stage": (
                        result.get(
                            "stage"
                        )
                    ),
                    "error": (
                        result.get(
                            "error"
                        )
                    ),
                }
            )

            print(
                f"[FAIL] {section_name} "
                f"{key} "
                "stage="
                f"{result.get('stage')} "
                "error="
                f"{result.get('error')}"
            )

    print()
    print(
        f"{section_name} "
        "CANDIDATES ACCEPTED:",
        len(accepted),
    )
    print(
        f"{section_name} "
        "CANDIDATES REJECTED:",
        len(rejected),
    )

    return (
        accepted,
        rejected,
        accepted_results,
    )


def deduplicate_items(
    items,
    seen_texts=None,
):
    seen = set(
        seen_texts or ()
    )
    unique = []

    for item in items:
        config_text = (
            get_config_text(
                item
            )
        )

        if not config_text:
            continue

        if config_text in seen:
            continue

        seen.add(
            config_text
        )
        unique.append(
            item
        )

    return unique, seen


def load_payload(path):
    path = Path(path)

    with open(
        path,
        "r",
        encoding="utf-8-sig",
    ) as file:
        payload = json.load(
            file
        )

    if not isinstance(
        payload,
        dict,
    ):
        raise ValueError(
            "public payload must be "
            "a JSON object"
        )

    return payload


def save_payload(
    path,
    payload,
):
    path = Path(path)
    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=(
                f".{path.name}."
                "delivery_tmp_"
            ),
            suffix=".json",
            delete=False,
        ) as file:
            temp_path = Path(
                file.name
            )

            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.flush()
            os.fsync(
                file.fileno()
            )

        os.replace(
            temp_path,
            path,
        )
        temp_path = None

    finally:
        if (
            temp_path is not None
            and temp_path.exists()
        ):
            try:
                temp_path.unlink()

            except OSError:
                pass


@contextmanager
def payload_file_lock(path):
    """
    Prevent concurrent final validation and replacement of one payload.
    """
    path = Path(path)
    lock_path = path.with_name(
        f".{path.name}.delivery.lock"
    )

    lock_file = open(
        lock_path,
        "a+b",
    )
    lock_acquired = False

    try:
        lock_file.seek(0, 2)

        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()

        lock_file.seek(0)

        try:
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )

        except ImportError:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX
                | fcntl.LOCK_NB,
            )

        except OSError as exception:
            raise RuntimeError(
                "Another delivery validator "
                "is already processing "
                f"{path}"
            ) from exception

        lock_acquired = True
        yield lock_path

    finally:
        if lock_acquired:
            try:
                lock_file.seek(0)

                try:
                    import msvcrt

                    msvcrt.locking(
                        lock_file.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )

                except ImportError:
                    import fcntl

                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )

                except OSError:
                    pass

            finally:
                lock_file.close()

        else:
            lock_file.close()


def build_final_payload(
    payload,
    hot_candidates,
    reserve_candidates,
    direct_ip=None,
):
    (
        accepted_hot_candidates,
        rejected_hot,
        accepted_hot_results,
    ) = validate_items(
        hot_candidates,
        "hot",
        direct_ip=direct_ip,
    )

    (
        accepted_reserve_candidates,
        rejected_reserve,
        accepted_reserve_results,
    ) = validate_items(
        reserve_candidates,
        "reserve",
        direct_ip=direct_ip,
    )

    accepted_hot_candidates, seen = (
        deduplicate_items(
            accepted_hot_candidates
        )
    )

    (
        accepted_reserve_candidates,
        _,
    ) = deduplicate_items(
        accepted_reserve_candidates,
        seen_texts=seen,
    )

    # Original Hot order remains authoritative. Reserve fills Hot shortages.
    final_hot = (
        accepted_hot_candidates[
            :MIN_HOT
        ]
    )

    needed_from_reserve = max(
        MIN_HOT
        - len(final_hot),
        0,
    )

    promoted_from_reserve = (
        accepted_reserve_candidates[
            :needed_from_reserve
        ]
    )

    final_hot = (
        final_hot
        + promoted_from_reserve
    )

    if len(final_hot) < MIN_HOT:
        raise RuntimeError(
            "delivery validation failed: "
            f"final hot {len(final_hot)} "
            f"< required {MIN_HOT}"
        )

    hot_extras = (
        accepted_hot_candidates[
            MIN_HOT:
        ]
    )

    remaining_reserve = (
        accepted_reserve_candidates[
            needed_from_reserve:
        ]
    )

    final_reserve_candidates = (
        hot_extras
        + remaining_reserve
    )

    final_hot_texts = {
        get_config_text(item)
        for item in final_hot
        if get_config_text(item)
    }

    (
        final_reserve_candidates,
        _,
    ) = deduplicate_items(
        final_reserve_candidates,
        seen_texts=(
            final_hot_texts
        ),
    )

    final_reserve = (
        final_reserve_candidates[
            :FINAL_RESERVE_LIMIT
        ]
    )

    if (
        len(final_reserve)
        < MIN_FINAL_RESERVE
    ):
        raise RuntimeError(
            "delivery validation failed: "
            f"final reserve "
            f"{len(final_reserve)} "
            f"< required "
            f"{MIN_FINAL_RESERVE}"
        )

    final_configs = (
        final_hot
        + final_reserve
    )

    payload = dict(
        payload
    )
    payload["hot"] = final_hot
    payload["reserve"] = (
        final_reserve
    )

    # Public subscription output: 5 Hot + 5 Reserve by default.
    payload["configs"] = (
        final_configs
    )
    payload["hot_count"] = len(
        final_hot
    )
    payload["reserve_count"] = len(
        final_reserve
    )
    payload["config_count"] = len(
        final_configs
    )
    payload["delivery_strategy"] = (
        "validated_hot_plus_reserve"
    )

    payload["delivery_validation"] = {
        "enabled": True,
        "validated_at": (
            time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(),
            )
        ),
        # Do not persist the host's direct IP.
        "direct_ip_detected": bool(
            direct_ip
        ),
        "final_hot_target": MIN_HOT,
        "final_reserve_limit": (
            FINAL_RESERVE_LIMIT
        ),
        "min_final_reserve": (
            MIN_FINAL_RESERVE
        ),
        "final_config_target": (
            MIN_HOT
            + FINAL_RESERVE_LIMIT
        ),
        "candidate_hot_total": len(
            hot_candidates
        ),
        "candidate_reserve_total": (
            len(
                reserve_candidates
            )
        ),
        "candidate_hot_accepted": (
            len(
                accepted_hot_candidates
            )
        ),
        "candidate_hot_rejected": (
            len(rejected_hot)
        ),
        "candidate_reserve_accepted": (
            len(
                accepted_reserve_candidates
            )
        ),
        "candidate_reserve_rejected": (
            len(rejected_reserve)
        ),
        "promoted_from_reserve": (
            len(
                promoted_from_reserve
            )
        ),
        "hot_extras_moved_to_reserve": (
            len(hot_extras)
        ),
        "final_hot": len(
            final_hot
        ),
        "final_reserve": len(
            final_reserve
        ),
        "final_configs": len(
            final_configs
        ),
        "min_successful_endpoints": (
            MIN_SUCCESSFUL_ENDPOINTS
        ),
        "require_retest": (
            REQUIRE_RETEST
        ),
        "download_bytes": (
            SMALL_DOWNLOAD_BYTES
        ),
        "min_download_mbps": (
            MIN_SMALL_DOWNLOAD_MBPS
        ),
        "accepted_hot_result_count": (
            len(
                accepted_hot_results
            )
        ),
        "accepted_reserve_result_count": (
            len(
                accepted_reserve_results
            )
        ),
    }

    return payload


def main():
    path = Path(
        sys.argv[1]
        if len(sys.argv) > 1
        else DEFAULT_FILE
    )

    if not path.exists():
        raise FileNotFoundError(
            path
        )

    if (
        MIN_FINAL_RESERVE
        > FINAL_RESERVE_LIMIT
    ):
        raise ValueError(
            "DELIVERY_VALIDATION_MIN_RESERVE "
            "cannot exceed "
            "DELIVERY_VALIDATION_"
            "FINAL_RESERVE_LIMIT"
        )

    print("=" * 70)
    print(
        "DELIVERY CANDIDATE "
        "VALIDATION STARTED"
    )
    print("FILE:", path)
    print(
        "FINAL HOT TARGET:",
        MIN_HOT,
    )
    print(
        "FINAL RESERVE LIMIT:",
        FINAL_RESERVE_LIMIT,
    )
    print(
        "MIN FINAL RESERVE:",
        MIN_FINAL_RESERVE,
    )
    print(
        "FINAL CONFIG TARGET:",
        MIN_HOT
        + FINAL_RESERVE_LIMIT,
    )
    print(
        "MIN SUCCESSFUL ENDPOINTS:",
        MIN_SUCCESSFUL_ENDPOINTS,
    )
    print(
        "REQUIRE RETEST:",
        REQUIRE_RETEST,
    )
    print(
        "MIN DOWNLOAD MBPS:",
        MIN_SMALL_DOWNLOAD_MBPS,
    )
    print("=" * 70)

    with payload_file_lock(
        path
    ) as lock_path:
        print(
            "LOCK ACQUIRED:",
            lock_path,
        )

        payload = load_payload(
            path
        )

        direct_ip = get_direct_ip()

        print(
            "DIRECT IP DETECTED:",
            bool(direct_ip),
        )

        hot_candidates = (
            payload.get(
                "hot_candidates"
            )
            if payload.get(
                "hot_candidates"
            )
            is not None
            else payload.get("hot")
        ) or []

        reserve_candidates = (
            payload.get(
                "reserve_candidates"
            )
            if payload.get(
                "reserve_candidates"
            )
            is not None
            else payload.get(
                "reserve"
            )
        ) or []

        print()
        print("=" * 70)
        print(
            "INPUT CANDIDATE POOL"
        )
        print(
            "HOT CANDIDATES:",
            len(hot_candidates),
        )
        print(
            "RESERVE CANDIDATES:",
            len(
                reserve_candidates
            ),
        )
        print(
            "TOTAL CANDIDATES:",
            len(hot_candidates)
            + len(
                reserve_candidates
            ),
        )
        print("=" * 70)

        final_payload = (
            build_final_payload(
                payload=payload,
                hot_candidates=(
                    hot_candidates
                ),
                reserve_candidates=(
                    reserve_candidates
                ),
                direct_ip=direct_ip,
            )
        )

        save_payload(
            path,
            final_payload,
        )

    print()
    print("=" * 70)
    print(
        "DELIVERY CANDIDATE "
        "VALIDATION FINISHED "
        "SUCCESSFULLY"
    )
    print(
        "FINAL HOT:",
        final_payload[
            "hot_count"
        ],
    )
    print(
        "FINAL RESERVE:",
        final_payload[
            "reserve_count"
        ],
    )
    print(
        "FINAL CONFIGS:",
        final_payload[
            "config_count"
        ],
    )
    print("=" * 70)


if __name__ == "__main__":
    main()
