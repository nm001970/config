import os
import json
import ipaddress
from datetime import datetime
from urllib.parse import urlsplit, parse_qs

from database.db import SessionLocal
from database.models import Config
from worker.config_sanitizer import sanitize_config_for_delivery
from worker.parsers.factory import parse_config



EXPORT_PATH = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")

HOT_LIMIT = int(os.getenv("PUBLIC_HOT_LIMIT", "5"))
RESERVE_LIMIT = int(os.getenv("PUBLIC_RESERVE_LIMIT", "40"))

# برای اینکه diversity خوب داشته باشیم، بیشتر از ۵۰تا candidate می‌خوانیم.
CANDIDATE_MULTIPLIER = int(os.getenv("PUBLIC_CANDIDATE_MULTIPLIER", "10"))

BLOCKED_EXPORT_MARKERS = [
    "trycloudflare",
    "127.0.0.1",
    "localhost",
]


def utc_now_text():
    return datetime.utcnow().isoformat() + "Z"


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default
    

def calculate_delivery_score(row):
    """
    امتیاز نهایی برای انتخاب خروجی public.

    quality_score همچنان پایه اصلی است،
    اما download/upload/latency/ping هم اثر دارند.
    """
    quality_score = safe_float(row.quality_score)
    latency = safe_float(row.latency, 9999)
    ping_avg = safe_float(row.ping_avg_ms, latency)
    download = safe_float(row.download_mbps)
    upload = safe_float(row.upload_mbps)

    score = quality_score

    # download مهم است، اما سقف‌دار تا عددهای غیرعادی غالب نشوند.
    score += min(download, 10.0) * 2.0

    # upload مهم است، ولی کمتر از download.
    score += min(upload, 3.0) * 3.0

    # latency
    if latency <= 400:
        score += 8
    elif latency <= 700:
        score += 5
    elif latency <= 1000:
        score += 2
    else:
        score -= 5

    # ping average
    if ping_avg <= 500:
        score += 5
    elif ping_avg <= 800:
        score += 2
    elif ping_avg > 1200:
        score -= 6

    # upload صفر یعنی کیفیت ناقص‌تر است.
    if upload <= 0:
        score -= 5

    # download خیلی پایین قابل قبول نیست.
    if download < 1:
        score -= 10

    return round(score, 2)


def normalize_host_family(host):
    """
    برای جلوگیری از ارسال چند کانفیگ از یک subnet/domain family.
    مثال:
      151.115.166.43 -> ip4:151.115.166.0/24
      sca21.myfymain.com -> domain:myfymain.com
    """
    if not host:
        return "host:unknown"

    host = host.strip().lower().strip("[]")

    try:
        ip = ipaddress.ip_address(host)

        if isinstance(ip, ipaddress.IPv4Address):
            parts = host.split(".")
            if len(parts) == 4:
                return f"ip4:{parts[0]}.{parts[1]}.{parts[2]}.0/24"
            return f"ip4:{host}"

        network = ipaddress.ip_network(f"{ip}/48", strict=False)
        return f"ip6:{network}"

    except ValueError:
        parts = [p for p in host.split(".") if p]
        if len(parts) >= 2:
            return "domain:" + ".".join(parts[-2:])
        return f"domain:{host}"


def get_first_param(params, key, default=""):
    value = params.get(key)
    if not value:
        return default
    return value[0] or default


