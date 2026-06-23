import time
import requests


TEST_URLS = [
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
]


def test_proxy(socks_port):
    """
    Stage 1 connectivity test.

    This function only checks whether the Xray SOCKS proxy works.
    It must not run download/upload quality tests.
    """

    proxy_url = f"socks5h://127.0.0.1:{socks_port}"

    proxies = {
        "http": proxy_url,
        "https": proxy_url
    }

    last_error = None

    for url in TEST_URLS:
        try:
            start = time.perf_counter()

            response = requests.get(
                url,
                proxies=proxies,
                timeout=(3, 8),
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            elapsed = time.perf_counter() - start
            latency_ms = round(elapsed * 1000, 2)

            if response.status_code != 200:
                last_error = f"{url} HTTP {response.status_code}"
                continue

            return {
                "alive": True,
                "latency": latency_ms,
                "ip": response.text.strip(),
                "error": None
            }

        except requests.exceptions.RequestException as e:
            last_error = f"{url} -> {repr(e)}"

        except Exception as e:
            last_error = f"{url} -> UNKNOWN ERROR: {repr(e)}"

    return {
        "alive": False,
        "latency": 0,
        "ip": None,
        "error": last_error
    }