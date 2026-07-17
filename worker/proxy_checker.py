import ipaddress
import time

import requests


PROBES = [
    {
        "name": "ipify",
        "url": "https://api.ipify.org?format=json",
        "status_code": 200,
        "require_ip": True,
    },
    {
        "name": "gstatic_204",
        "url": "http://www.gstatic.com/generate_204",
        "status_code": 204,
        "require_ip": False,
    },
]

REQUEST_TIMEOUT_SECONDS = 5

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64)"
    ),
}


def normalize_ip_response(response):
    """
    Extract and validate an IPv4 or IPv6 address.
    """
    candidate = None

    try:
        payload = response.json()

        if isinstance(payload, dict):
            candidate = (
                payload.get("ip")
                or payload.get("origin")
            )

    except ValueError:
        pass

    if candidate is None:
        text = str(
            response.text or ""
        ).strip()

        if text:
            candidate = text.splitlines()[0]

    if candidate is None:
        return None

    candidate = str(candidate).strip()

    # Some endpoints may return multiple IP addresses.
    candidate = candidate.split(",", 1)[0].strip()

    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None

    return candidate


def test_proxy(socks_port):
    """
    Fast stage-1 connectivity test.

    The first probe tries to obtain the exit IP.
    The second probe is a lightweight HTTP fallback.

    Detailed speed and quality tests belong to the
    quality-testing stage, not this function.
    """
    socks_port = int(socks_port)

    if socks_port <= 0:
        raise ValueError(
            "socks_port must be greater than zero"
        )

    proxy_url = (
        f"socks5h://127.0.0.1:{socks_port}"
    )

    proxies = {
        "http": proxy_url,
        "https": proxy_url,
    }

    errors = []

    for probe in PROBES:
        started_at = time.perf_counter()

        try:
            with requests.get(
                probe["url"],
                proxies=proxies,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers=REQUEST_HEADERS,
                allow_redirects=False,
            ) as response:
                latency_ms = max(
                    round(
                        (
                            time.perf_counter()
                            - started_at
                        )
                        * 1000,
                        2,
                    ),
                    0.01,
                )

                if (
                    response.status_code
                    != probe["status_code"]
                ):
                    errors.append(
                        f"{probe['name']}:"
                        f"HTTP {response.status_code}"
                    )
                    continue

                exit_ip = None

                if probe["require_ip"]:
                    exit_ip = normalize_ip_response(
                        response
                    )

                    if exit_ip is None:
                        errors.append(
                            f"{probe['name']}:"
                            "invalid_ip_response"
                        )
                        continue

                return {
                    "alive": True,
                    "latency": latency_ms,
                    "ip": exit_ip,
                    "error": None,
                }

        except requests.exceptions.RequestException as exc:
            errors.append(
                f"{probe['name']}:{repr(exc)}"
            )

        except Exception as exc:
            errors.append(
                f"{probe['name']}:"
                f"unknown_error:{repr(exc)}"
            )

    return {
        "alive": False,
        "latency": 0.0,
        "ip": None,
        "error": (
            "connectivity_failed | "
            + " | ".join(errors)
        ),
    }