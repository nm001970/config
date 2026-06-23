import json
import os
from collections import Counter
from urllib.parse import urlsplit, parse_qs


PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
EXPECTED_HOT_LIMIT = int(os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "10"))

BLOCKED_MARKERS = [
    "trycloudflare",
    "127.0.0.1",
    "localhost",
]

EXPECTED_REMARK = "@mnproxyBot"


def fail(message):
    print(f"FAIL: {message}")
    raise SystemExit(1)


def warn(message):
    print(f"WARNING: {message}")


def ok(message):
    print(f"OK: {message}")


def load_payload():
    if not os.path.exists(PUBLIC_CONFIGS_FILE):
        fail(f"{PUBLIC_CONFIGS_FILE} not found")

    with open(PUBLIC_CONFIGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_config_text(item):
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        return (item.get("config_text") or "").strip()

    return ""


def extract_family(config_text):
    try:
        parsed = urlsplit(config_text)
        params = parse_qs(parsed.query)

        protocol = parsed.scheme.lower()
        uuid = (parsed.username or "").lower()
        host = (parsed.hostname or "").lower()
        port = str(parsed.port or "")

        network_type = (params.get("type", [""])[0] or "").lower()
        mode = (params.get("mode", [""])[0] or "").lower()
        security = (params.get("security", ["none"])[0] or "none").lower()
        sni = (params.get("sni", [""])[0] or "").lower()
        host_header = (params.get("host", [""])[0] or "").lower()

        return "|".join([
            protocol,
            uuid,
            port,
            network_type,
            mode,
            security,
            sni,
            host_header,
            host,
        ])

    except Exception:
        return "unknown"


def check_configs(name, items, min_count=1):
    if not isinstance(items, list):
        fail(f"{name} must be a list")

    if len(items) < min_count:
        fail(f"{name} has too few items: {len(items)}")

    texts = []

    for index, item in enumerate(items, start=1):
        config_text = get_config_text(item)

        if not config_text:
            fail(f"{name}[{index}] has empty config_text")

        lowered = config_text.lower()

        for marker in BLOCKED_MARKERS:
            if marker in lowered:
                fail(f"{name}[{index}] contains blocked marker: {marker}")

        if not config_text.startswith(("vless://", "vmess://", "ss://", "trojan://")):
            fail(f"{name}[{index}] has unsupported URL prefix")

        if f"#{EXPECTED_REMARK}" not in config_text:
            fail(f"{name}[{index}] does not contain remark #{EXPECTED_REMARK}")

        texts.append(config_text)

    duplicate_count = len(texts) - len(set(texts))
    if duplicate_count > 0:
        fail(f"{name} contains exact duplicates: {duplicate_count}")

    ok(f"{name} basic checks passed: {len(texts)} items")

    return texts


def main():
    payload = load_payload()

    hot = payload.get("hot", [])
    reserve = payload.get("reserve", [])

    print("=" * 40)
    print("PUBLIC EXPORT CHECK")
    print("=" * 40)

    print("file:", PUBLIC_CONFIGS_FILE)
    print("version:", payload.get("version"))
    print("strategy:", payload.get("strategy"))
    print("generated_at:", payload.get("generated_at"))

    hot_texts = check_configs(
        name="hot",
        items=hot,
        min_count=EXPECTED_HOT_LIMIT
    )

    if len(hot_texts) != EXPECTED_HOT_LIMIT:
        fail(
            f"hot count mismatch: expected {EXPECTED_HOT_LIMIT}, got {len(hot_texts)}"
        )

    if reserve:
        check_configs(
            name="reserve",
            items=reserve,
            min_count=1
        )
        ok(f"reserve exists: {len(reserve)} items")
    else:
        warn("reserve is empty")

    families = [extract_family(text) for text in hot_texts]
    family_counts = Counter(families)

    repeated_families = [
        (family, count)
        for family, count in family_counts.items()
        if count > 1
    ]

    if repeated_families:
        warn("hot has repeated families:")
        for family, count in repeated_families:
            print(f"  count={count} family={family}")
    else:
        ok("hot family diversity looks good")

    print("\nHOT PREVIEW:")
    for index, text in enumerate(hot_texts, start=1):
        parsed = urlsplit(text)
        params = parse_qs(parsed.query)
        print(
            f"{index}) "
            f"{parsed.hostname}:{parsed.port} "
            f"type={params.get('type', [''])[0]} "
            f"security={params.get('security', ['none'])[0]}"
        )

    print("\nRESULT: PUBLIC EXPORT IS VALID")


if __name__ == "__main__":
    main()