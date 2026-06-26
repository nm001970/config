import os
import json


PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")

PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")

SUBSCRIPTION_CONFIG_LIMIT = int(
    os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "10")
)

HOT_DELIVERY_LIMIT = int(
    os.getenv("HOT_DELIVERY_LIMIT", "5")
)

RESERVE_DELIVERY_LIMIT = int(
    os.getenv("RESERVE_DELIVERY_LIMIT", "5")
)

def load_public_configs_payload():
    if not os.path.exists(PUBLIC_CONFIGS_FILE):
        return {}

    with open(PUBLIC_CONFIGS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_config_texts(items, limit):
    result = []

    if not isinstance(items, list):
        return result

    for item in items:
        if len(result) >= limit:
            break

        if isinstance(item, str):
            config_text = item.strip()
        elif isinstance(item, dict):
            config_text = (item.get("config_text") or "").strip()
        else:
            continue

        if not config_text:
            continue

        result.append(config_text)

    return result

def dedupe_configs(configs, limit):
    unique = []
    seen = set()

    for config in configs:
        if not config:
            continue

        if config in seen:
            continue

        seen.add(config)
        unique.append(config)

        if len(unique) >= limit:
            break

    return unique


def get_public_configs_for_subscription(limit=None):
    payload = load_public_configs_payload()

    total_limit = int(limit or SUBSCRIPTION_CONFIG_LIMIT)

    hot_items = payload.get("hot") or []
    reserve_items = payload.get("reserve") or []

    hot_configs = extract_config_texts(
        hot_items,
        limit=HOT_DELIVERY_LIMIT,
    )

    # reserve را کمی بیشتر می‌خوانیم تا اگر duplicate وجود داشت،
    # بعد از dedupe همچنان بتوانیم total_limit را پر کنیم.
    reserve_configs = extract_config_texts(
        reserve_items,
        limit=max(RESERVE_DELIVERY_LIMIT, total_limit),
    )

    selected_configs = dedupe_configs(
        hot_configs + reserve_configs,
        total_limit,
    )

    if selected_configs:
        return selected_configs

    # fallback برای نسخه قبلی public_configs.json
    legacy_items = payload.get("configs") or []

    return extract_config_texts(
        legacy_items,
        limit=total_limit,
    )


def get_public_reserve_configs(limit=45):
    payload = load_public_configs_payload()
    reserve_items = payload.get("reserve")
    return extract_config_texts(reserve_items, limit=limit)