import json
import os
from collections import Counter
from urllib.parse import urlsplit, parse_qs

from database.db import SessionLocal
from database.models import Config


PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def get_first_param(params, key, default=""):
    value = params.get(key)
    if not value:
        return default
    return value[0] or default


def extract_fingerprint(config_text):
    try:
        parsed = urlsplit(config_text)
        params = parse_qs(parsed.query)

        protocol = (parsed.scheme or "").lower()
        uuid = (parsed.username or "").lower()
        host = (parsed.hostname or "").lower()
        port = str(parsed.port or "")

        network_type = get_first_param(params, "type", "").lower()
        mode = get_first_param(params, "mode", "").lower()
        security = get_first_param(params, "security", "none").lower()
        sni = get_first_param(params, "sni", "").lower()
        host_header = get_first_param(params, "host", "").lower()

        strong_family = "|".join(
            [
                protocol,
                uuid,
                port,
                network_type,
                mode,
                security,
                sni,
                host_header,
            ]
        )

        return {
            "server": host,
            "port": port,
            "type": network_type,
            "security": security,
            "strong_family": strong_family,
        }

    except Exception:
        return {
            "server": "unknown",
            "port": "",
            "type": "",
            "security": "",
            "strong_family": "unknown",
        }


def proposed_delivery_score(row):
    """
    فقط برای audit است؛ فعلاً DB را تغییر نمی‌دهد.

    هدف:
    quality_score مهم بماند،
    اما download/upload/latency/ping هم در انتخاب اثر داشته باشند.
    """
    quality_score = safe_float(row.quality_score)
    latency = safe_float(row.latency, 9999)
    ping_avg = safe_float(row.ping_avg_ms, latency)
    download = safe_float(row.download_mbps)
    upload = safe_float(row.upload_mbps)

    score = quality_score

    # سرعت دانلود، ولی سقف‌دار تا یک عدد غیرعادی همه چیز را خراب نکند.
    score += min(download, 10.0) * 2.0

    # آپلود مهم است، اما نباید بیش از حد غالب شود.
    score += min(upload, 3.0) * 3.0

    # latency بهتر، امتیاز مثبت.
    if latency <= 400:
        score += 8
    elif latency <= 700:
        score += 5
    elif latency <= 1000:
        score += 2
    else:
        score -= 5

    # ping_avg بهتر، امتیاز مثبت.
    if ping_avg <= 500:
        score += 5
    elif ping_avg <= 800:
        score += 2
    elif ping_avg > 1200:
        score -= 6

    # اگر upload صفر است، کمی جریمه شود.
    # چون قبلاً دیدیم بعضی score بالاها upload=0 دارند.
    if upload <= 0:
        score -= 5

    # اگر download خیلی پایین است، جریمه شود.
    if download < 1:
        score -= 10

    return round(score, 2)


def load_public_hot():
    if not os.path.exists(PUBLIC_CONFIGS_FILE):
        return []

    with open(PUBLIC_CONFIGS_FILE, "r", encoding="utf-8") as f:
        payload = json.load(f)

    hot = payload.get("hot") or payload.get("configs") or []
    result = []

    for item in hot:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"config_text": item})

    return result


def print_row(index, row):
    fp = extract_fingerprint(row.config_text)

    print(
        f"{index:02d}) "
        f"id={row.id} "
        f"q={safe_float(row.quality_score):.1f} "
        f"delivery={proposed_delivery_score(row):.1f} "
        f"lat={safe_float(row.latency):.0f} "
        f"ping={safe_float(row.ping_avg_ms):.0f} "
        f"down={safe_float(row.download_mbps):.2f} "
        f"up={safe_float(row.upload_mbps):.2f} "
        f"{fp['server']}:{fp['port']} "f"type={fp['type']} "
        f"sec={fp['security']}"
    )


def main():
    session = SessionLocal()

    try:
        print("=" * 60)
        print("SCORING AUDIT")
        print("=" * 60)

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
            .limit(50)
            .all()
        )

        print("\nTOP 30 BY CURRENT EXPORT ORDER:")
        for index, row in enumerate(rows[:30], start=1):
            print_row(index, row)

        rows_by_delivery = sorted(
            rows,
            key=lambda r: proposed_delivery_score(r),
            reverse=True
        )

        print("\nTOP 30 BY PROPOSED DELIVERY SCORE:")
        for index, row in enumerate(rows_by_delivery[:30], start=1):
            print_row(index, row)

        hot = load_public_hot()

        print("\nCURRENT PUBLIC HOT:")
        hot_source_ids = []

        for index, item in enumerate(hot, start=1):
            source_id = item.get("source_id")
            hot_source_ids.append(source_id)

            print(
                f"{index:02d}) "
                f"id={source_id} "
                f"q={item.get('quality_score')} "
                f"lat={item.get('latency')} "
                f"down={item.get('download_mbps')} "
                f"up={item.get('upload_mbps')} "
                f"{item.get('server')}:{item.get('port')} "
                f"type={item.get('network_type')} "
                f"sec={item.get('security')}"
            )

        hot_families = []
        for item in hot:
            config_text = item.get("config_text", "")
            fp = extract_fingerprint(config_text)
            hot_families.append(fp["strong_family"])

        family_counts = Counter(hot_families)
        repeated = [(family, count) for family, count in family_counts.items() if count > 1]

        print("\nHOT FAMILY CHECK:")
        if repeated:
            print("WARNING: repeated families found:")
            for family, count in repeated:
                print(f"count={count} family={family}")
        else:
            print("OK: no repeated strong family in Hot")

        print("\nQUALIFIED SUMMARY:")
        total_qualified = (
            session.query(Config)
            .filter(
                Config.status == "alive",
                Config.quality_status == "qualified",
            )
            .count()
        )
        print("total qualified:", total_qualified)

        print("\nAUDIT RESULT:")
        print("Review the difference between CURRENT EXPORT ORDER and PROPOSED DELIVERY SCORE.")
        print("If proposed ranking looks better, we will update export_public_configs.py ranking logic.")

    finally:
        session.close()


if __name__ == "__main__":
    main()