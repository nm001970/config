import json
import os
from collections import Counter
# from urllib.parse import urlsplit, parse_qs


PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
import base64
import binascii
import urllib.parse
EXPECTED_HOT_LIMIT = int(os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "5"))

BLOCKED_MARKERS = [
    "trycloudflare",
    "127.0.0.1",
    "localhost",
]

EXPECTED_REMARK = "@mnproxyBot"



def decode_vmess_payload(config_text):
    payload = config_text.split("://", 1)[1]
    payload = payload.split("#", 1)[0].strip()
    payload += "=" * (-len(payload) % 4)

    decoded_bytes = base64.b64decode(
        payload.encode("ascii"),
        altchars=b"-_",
        validate=False,
    )

    decoded = json.loads(
        decoded_bytes.decode("utf-8-sig")
    )

    if not isinstance(decoded, dict):
        raise ValueError(
            "Decoded VMess payload must be a JSON object."
        )

    return decoded


def has_expected_remark(config_text):
    if config_text.startswith("vmess://"):
        try:
            vmess_data = decode_vmess_payload(config_text)
        except (
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            return False

        return vmess_data.get("ps") == EXPECTED_REMARK

    try:
        fragment = urllib.parse.unquote(
            urllib.parse.urlsplit(config_text).fragment
        )
    except ValueError:
        return False

    return fragment == EXPECTED_REMARK

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


def require_public_metadata(item, name, index):
    protocol = str(item.get("protocol") or "").strip().lower()
    server = str(item.get("server") or "").strip()
    port = str(item.get("port") or "").strip()
    network_type = str(item.get("network_type") or "").strip().lower()
    security = str(item.get("security") or "none").strip().lower()

    if protocol not in ("vless", "vmess", "ss", "trojan"):
        fail(f"{name}[{index}] has invalid protocol metadata: {protocol}")

    if not server or server == "unknown":
        fail(f"{name}[{index}] has invalid server metadata")

    if server.startswith(("vless://", "vmess://", "ss://", "trojan://")):
        fail(f"{name}[{index}] server metadata looks like config text")

    # VMess base64 payload اشتباهاً به‌عنوان server نباید قبول شود.
    if protocol == "vmess" and len(server) > 120:
        fail(f"{name}[{index}] vmess server metadata looks invalid: {server[:80]}")

    if not port:
        fail(f"{name}[{index}] has empty port metadata")

    try:
        port_number = int(port)
        if port_number <= 0 or port_number > 65535:
            fail(f"{name}[{index}] has invalid port metadata: {port}")
    except Exception:
        fail(f"{name}[{index}] has non-numeric port metadata: {port}")

    if not network_type:
        fail(f"{name}[{index}] has empty network_type metadata")

    if not security:
        fail(f"{name}[{index}] has empty security metadata")

    return {
        "protocol": protocol,
        "server": server.lower(),
        "port": port,
        "network_type": network_type,
        "security": security,
    }


def extract_family(item):
    meta = require_public_metadata(item, "family", item.get("source_id", "?"))

    return "|".join([
        meta["protocol"],
        meta["server"],
        meta["port"],
        meta["network_type"],
        meta["security"],
    ])


def check_configs(name, items, min_count=1):
    if not isinstance(items, list):
        fail(f"{name} must be a list")

    if len(items) < min_count:
        fail(f"{name} has too few items: {len(items)}")

    texts = []

    for index, item in enumerate(items, start=1):
        config_text = get_config_text(item)

        require_public_metadata(item, name, index)

        if not config_text:
            fail(f"{name}[{index}] has empty config_text")

        lowered = config_text.lower()

        for marker in BLOCKED_MARKERS:
            if marker in lowered:
                fail(f"{name}[{index}] contains blocked marker: {marker}")

        if not config_text.startswith(("vless://", "vmess://", "ss://", "trojan://")):
            fail(f"{name}[{index}] has unsupported URL prefix")

        if not has_expected_remark(config_text):
            fail(
                f"{name}[{index}] does not contain expected remark "
                f"{EXPECTED_REMARK}"
            )

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

    families = [extract_family(item) for item in hot]
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
    for index, item in enumerate(hot, start=1):
        print(
            f"{index}) "
            f"{item.get('server')}:{item.get('port')} "
            f"type={item.get('network_type')} "
            f"security={item.get('security')}"
        )

    print("\nRESULT: PUBLIC EXPORT IS VALID")


if __name__ == "__main__":
    main()