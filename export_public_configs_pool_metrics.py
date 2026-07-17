import os
import json
import ipaddress
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs

from database.db import SessionLocal
from database.models import Config, ConfigPool
from database.repositories.pool_repository import normalize_operator
from worker.config_sanitizer import sanitize_config_for_delivery
from worker.parsers.factory import parse_config

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_env_file():
    env_path = PROJECT_ROOT / ".env"

    if not env_path.exists():
        return

    with open(env_path, "r", encoding="utf-8-sig") as f:
        for raw_line in f:
            line = raw_line.strip()

            if not line:
                continue

            if line.startswith("#"):
                continue

            if "=" not in line:
                continue

            key, value = line.split("=", 1)

            key = key.strip()
            value = value.strip()

            if not key:
                continue

            if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
                value = value[1:-1]

            os.environ.setdefault(key, value)


load_env_file()


EXPORT_PATH = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")

HOT_LIMIT = int(os.getenv("PUBLIC_HOT_LIMIT", "5"))
RESERVE_LIMIT = int(os.getenv("PUBLIC_RESERVE_LIMIT", "40"))

POOL_OPERATOR = normalize_operator(
    os.getenv(
        "PUBLIC_POOL_OPERATOR",
        os.getenv(
            "AUTO_CYCLE_POOL_SUPERVISOR_OPERATOR",
            "irancell",
        ),
    )
)

EXPORTABLE_HEALTH_STATUSES = (
    "unknown",
    "healthy",
    "suspect",
)


BLOCKED_EXPORT_MARKERS = [
    "trycloudflare",
    "127.0.0.1",
    "localhost",
]


def utc_now_text():
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def positive_float(value, default=0.0):
    number = safe_float(value, default)
    if number <= 0:
        return default
    return number


def build_effective_metrics(row, pool_row):
    base_download = positive_float(row.download_mbps)
    health_median = positive_float(
        pool_row.health_median_download_mbps
    )
    health_average = positive_float(
        pool_row.health_avg_download_mbps
    )

    if health_median > 0:
        download = health_median
        download_source = "pool_health_median"
    elif health_average > 0:
        download = health_average
        download_source = "pool_health_average"
    else:
        download = base_download
        download_source = "config_quality"

    latency = positive_float(row.latency, 9999.0)
    ping_avg = positive_float(
        row.ping_avg_ms,
        latency,
    )

    return {
        "quality_score": safe_float(row.quality_score),
        "latency": latency,
        "ping_avg_ms": ping_avg,
        "download_mbps": download,
        "upload_mbps": positive_float(row.upload_mbps),
        "download_source": download_source,
        "health_avg_download_mbps": health_average,
        "health_median_download_mbps": health_median,
    }


def calculate_delivery_score(metrics):
    quality_score = metrics["quality_score"]
    latency = metrics["latency"]
    ping_avg = metrics["ping_avg_ms"]
    download = metrics["download_mbps"]
    upload = metrics["upload_mbps"]

    score = quality_score

    # download Ù…Ù‡Ù… Ø§Ø³ØªØŒ Ø§Ù…Ø§ Ø³Ù‚Ùâ€ŒØ¯Ø§Ø± ØªØ§ Ø¹Ø¯Ø¯Ù‡Ø§ÛŒ ØºÛŒØ±Ø¹Ø§Ø¯ÛŒ ØºØ§Ù„Ø¨ Ù†Ø´ÙˆÙ†Ø¯.
    score += min(download, 10.0) * 2.0

    # upload Ù…Ù‡Ù… Ø§Ø³ØªØŒ ÙˆÙ„ÛŒ Ú©Ù…ØªØ± Ø§Ø² download.
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

    # upload ØµÙØ± ÛŒØ¹Ù†ÛŒ Ú©ÛŒÙÛŒØª Ù†Ø§Ù‚Øµâ€ŒØªØ± Ø§Ø³Øª.
    if upload <= 0:
        score -= 5

    # download Ø®ÛŒÙ„ÛŒ Ù¾Ø§ÛŒÛŒÙ† Ù‚Ø§Ø¨Ù„ Ù‚Ø¨ÙˆÙ„ Ù†ÛŒØ³Øª.
    if download < 1:
        score -= 10

    return round(score, 2)


