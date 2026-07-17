def build(config, socks_port):
    user = {
        "id": config.auth["uuid"],
        "alterId": int(config.auth.get("alter_id", 0) or 0),
        "security": config.auth.get("security", "auto") or "auto",
    }

    network = config.transport.get("type", "tcp") or "tcp"
    security = config.tls.get("security", "none") or "none"

    stream_settings = {
        "network": network,
        "security": security,
    }

    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": config.transport.get("path", "/") or "/",
            "headers": {},
        }

        host = config.transport.get("host", "")

        if host:
            stream_settings["wsSettings"]["headers"]["Host"] = host

    elif network == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": (
                config.transport.get("service_name", "")
                or config.transport.get("path", "")
                or ""
            )
        }

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": (
                config.tls.get("sni", "")
                or config.transport.get("host", "")
                or config.server
            ),
            "fingerprint": config.tls.get("fp", "chrome") or "chrome",
        }

    xray_config = {
        "log": {
            "loglevel": "warning",
        },
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": socks_port,
                "protocol": "socks",
                "settings": {
                    "udp": True,
                },
            }
        ],
        "outbounds": [
            {
                "protocol": "vmess",
                "settings": {
                    "vnext": [
                        {
                            "address": config.server,
                            "port": config.port,
                            "users": [user],
                        }
                    ]
                },
                "streamSettings": stream_settings,
            }
        ],
    }

    return xray_config