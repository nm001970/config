import base64
import json
from urllib.parse import unquote
import html

from worker.core.unified_config import UnifiedConfig


def _decode_vmess_payload(config_url: str):
    payload = config_url.replace("vmess://", "", 1).strip()
    payload = unquote(payload)
    payload = html.unescape(payload)

    padding = "=" * (-len(payload) % 4)

    try:
        decoded = base64.urlsafe_b64decode(payload + padding)
    except Exception:
        decoded = base64.b64decode(payload + padding)

    text = decoded.decode("utf-8", errors="ignore")
    return json.loads(text)


def _get(data, key, default=""):
    value = data.get(key, default)
    if value is None:
        return default
    return value

def _normalize_security(value) -> str:
    if isinstance(value, bool):
        return "tls" if value else "none"

    if value is None:
        return "none"

    security = str(value).strip().lower()

    if security in {
        "tls",
        "true",
        "1",
        "yes",
        "on",
    }:
        return "tls"

    if security == "reality":
        return "reality"

    if security in {
        "",
        "none",
        "false",
        "0",
        "no",
        "off",
        "auto",
    }:
        return "none"

    # مقدار ناشناخته نباید مستقیماً وارد
    # streamSettings.security شود.
    return "none"


def parse(config_url: str):
    data = _decode_vmess_payload(config_url)

    server = str(_get(data, "add", "")).strip()
    port = int(_get(data, "port", 0))

    uuid = str(_get(data, "id", "")).strip()
    alter_id = int(_get(data, "aid", 0) or 0)

    network = str(_get(data, "net", "tcp") or "tcp").strip()
    security = _normalize_security(
    _get(data, "tls", None)
)

    if security == "":
        security = "none"

    host = str(_get(data, "host", "") or "").strip()
    path = str(_get(data, "path", "") or "").strip()
    sni = str(_get(data, "sni", "") or "").strip()
    fp = str(_get(data, "fp", "chrome") or "chrome").strip()

    header_type = str(_get(data, "type", "none") or "none").strip()

    auth = {
        "uuid": uuid,
        "alter_id": alter_id,
        "security": str(_get(data, "scy", "auto") or "auto").strip()
    }

    transport = {
        "type": network,
        "host": host,
        "path": path,
        "header_type": header_type,
        "service_name": path if network == "grpc" else ""
    }

    tls = {
        "enabled": security in ("tls", "reality"),
        "security": security,
        "sni": sni or host,
        "fp": fp,
        "alpn": str(_get(data, "alpn", "") or "").strip()
    }

    metadata = {
        "remark": str(_get(data, "ps", "") or "").strip()
    }

    return UnifiedConfig(
        protocol="vmess",
        server=server,
        port=port,
        auth=auth,
        transport=transport,
        tls=tls,
        metadata=metadata,
        raw_url=config_url
    )