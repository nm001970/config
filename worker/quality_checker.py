import time
import requests

PING_URLS= [
    "https://api.ipify.org?format=json",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
]

DOWNLOAD_URL = "https://speed.cloudflare.com/__down?bytes=1048576"
UPLOAD_URL= "https://httpbin.org/post"

def _make_proxies(socks_port):
    proxy_url= f"socks5h://127.0.0.1:{socks_port}"

    return {
        "http": proxy_url,
        "https": proxy_url
    }

def test_connectivity(socks_port):
    proxies= _make_proxies(socks_port)

    last_error= None

    for url in PING_URLS:
        try:
            response = requests.get(
                url,
                proxies= proxies,
                timeout=(3,8),
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            if response.status_code ==200:
                return{
                    "alive": True,
                    "ip": response.text.strip(),
                    "error": None
                }
            
            last_error= f"{url} HTTP {response.status_code}"

        except Exception as e:
            last_error= f"{url} -> {repr(e)}"
    
    return{
        "alive": False,
        "ip": None,
        "error": last_error
    }

def test_http_ping(socks_port, attempts=3):
    proxise= _make_proxies(socks_port)

    latencies= []
    last_error= None

    for _ in range(attempts):
        try:
            start= time.perf_counter()

            response= requests.get(
                PING_URLS[0],
                proxies= proxise,
                timeout=(3, 8),
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            elapsed_ms = (
                time.perf_counter() - start
            )* 1000

            if response.status_code == 200:
                latencies.append(elapsed_ms)
            else:
                last_error= f"HTTP {response.status_code}"

        except Exception as e:
            last_error= repr(e)

    if not latencies:
        return{
            "ok": False,
            "min_ms": None,
            "avg_ms": None,
            "error": last_error
        }
    return {
        "ok": True,
        "min_ms": round(min(latencies), 2),
        "avg_ms": round(sum(latencies) / len(latencies), 2),
        "error": None
    }

def test_download_speed(socks_port):
    print("[DOWNLOAD TEST START]")

    proxies= _make_proxies(socks_port)

    try:
        start= time.perf_counter()

        response= requests.get(
            DOWNLOAD_URL,
            proxies=proxies,
            timeout=(3, 15),
            stream=True,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        downloaded= 0

        for chunk in response.iter_content(chunk_size= 64 * 1024):
            if chunk:
                downloaded += len(chunk)

        elapsed = time.perf_counter() - start

        if response.status_code != 200:
            print("[DOWNLOAD HTTP ERROR]", response.status_code)
            return{
                "ok":False,
                "mbps": None,
                "bytes": downloaded,
                "error": f"HTTP {response.status_code}"
            }
        
        if elapsed <= 0 or downloaded <= 0:
            print("DOWNLOAD EMPTY", downloaded, elapsed)
            return{
                "ok": False,
                "mbps": None,
                "bytes": downloaded,
                "error": "empty download"
            }
        
        mbps= (
            downloaded *8
        ) / elapsed / 1_000_000

        return {
            "ok": True,
            "mbps": round(mbps, 2),
            "bytes": downloaded,
            "error": None
        }

    except Exception as e:
        print("[DOWNLOAD ERROR]", repr(e))
        return{
            "ok":False,
            "mbps": None,
            "bytes": 0,
            "error": repr(e)
        }
    
def test_upload_speed(socks_port):
    print("[UPLOAD TEST START]")

    proxies= _make_proxies(socks_port)

    payload = b"x" * (256 * 1024)

    try:
        start= time.perf_counter()

        response= requests.post(
            UPLOAD_URL,
            data= payload,
            proxies= proxies,
            timeout=(3, 15),
            headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/octet-stream"
            }
        )

        elapsed= time.perf_counter() - start
        if elapsed <= 0:
            return {
                "ok": False,
                "mbps": None,
                "bytes": len(payload),
                "error": "invalid elapsed"
            }
        if response.status_code not in (200, 201):
            return{
                "ok": False,
                "mbps": None,
                "bytes": len(payload),
                "error": f"HTTP {response.status_code}"
            }
        
        mbps= (
            len(payload)*8
        )/ elapsed / 1_000_000

        return{
            "ok": True,
            "mbps": round(mbps, 2),
            "bytes": len(payload),
            "error": None
        }
    
    except Exception as e:
        return{
            "ok": False,
            "mbps": None,
            "bytes": len(payload),
            "error": repr(e)
        }
    

def test_proxy_quality(socks_port):

    ping = test_http_ping(socks_port, attempts=2)

    if not ping["ok"]:
        return{
            "alive": False,
            "ip": None,
            "ping_min_ms": None,
            "ping_avg_ms":None,
            "download_mbps": None,
            "upload_mbps": None,
            "ping_error": ping["error"],
            "download_error": None,
            "upload_error":None,
            "error": ping["error"]
        }
    
    download= test_download_speed(socks_port)
    print("[DOWNLOAD RESULT:]", download)
    upload= test_upload_speed(socks_port)
    print("[UPLOAD RESULT:]", upload)

    return{
        "alive":True,
        "ip":None,

        "ping_min_ms": ping["min_ms"],
        "ping_avg_ms": ping["avg_ms"],

        "download_mbps": download["mbps"],
        "upload_mbps": upload["mbps"],

        "ping_error": ping["error"],
        "download_error": download["error"],
        "upload_error": upload["error"],

        "error": None
    }
        
