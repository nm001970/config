from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, unquote


BRAND_REMARK = "@mnproxyBot"

BLOCKED_TEXTS = [
    "telegram",
    "TELEGRAM",
    "Telegram",
    "t.me",
    "T.ME",
    "join",
    "JOIN",
    "Join",
    "channel",
    "CHANNEL",
    "Channel",
    "subscribe",
    "SUBSCRIBE",
    "Subscribe",
    "whatsapp",
    "WHATSAPP",
    "instagram",
    "INSTAGRAM",
    "youtube",
    "YOUTUBE",
    "تلگرام",
    "کانال",
    "عضویت",
]


LEAK_MARKERS = [
    "telegram",
    "t.me",
    "join",
    "channel",
    "subscribe",
    "whatsapp",
    "instagram",
    "youtube",
    "تلگرام",
    "کانال",
    "عضویت",
]


def clean_value(value: str) -> str:
    if not value:
        return value

    cleaned = value

    for blocked in BLOCKED_TEXTS:
        cleaned = cleaned.replace(blocked, "")

    cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.replace("..", ".")
    cleaned = cleaned.strip("_- .")

    return cleaned


def has_remaining_leak(value: str) -> bool:
    if not value:
        return False

    decoded = unquote(value).lower()

    for marker in LEAK_MARKERS:
        if marker in decoded:
            return True

    return False


def has_suspicious_query_at(value: str) -> bool:
    """
    @ داخل vless://uuid@host طبیعی است.
    اما @ داخل query معمولاً تبلیغ یا منبع است.
    """
    if not value:
        return False

    return "@" in unquote(value)


def sanitize_config_text(config_text: str, remark: str = BRAND_REMARK):
    """
    خروجی:
    - config تمیز شده
    - یا None اگر بعد از پاک‌سازی هنوز نشتی مشکوک وجود داشته باشد
    """

    if not config_text:
        return None

    config_text = config_text.strip()

    if not config_text:
        return None

    try:
        parts = urlsplit(config_text)

        query_items = parse_qsl(
            parts.query,
            keep_blank_values=True
        )

        cleaned_query_items = []

        for key, value in query_items:
            cleaned_key = clean_value(key)
            cleaned_value = clean_value(value)

            if not cleaned_key:
                continue

            if has_remaining_leak(cleaned_key) or has_remaining_leak(cleaned_value):
                return None

            if has_suspicious_query_at(cleaned_key) or has_suspicious_query_at(cleaned_value):
                return None

            cleaned_query_items.append(
                (cleaned_key, cleaned_value)
            )

        cleaned_query = urlencode(
            cleaned_query_items,
            doseq=True
        )

        sanitized = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                cleaned_query,
                remark
            )
        )

        return sanitized

    except Exception:
        base = config_text.split("#", 1)[0]

        for blocked in BLOCKED_TEXTS:
            base = base.replace(blocked, "")

        if has_remaining_leak(base):
            return None

        return f"{base}#{remark}"


def sanitize_config_for_delivery(config_text, remark=BRAND_REMARK):
    """
    لایه دفاعی قبل از خروجی Bot/API.
    حتی اگر config قبلاً sanitize شده باشد، دوباره idempotent تمیز می‌شود.
    """
    return sanitize_config_text(config_text, remark=remark)


def sanitize_configs_for_storage(configs):
    """
    ورودی:
    [
        {"protocol": "vless", "config": "..."},
        ...
    ]

    خروجی:
    [
        {"protocol": "vless", "config": "sanitized config"},
        ...
    ]
    """

    sanitized_configs = []
    skipped = 0

    for item in configs:
        sanitized_text = sanitize_config_text(
            item.get("config")
        )

        if not sanitized_text:
            skipped += 1
            continue

        sanitized_configs.append(
            {
                "protocol": item["protocol"],
                "config": sanitized_text
            }
        )

    return sanitized_configs, skipped