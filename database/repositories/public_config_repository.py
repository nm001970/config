import os
import json


PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")


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


def get_public_configs_for_subscription(limit=5):
    payload = load_public_configs_payload()

    # نسخه جدید: فقط Hot به کاربر داده می‌شود.
    hot_items = payload.get("hot")
    hot_configs = extract_config_texts(hot_items, limit=limit)

    if hot_configs:
        return hot_configs

    # fallback برای نسخه قبلی public_configs.json
    legacy_items = payload.get("configs")
    return extract_config_texts(legacy_items, limit=limit)


def get_public_reserve_configs(limit=45):
    payload = load_public_configs_payload()
    reserve_items = payload.get("reserve")
    return extract_config_texts(reserve_items, limit=limit)