def normalize_host_family(host):
    """
    Ø¨Ø±Ø§ÛŒ Ø¬Ù„ÙˆÚ¯ÛŒØ±ÛŒ Ø§Ø² Ø§Ø±Ø³Ø§Ù„ Ú†Ù†Ø¯ Ú©Ø§Ù†ÙÛŒÚ¯ Ø§Ø² ÛŒÚ© subnet/domain family.
    Ù…Ø«Ø§Ù„:
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
    Fingerprint Ùˆ metadata Ø®Ø±ÙˆØ¬ÛŒ public.

    Ø¨Ø±Ø§ÛŒ VLESS Ùˆ VMess Ù†Ø¨Ø§ÛŒØ¯ ÙÙ‚Ø· urlsplit Ø§Ø³ØªÙØ§Ø¯Ù‡ Ú©Ù†ÛŒÙ….
    VMess Ø¨Ù‡ Ø´Ú©Ù„ vmess://base64-json Ø§Ø³Øª Ùˆ urlsplit Ø¢Ù† Ø±Ø§ Ø§Ø´ØªØ¨Ø§Ù‡
    Ø¨Ù‡â€ŒØ¹Ù†ÙˆØ§Ù† host Ù…ÛŒâ€ŒØ®ÙˆØ§Ù†Ø¯. Ø¨Ù†Ø§Ø¨Ø±Ø§ÛŒÙ† Ø§Ø² parser Ø§ØµÙ„ÛŒ Ù¾Ø±ÙˆÚ˜Ù‡ Ø§Ø³ØªÙØ§Ø¯Ù‡ Ù…ÛŒâ€ŒÚ©Ù†ÛŒÙ….
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

        # metadata Ø®Ø±Ø§Ø¨ Ù†Ø¨Ø§ÛŒØ¯ ÙˆØ§Ø±Ø¯ public Ø´ÙˆØ¯
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


def make_public_item(
    row,
    pool_row,
    sanitized_text,
    fp,
    delivery_score,
    metrics,
):
    return {
        "config_text": sanitized_text,
        "source_id": row.id,
        "protocol": row.protocol,
        "quality_score": metrics["quality_score"],
        "delivery_score": safe_float(delivery_score),
        "latency": metrics["latency"],
        "ping_avg_ms": metrics["ping_avg_ms"],
        "download_mbps": metrics["download_mbps"],
        "upload_mbps": metrics["upload_mbps"],
        "download_source": metrics["download_source"],
        "health_status": pool_row.health_status or "unknown",
        "health_avg_download_mbps": metrics[
            "health_avg_download_mbps"
        ],
        "health_median_download_mbps": metrics[
            "health_median_download_mbps"
        ],
        "server": fp["server"],
        "port": fp["port"],
        "network_type": fp["network_type"],
        "security": fp["security"],
    }


def build_pool_candidate(row, pool_row):
    sanitized_text = sanitize_config_for_delivery(row.config_text)

    if not sanitized_text:
        return None, "sanitize_failed"

    if not is_export_safe(sanitized_text):
        return None, "blocked_marker"

    fp = extract_fingerprint(sanitized_text)

    if (
        fp["protocol"] == "unknown"
        or fp["server"] in ("", "unknown")
        or not fp["port"]
        or not fp["network_type"]
    ):
        return None, "invalid_fingerprint"

    metrics = build_effective_metrics(
        row,
        pool_row,
    )
    delivery_score = calculate_delivery_score(metrics)

    return ({
        "row": row,
        "pool_row": pool_row,
        "config_text": sanitized_text,
        "strong_family": fp["strong_family"],
        "host_family": fp["host_family"],
        "delivery_score": delivery_score,
        "quality_score": metrics["quality_score"],
        "latency": metrics["latency"],
        "health_status": pool_row.health_status or "unknown",
        "health_avg_download_mbps": metrics[
            "health_avg_download_mbps"
        ],
        "health_median_download_mbps": metrics[
            "health_median_download_mbps"
        ],
        "download_source": metrics["download_source"],
        "hot_slot": pool_row.hot_slot,
        "item": make_public_item(
            row,
            pool_row,
            sanitized_text,
            fp,
            delivery_score,
            metrics,
        ),
    }, None)


def hot_sort_key(candidate):
    hot_slot = candidate["hot_slot"]
    if hot_slot is None:
        hot_slot = 10**9

    return (
        int(hot_slot),
        -candidate["delivery_score"],
        candidate["row"].id,
    )


def reserve_sort_key(candidate):
    health_priority = {
        "healthy": 0,
        "unknown": 1,
        "suspect": 2,
    }

    return (
        health_priority.get(candidate["health_status"], 9),
        -candidate["health_median_download_mbps"],
        -candidate["health_avg_download_mbps"],
        -candidate["delivery_score"],
        candidate["latency"],
        candidate["row"].id,
    )


def load_pool_candidates(session, pool_status, limit):
    rows = (
        session.query(Config, ConfigPool)
        .join(
            ConfigPool,
            ConfigPool.config_id == Config.id,
        )
        .filter(
            ConfigPool.operator == POOL_OPERATOR,
            ConfigPool.pool_status == pool_status,
            ConfigPool.gate_status == "accepted",
            ConfigPool.health_status.in_(
                EXPORTABLE_HEALTH_STATUSES
            ),
        )
        .all()
    )

    candidates = []
    skipped = 0
    skip_reasons = {}

    for row, pool_row in rows:
        candidate, skip_reason = build_pool_candidate(row, pool_row)

        if candidate is None:
            skipped += 1
            skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            continue

        candidates.append(candidate)

    if pool_status == "hot":
        candidates.sort(key=hot_sort_key)
    else:
        candidates.sort(key=reserve_sort_key)

    selected = candidates[:max(int(limit), 0)]

    return {
        "raw_rows": len(rows),
        "valid_candidates": len(candidates),
        "selected": selected,
        "skipped": skipped,
        "skip_reasons": skip_reasons,
    }


def export_public_configs():
    if not POOL_OPERATOR:
        raise RuntimeError("PUBLIC_POOL_OPERATOR cannot be empty")

    session = SessionLocal()

    try:
        hot_result = load_pool_candidates(
            session=session,
            pool_status="hot",
            limit=HOT_LIMIT,
        )
        reserve_result = load_pool_candidates(
            session=session,
            pool_status="reserve",
            limit=RESERVE_LIMIT,
        )

        hot = hot_result["selected"]
        reserve = reserve_result["selected"]

        hot_items = [candidate["item"] for candidate in hot]
        reserve_items = [candidate["item"] for candidate in reserve]

        payload = {
            "generated_at": utc_now_text(),
            "version": 2,
            "strategy": "operator_pool_authoritative_health_metrics",
            "operator": POOL_OPERATOR,
            "hot_count": len(hot_items),
            "reserve_count": len(reserve_items),
            "pool_hot_rows": hot_result["raw_rows"],
            "pool_reserve_rows": reserve_result["raw_rows"],
            "hot": hot_items,
            "reserve": reserve_items,
            "configs": hot_items,
        }

        temp_path = EXPORT_PATH + ".tmp"

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        os.replace(temp_path, EXPORT_PATH)

        print("PUBLIC CONFIGS EXPORTED")
        print("EXPORT_PATH:", EXPORT_PATH)
        print("STRATEGY:", payload["strategy"])
        print("POOL IS AUTHORITATIVE: True")
        print("OPERATOR:", POOL_OPERATOR)
        print("POOL HOT ROWS:", hot_result["raw_rows"])
        print("POOL HOT VALID:", hot_result["valid_candidates"])
        print("HOT:", len(hot_items))
        print("HOT SKIPPED:", hot_result["skipped"])
        print("HOT SKIP REASONS:", hot_result["skip_reasons"])
        print("POOL RESERVE ROWS:", reserve_result["raw_rows"])
        print("POOL RESERVE VALID:", reserve_result["valid_candidates"])
        print("RESERVE:", len(reserve_items))
        print("RESERVE SKIPPED:", reserve_result["skipped"])
        print("RESERVE SKIP REASONS:", reserve_result["skip_reasons"])

        print("\nHOT PREVIEW:")
        for index, candidate in enumerate(hot, start=1):
            item = candidate["item"]
            print(
                f"{index}) "
                f"slot={candidate['hot_slot']} "
                f"id={item['source_id']} "
                f"health={candidate['health_status']} "
                f"q={item['quality_score']} "
                f"delivery={item['delivery_score']} "
                f"latency={item['latency']} "
                f"down={item['download_mbps']} "
                f"down_source={item['download_source']} "
                f"up={item['upload_mbps']} "
                f"server={item['server']} "
                f"port={item['port']} "
                f"type={item['network_type']} "
                f"security={item['security']}"
            )

        print("\nRESERVE PREVIEW:")
        for index, candidate in enumerate(reserve, start=1):
            item = candidate["item"]
            print(
                f"{index}) "
                f"id={item['source_id']} "
                f"health={candidate['health_status']} "
                f"health_median={candidate['health_median_download_mbps']} "
                f"delivery={item['delivery_score']} "
                f"server={item['server']} "
                f"port={item['port']}"
            )

    finally:
        session.close()


if __name__ == "__main__":
    export_public_configs()
