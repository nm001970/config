import base64
import html
from urllib.parse import urlsplit, parse_qs, unquote

from worker.core.unified_config import UnifiedConfig

SUPPORTED_SS_METHODS = {
    "aes-128-gcm",
    "aes-192-gcm",
    "aes-256-gcm",
    "chacha20-poly1305",
    "chacha20-ietf-poly1305",
    "xchacha20-poly1305",
    "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm",
    "2022-blake3-chacha20-poly1305",
}

def _validate_method(method: str):
    method = (method or "").strip().lower()

    if method not in SUPPORTED_SS_METHODS:
        raise ValueError(f"Unsupported Shadowsocks cipher method: {method}")

    return method

def _add_padding(value: str):
    return value + "=" * (-len(value) % 4)


def _b64_decode(value: str):
    value = html.unescape(unquote(value.strip()))
    value = _add_padding(value)

    try:
        return base64.urlsafe_b64decode(value).decode("utf-8", errors="ignore")
    except Exception:
        return base64.b64decode(value).decode("utf-8", errors="ignore")


def _split_method_password(value: str):
    if ":" not in value:
        raise ValueError("Invalid Shadowsocks userinfo: missing method/password")

    method, password = value.split(":", 1)

    method = method.strip()
    password = password.strip()

    if not method or not password:
        raise ValueError("Invalid Shadowsocks userinfo: empty method/password")

    return method, password


def _parse_host_port(value: str):
    parsed = urlsplit("//" + value)

    server = parsed.hostname
    port = parsed.port

    if not server or not port:
        raise ValueError("Invalid Shadowsocks server/port")

    return server, int(port)


def parse(config_url: str):
    parsed = urlsplit(config_url)
    params = parse_qs(parsed.query)

    plugin = params.get("plugin", [""])[0] or ""
    if plugin:
        raise ValueError("Unsupported Shadowsocks plugin config")

    remark = unquote(parsed.fragment) if parsed.fragment else ""

    server = parsed.hostname
    port = parsed.port
    method = ""
    password = ""

    # SIP002:
    # ss://BASE64(method:password)@host:port#remark
    # یا plain:
    # ss://method:password@host:port#remark
    if server and port and parsed.username:
        username = unquote(parsed.username)

        if parsed.password:
            method = username
            password = unquote(parsed.password)
        else:
            try:
                decoded_userinfo = _b64_decode(username)
                method, password = _split_method_password(decoded_userinfo)
            except Exception:
                # اگر username فقط uuid باشد، SS استاندارد نیست.
                raise ValueError("Unsupported Shadowsocks userinfo format")

    else:
        # Legacy:
        # ss://BASE64(method:password@host:port)#remark
        payload = config_url.replace("ss://", "", 1).split("#", 1)[0].split("?", 1)[0]
        decoded = _b64_decode(payload)

        if "@" not in decoded:
            raise ValueError("Invalid legacy Shadowsocks payload")

        userinfo, host_port = decoded.rsplit("@", 1)

        method, password = _split_method_password(userinfo)
        server, port = _parse_host_port(host_port)

    if not server or not port:
        raise ValueError("Invalid Shadowsocks server/port")

    if not method or not password:
        raise ValueError("Invalid Shadowsocks method/password")

    transport = {
        "type": "tcp",
        "host": "",
        "path": "",
        "service_name": "",
        "mode": "",
    }

    tls = {
        "enabled": False,
        "security": "none",
        "sni": "",
        "fp": "",
        "alpn": "",
    }

    if not method or not password:
        raise ValueError("Invalid Shadowsocks method/password")

    method = _validate_method(method)

    auth = {
        "method": method,
        "password": password,
    }

    metadata = {
        "remark": remark,
    }

    return UnifiedConfig(
        protocol="ss",
        server=server,
        port=int(port),
        auth=auth,
        transport=transport,
        tls=tls,
        metadata=metadata,
        raw_url=config_url,
    )
