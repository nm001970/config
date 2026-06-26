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


def build(config, socks_port):
    method = (config.auth.get("method", "") or "").strip().lower()
    password = config.auth.get("password", "")

    if not method or not password:
        raise ValueError("Invalid Shadowsocks method/password")
    
    if method not in SUPPORTED_SS_METHODS:
        raise ValueError(f"Unsupported Shadowsocks cipher method: {method}")

    xray_config = {
        "log": {
            "loglevel": "warning"
        },

        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {
                    "udp": True
                }
            }
        ],

        "outbounds": [
            {
                "protocol": "shadowsocks",
                "settings": {
                    "servers": [
                        {
                            "address": config.server,
                            "port": config.port,
                            "method": method,
                            "password": password
                        }
                    ]
                }
            }
        ]
    }

    return xray_config
