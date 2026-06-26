import html
from urllib.parse import urlsplit, parse_qs, unquote

from worker.core.unified_config import UnifiedConfig


def _get(params, key, default=""):
    value = params.get(key)
    if not value:
        return default
    return value[0] or default


def _normalize_security(value, port, sni):
    security = (value or "").strip().lower()

    # Trojan معمولاً TLS دارد. اگر security نیامده باشد،
    # برای پورت 443 یا وجود sni، پیش‌فرض tls امن‌تر است.
    if not security:
        if str(port) == "443" or sni:
            return "tls"
        return "none"

    return security


def parse(config_url: str):
    config_url = html.unescape(config_url.strip())

    parsed = urlsplit(config_url)
    params = parse_qs(parsed.query)

    password = unquote(parsed.username or "").strip()
    server = parsed.hostname
    port = parsed.port

    if not password:
        raise ValueError("Invalid Trojan password")

    if not server or not port:
        raise ValueError("Invalid Trojan server/port")

    network = _get(params, "type", "tcp").strip().lower() or "tcp"

    sni = (
        _get(params, "sni", "")
        or _get(params, "peer", "")
        or _get(params, "host", "")
    ).strip()

    security = _normalize_security(
        _get(params, "security", ""),
        port,
        sni,
    )

    path = unquote(_get(params, "path", "/") or "/")
    host = _get(params, "host", "").strip()

    service_name = (
        _get(params, "serviceName", "")
        or _get(params, "service_name", "")
    ).strip()

    fp = _get(params, "fp", "chrome").strip() or "chrome"

    pbk = _get(params, "pbk", "").strip()
    sid = _get(params, "sid", "").strip()
    spx = unquote(_get(params, "spx", "/") or "/")

    transport = {
        "type": network,
        "host": host,
        "path": path,
        "service_name": service_name,
        "mode": _get(params, "mode", "").strip(),
        "header_type": _get(params, "headerType", "").strip(),
    }

    tls = {
        "enabled": security != "none",
        "security": security,
        "sni": sni,
        "fp": fp,
        "pbk": pbk,
        "sid": sid,
        "spx": spx,
        "alpn": _get(params, "alpn", "").strip(),
    }

    auth = {
        "password": password,
    }

    metadata = {
        "remark": unquote(parsed.fragment) if parsed.fragment else "",
    }

    return UnifiedConfig(
        protocol="trojan",
        server=server,
        port=int(port),
        auth=auth,
        transport=transport,
        tls=tls,
        metadata=metadata,
        raw_url=config_url,
    )