def extract_fingerprint(config_text):
    """
    Fingerprint و metadata خروجی public.

    برای VLESS و VMess نباید فقط urlsplit استفاده کنیم.
    VMess به شکل vmess://base64-json است و urlsplit آن را اشتباه
    به‌عنوان host می‌خواند. بنابراین از parser اصلی پروژه استفاده می‌کنیم.
    """
    try:
        parsed_config = parse_config(config_text)

        protocol = (parsed_config.protocol or "").lower()
        server = (parsed_config.server or "").strip().lower()
        port = str(parsed_config.port or "")

        network_type = (
            parsed_config.transport.get("type", "")
            if parsed_config.transport
            else ""
        )
        network_type = (network_type or "").lower()

        mode = (
            parsed_config.transport.get("mode", "")
            if parsed_config.transport
            else ""
        )
        mode = (mode or "").lower()

        security = (
            parsed_config.tls.get("security", "none")
            if parsed_config.tls
            else "none"
        )
        security = (security or "none").lower()

        uuid = (
            parsed_config.auth.get("uuid", "")
            if parsed_config.auth
            else ""
        )
        uuid = (uuid or "").lower()

        encryption = (
            parsed_config.auth.get("encryption", "")
            if parsed_config.auth
            else ""
        )
        encryption = (encryption or "").lower()

        sni = (
            parsed_config.tls.get("sni", "")
            if parsed_config.tls
            else ""
        )
        sni = (sni or "").lower()

        host_header = (
            parsed_config.transport.get("host", "")
            if parsed_config.transport
            else ""
        )
        host_header = (host_header or "").lower()

        service_name = (
            parsed_config.transport.get("service_name", "")
            if parsed_config.transport
            else ""
        )
        service_name = (service_name or "").lower()

        path = (
            parsed_config.transport.get("path", "")
            if parsed_config.transport
            else ""
        )
        path = (path or "").lower()

        # metadata خراب نباید وارد public شود
        if not protocol or not server or not port:
            raise ValueError(
                f"Invalid parsed config metadata: "
                f"protocol={protocol}, server={server}, port={port}"
            )

        strong_family = "|".join(
            [
                protocol,
                uuid,
                port,
                network_type,
                mode,
                security,
                encryption,
                sni,
                host_header,
                service_name,
                path,
            ]
        )

        host_family = normalize_host_family(server)

        return {
            "protocol": protocol,
            "server": server,
            "port": port,
            "network_type": network_type,
            "security": security,
            "strong_family": strong_family,
            "host_family": host_family,
        }

    except Exception:
        return {
            "protocol": "unknown",
            "server": "unknown",
            "port": "",
            "network_type": "",
            "security": "",
            "strong_family": "unknown:" + str(abs(hash(config_text))),
            "host_family": "unknown",
        }


def is_export_safe(config_text):
    if not config_text:
        return False

    lowered = config_text.lower()
    for marker in BLOCKED_EXPORT_MARKERS:
        if marker in lowered:
            return False

    return True


def make_public_item(row, sanitized_text, fp, delivery_score):
    return {
        "config_text": sanitized_text,
        "source_id": row.id,
        "protocol": row.protocol,
        "quality_score": safe_float(row.quality_score),
        "delivery_score": safe_float(delivery_score),
        "latency": safe_float(row.latency),
        "ping_avg_ms": safe_float(row.ping_avg_ms),
        "download_mbps": safe_float(row.download_mbps),
        "upload_mbps": safe_float(row.upload_mbps),
        "server": fp["server"],
        "port": fp["port"],
        "network_type": fp["network_type"],
        "security": fp["security"],
    }


def load_candidates(session):
    query_limit = (HOT_LIMIT + RESERVE_LIMIT) * CANDIDATE_MULTIPLIER

    rows = (
        session.query(Config)
        .filter(
            Config.status == "alive",
            Config.quality_status == "qualified",
        )
        .order_by(
            Config.quality_score.desc(),
            Config.latency.asc(),
        )
        .limit(query_limit)
        .all()
    )

    candidates = []
    skipped = 0

    for row in rows:
        sanitized_text = sanitize_config_for_delivery(row.config_text)

        if not sanitized_text:
            skipped += 1
            continue

        if not is_export_safe(sanitized_text):
            skipped += 1
            continue

        fp = extract_fingerprint(sanitized_text)
        if (
            fp["protocol"] == "unknown"
            or fp["server"] in ("", "unknown")
            or not fp["port"]
            or not fp["network_type"]
        ):
            skipped += 1
            continue
        delivery_score = calculate_delivery_score(row)

        candidates.append(
            {
                "row": row,
                "config_text": sanitized_text,
                "strong_family": fp["strong_family"],
                "host_family": fp["host_family"],
                "delivery_score": delivery_score,
                "quality_score": safe_float(row.quality_score),
                "latency": safe_float(row.latency, 9999),
                "item": make_public_item(row, sanitized_text, fp, delivery_score),
            }
        )
        candidates.sort(
            key=lambda item: (
                item["delivery_score"],
                item["quality_score"],
                -item["latency"],
            ),
            reverse=True
        )

    return candidates, skipped


