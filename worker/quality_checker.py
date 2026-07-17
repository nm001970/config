import time

import requests


PING_URLS = [
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
]

DOWNLOAD_URL = (
    "https://speed.cloudflare.com/"
    "__down?bytes=1048576"
)

UPLOAD_URLS = [
    "https://httpbin.org/post",
    "https://httpbingo.org/post",
]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0",
}

PING_TIMEOUT = (3, 8)
DOWNLOAD_TIMEOUT = (3, 15)
UPLOAD_TIMEOUT = (3, 15)

DOWNLOAD_CHUNK_SIZE = 64 * 1024
UPLOAD_PAYLOAD_BYTES = 256 * 1024


def _make_proxies(socks_port):
    socks_port = int(socks_port)

    if socks_port <= 0:
        raise ValueError(
            "socks_port must be greater than zero"
        )

    proxy_url = (
        f"socks5h://127.0.0.1:{socks_port}"
    )

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def _to_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def _extract_ip(response):
    """
    Extract an exit IP from JSON or plain-text IP endpoints.
    """
    content_type = str(
        response.headers.get(
            "Content-Type",
            "",
        )
    ).lower()

    if "json" in content_type:
        try:
            payload = response.json()
            value = (
                payload.get("ip")
                or payload.get("origin")
            )

            if value:
                return str(value).strip()

        except Exception:
            pass

    text = str(
        response.text or ""
    ).strip()

    if not text:
        return None

    # api.ipify may return JSON even when a proxy strips the expected
    # content-type header.
    if (
        text.startswith("{")
        and text.endswith("}")
    ):
        try:
            payload = response.json()
            value = (
                payload.get("ip")
                or payload.get("origin")
            )

            if value:
                return str(value).strip()

        except Exception:
            pass

    return text.splitlines()[0].strip() or None


def _base_quality_result():
    return {
        "alive": False,
        "ip": None,
        "failed_stage": None,
        "ping_min_ms": None,
        "ping_avg_ms": None,
        "download_mbps": None,
        "upload_mbps": None,
        "ping_error": None,
        "download_error": None,
        "upload_error": None,
        "upload_verified": False,
        "error": None,
    }


def test_connectivity(socks_port):
    proxies = _make_proxies(
        socks_port
    )
    last_error = None

    for url in PING_URLS:
        try:
            with requests.get(
                url,
                proxies=proxies,
                timeout=PING_TIMEOUT,
                headers=REQUEST_HEADERS,
            ) as response:
                if response.status_code == 200:
                    return {
                        "alive": True,
                        "ip": _extract_ip(
                            response
                        ),
                        "error": None,
                    }

                last_error = (
                    f"{url} HTTP "
                    f"{response.status_code}"
                )

        except Exception as exception:
            last_error = (
                f"{url} -> "
                f"{repr(exception)}"
            )

    return {
        "alive": False,
        "ip": None,
        "error": (
            last_error
            or "connectivity failed"
        ),
    }


def test_http_ping(
    socks_port,
    attempts=2,
):
    proxies = _make_proxies(
        socks_port
    )
    attempts = max(
        int(attempts),
        1,
    )

    latencies = []
    errors = []
    exit_ip = None

    for url in PING_URLS:
        for _ in range(attempts):
            try:
                started_at = (
                    time.perf_counter()
                )

                with requests.get(
                    url,
                    proxies=proxies,
                    timeout=PING_TIMEOUT,
                    headers=REQUEST_HEADERS,
                ) as response:
                    elapsed_ms = (
                        time.perf_counter()
                        - started_at
                    ) * 1000

                    if response.status_code == 200:
                        latencies.append(
                            elapsed_ms
                        )

                        if exit_ip is None:
                            exit_ip = (
                                _extract_ip(
                                    response
                                )
                            )

                        # One successful attempt per endpoint is enough.
                        break

                    errors.append(
                        f"{url} HTTP "
                        f"{response.status_code}"
                    )

            except Exception as exception:
                errors.append(
                    f"{url} -> "
                    f"{repr(exception)}"
                )

    if not latencies:
        return {
            "ok": False,
            "ip": None,
            "min_ms": None,
            "avg_ms": None,
            "error": (
                " | ".join(
                    errors[-3:]
                )
                if errors
                else "ping failed"
            ),
        }

    return {
        "ok": True,
        "ip": exit_ip,
        "min_ms": round(
            min(latencies),
            2,
        ),
        "avg_ms": round(
            sum(latencies)
            / len(latencies),
            2,
        ),
        "error": None,
    }


