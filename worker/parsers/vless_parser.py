from urllib.parse import urlparse, parse_qs, unquote

from worker.core.unified_config import UnifiedConfig


def _get(params, key, default=""):
    return params.get(key, [default])[0]


def parse(config_url: str):

    parsed = urlparse(config_url)
    params = parse_qs(parsed.query)

    security = _get(params, "security", "none")
    network = _get(params, "type", "tcp")

    path = _get(params, "path", "/")
    path = unquote(path) if path else "/"

    host = _get(params, "host", "")
    sni = _get(params, "sni", "")
    fp = _get(params, "fp", "chrome")

    pbk = _get(params, "pbk", "")
    sid = _get(params, "sid", "")
    spx = _get(params, "spx", "/")
    spx = unquote(spx) if spx else "/"

    flow = _get(params, "flow", "")

    auth = {
        "uuid": parsed.username
    }

    if flow:
        auth["flow"] = flow

    transport = {
        "type": network,
        "host": host,
        "path": path,
        "service_name": _get(params, "serviceName", ""),
        "mode": _get(params, "mode", "")
    }

    tls = {
        "enabled": security != "none",
        "security": security,
        "sni": sni,
        "fp": fp,
        "pbk": pbk,
        "sid": sid,
        "spx": spx,
        "alpn": _get(params, "alpn", "")
    }

    metadata = {
        "remark": unquote(parsed.fragment) if parsed.fragment else ""
    }

    return UnifiedConfig(
        protocol="vless",
        server=parsed.hostname,
        port=parsed.port,
        auth=auth,
        transport=transport,
        tls=tls,
        metadata=metadata,
        raw_url=config_url
    )