def select_hot(candidates, limit):
    """
    Hot باید بسیار متنوع باشد.
    pass 1:
      نه strong_family تکراری
      نه host_family تکراری
    pass 2:
      نه strong_family تکراری
    pass 3:
      اگر هنوز به ۵ نرسیدیم، فقط exact duplicate نده.
    """
    selected = []
    selected_texts = set()
    used_strong = set()
    used_host = set()

    # pass 1: سخت‌گیرانه‌ترین حالت
    for candidate in candidates:
        if len(selected) >= limit:
            break

        if candidate["config_text"] in selected_texts:
            continue

        if candidate["strong_family"] in used_strong:
            continue

        if candidate["host_family"] in used_host:
            continue

        selected.append(candidate)
        selected_texts.add(candidate["config_text"])
        used_strong.add(candidate["strong_family"])
        used_host.add(candidate["host_family"])

    # pass 2: اگر کم بود، host_family را آزادتر می‌کنیم
    for candidate in candidates:
        if len(selected) >= limit:
            break

        if candidate["config_text"] in selected_texts:
            continue

        if candidate["strong_family"] in used_strong:
            continue

        selected.append(candidate)
        selected_texts.add(candidate["config_text"])
        used_strong.add(candidate["strong_family"])

    # pass 3: اگر باز هم کم بود، فقط exact duplicate را حذف می‌کنیم
    for candidate in candidates:
        if len(selected) >= limit:
            break

        if candidate["config_text"] in selected_texts:
            continue

        selected.append(candidate)
        selected_texts.add(candidate["config_text"])

    return selected


def select_reserve(candidates, hot, limit):
    """
    Reserve می‌تواند کمی کمتر سخت‌گیر باشد، اما نباید از یک خانواده بیش از حد پر شود.
    """
    selected = []
    selected_texts = {item["config_text"] for item in hot}

    family_counts = {}
    host_counts = {}

    for item in hot:
        family_counts[item["strong_family"]] = family_counts.get(item["strong_family"], 0) + 1
        host_counts[item["host_family"]] = host_counts.get(item["host_family"], 0) + 1

    # pass 1: حداکثر ۲ عدد از هر strong_family و حداکثر ۳ عدد از هر host_family
    for candidate in candidates:
        if len(selected) >= limit:
            break
        if candidate["config_text"] in selected_texts:
            continue

        strong_count = family_counts.get(candidate["strong_family"], 0)
        host_count = host_counts.get(candidate["host_family"], 0)

        if strong_count >= 2:
            continue

        if host_count >= 3:
            continue

        selected.append(candidate)
        selected_texts.add(candidate["config_text"])
        family_counts[candidate["strong_family"]] = strong_count + 1
        host_counts[candidate["host_family"]] = host_count + 1

    # pass 2: اگر reserve کامل نشد، فقط exact duplicate را حذف می‌کنیم
    for candidate in candidates:
        if len(selected) >= limit:
            break

        if candidate["config_text"] in selected_texts:
            continue

        selected.append(candidate)
        selected_texts.add(candidate["config_text"])

    return selected


def export_public_configs():
    session = SessionLocal()

    try:
        candidates, skipped = load_candidates(session)

        hot = select_hot(candidates, HOT_LIMIT)
        reserve = select_reserve(candidates, hot, RESERVE_LIMIT)

        hot_items = [candidate["item"] for candidate in hot]
        reserve_items = [candidate["item"] for candidate in reserve]

        payload = {
            "generated_at": utc_now_text(),
            "version": 2,
            "strategy": "hot_reserve_diverse_delivery_score",
            "hot_count": len(hot_items),
            "reserve_count": len(reserve_items),

            # ساختار جدید
            "hot": hot_items,
            "reserve": reserve_items,

            # سازگاری با نسخه قبلی repository/API
            # اگر جایی هنوز payload["configs"] را بخواند، فقط hot را می‌گیرد.
            "configs": hot_items,
        }

        temp_path = EXPORT_PATH + ".tmp"

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        os.replace(temp_path, EXPORT_PATH)

        print("PUBLIC CONFIGS EXPORTED")
        print("EXPORT_PATH:", EXPORT_PATH)
        print("CANDIDATES:", len(candidates))
        print("HOT:", len(hot_items))
        print("RESERVE:", len(reserve_items))
        print("SKIPPED:", skipped)

        print("\nHOT PREVIEW:")
        for index, item in enumerate(hot_items, start=1):
            print(
                f"{index}) "
                f"id={item['source_id']} "
                f"q={item['quality_score']} "
                f"delivery={item['delivery_score']} "
                f"latency={item['latency']} "
                f"down={item['download_mbps']} "
                f"up={item['upload_mbps']} "
                f"server={item['server']} "
                f"port={item['port']} "
                f"type={item['network_type']}"
                f"sec={item['security']}"
            )

    finally:
        session.close()


if __name__ == "__main__":
    export_public_configs()