def test_download_speed(
    socks_port,
):
    print("[DOWNLOAD TEST START]")

    proxies = _make_proxies(
        socks_port
    )

    try:
        started_at = (
            time.perf_counter()
        )

        with requests.get(
            DOWNLOAD_URL,
            proxies=proxies,
            timeout=DOWNLOAD_TIMEOUT,
            stream=True,
            headers=REQUEST_HEADERS,
        ) as response:
            if response.status_code != 200:
                print(
                    "[DOWNLOAD HTTP ERROR]",
                    response.status_code,
                )

                return {
                    "ok": False,
                    "mbps": None,
                    "bytes": 0,
                    "error": (
                        "HTTP "
                        f"{response.status_code}"
                    ),
                }

            downloaded = 0

            for chunk in response.iter_content(
                chunk_size=(
                    DOWNLOAD_CHUNK_SIZE
                )
            ):
                if chunk:
                    downloaded += len(
                        chunk
                    )

        elapsed = (
            time.perf_counter()
            - started_at
        )

        if (
            elapsed <= 0
            or downloaded <= 0
        ):
            print(
                "[DOWNLOAD EMPTY]",
                downloaded,
                elapsed,
            )

            return {
                "ok": False,
                "mbps": None,
                "bytes": downloaded,
                "error": "empty download",
            }

        mbps = (
            downloaded * 8
        ) / elapsed / 1_000_000

        return {
            "ok": True,
            "mbps": round(
                mbps,
                2,
            ),
            "bytes": downloaded,
            "error": None,
        }

    except Exception as exception:
        print(
            "[DOWNLOAD ERROR]",
            repr(exception),
        )

        return {
            "ok": False,
            "mbps": None,
            "bytes": 0,
            "error": repr(
                exception
            ),
        }


def test_upload_speed(
    socks_port,
):
    print("[UPLOAD TEST START]")

    proxies = _make_proxies(
        socks_port
    )
    payload = (
        b"x" * UPLOAD_PAYLOAD_BYTES
    )
    errors = []

    for url in UPLOAD_URLS:
        try:
            started_at = (
                time.perf_counter()
            )

            with requests.post(
                url,
                data=payload,
                proxies=proxies,
                timeout=UPLOAD_TIMEOUT,
                headers={
                    **REQUEST_HEADERS,
                    "Content-Type": (
                        "application/octet-stream"
                    ),
                },
            ) as response:
                elapsed = (
                    time.perf_counter()
                    - started_at
                )

                if response.status_code not in (
                    200,
                    201,
                    204,
                ):
                    errors.append(
                        f"{url}: HTTP "
                        f"{response.status_code}"
                    )
                    continue

            if elapsed <= 0:
                errors.append(
                    f"{url}: invalid elapsed"
                )
                continue

            mbps = (
                len(payload) * 8
            ) / elapsed / 1_000_000

            return {
                "ok": True,
                "mbps": round(
                    mbps,
                    2,
                ),
                "bytes": len(
                    payload
                ),
                "error": None,
                "endpoint": url,
            }

        except Exception as exception:
            errors.append(
                f"{url}: "
                f"{repr(exception)}"
            )

    return {
        "ok": False,
        "mbps": None,
        "bytes": len(
            payload
        ),
        "error": (
            " | ".join(errors)
            or "upload failed"
        ),
        "endpoint": None,
    }


def test_proxy_quality(
    socks_port,
    min_download_mbps=None,
):
    """
    Run Ping -> Download -> optional Upload with strict fail-fast.

    min_download_mbps:
        When provided, Upload is skipped if the measured Download is
        below this minimum. This keeps the policy in quality_worker.py
        while allowing this lower-level checker to stop early.
    """
    result = _base_quality_result()

    # Stage 1: HTTP Ping / connectivity.
    ping = test_http_ping(
        socks_port,
        attempts=2,
    )

    result["ip"] = ping.get(
        "ip"
    )
    result["ping_min_ms"] = (
        ping.get("min_ms")
    )
    result["ping_avg_ms"] = (
        ping.get("avg_ms")
    )
    result["ping_error"] = (
        ping.get("error")
    )

    if not ping.get("ok"):
        result["failed_stage"] = (
            "ping"
        )
        result["error"] = (
            ping.get("error")
            or "ping failed"
        )
        return result

    # Stage 2: mandatory Download.
    download = test_download_speed(
        socks_port
    )
    print(
        "[DOWNLOAD RESULT:]",
        download,
    )

    result["download_mbps"] = (
        _to_float(
            download.get("mbps")
        )
    )
    result["download_error"] = (
        download.get("error")
    )

    if not download.get("ok"):
        print(
            "[UPLOAD SKIPPED] "
            "REASON=download_failed"
        )

        result["failed_stage"] = (
            "download"
        )
        result["error"] = (
            download.get("error")
            or "download failed"
        )
        return result

    minimum = _to_float(
        min_download_mbps
    )

    if (
        minimum is not None
        and result["download_mbps"]
        < minimum
    ):
        print(
            "[UPLOAD SKIPPED] "
            "REASON=download_below_minimum "
            f"DOWNLOAD={result['download_mbps']} "
            f"MINIMUM={minimum}"
        )

        error = (
            "download_too_low:"
            f"{result['download_mbps']}"
        )

        result["failed_stage"] = (
            "download"
        )
        result["error"] = error
        result["download_error"] = (
            error
        )
        return result

    # Stage 3: optional Upload.
    upload = test_upload_speed(
        socks_port
    )
    print(
        "[UPLOAD RESULT:]",
        upload,
    )

    result["upload_mbps"] = (
        _to_float(
            upload.get("mbps")
        )
    )
    result["upload_error"] = (
        upload.get("error")
    )
    result["upload_verified"] = bool(
        upload.get("ok")
        and result["upload_mbps"]
        is not None
    )

    # Upload endpoint failure is monitoring data only.
    result["alive"] = True
    result["failed_stage"] = None
    result["error"] = None

    return result


__all__ = [
    "test_connectivity",
    "test_http_ping",
    "test_download_speed",
    "test_upload_speed",
    "test_proxy_quality",
]