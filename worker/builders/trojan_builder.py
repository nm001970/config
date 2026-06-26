def build(config, socks_port):
    password = config.auth.get("password", "")

    if not password:
        raise ValueError("Invalid Trojan password")

    network = config.transport.get("type", "tcp") or "tcp"
    security = config.tls.get("security", "tls") or "tls"

    stream_settings = {
        "network": network,
        "security": security,
    }

    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": config.transport.get("path", "/") or "/",
            "headers": {}
        }

        host = config.transport.get("host", "")
        if host:
            stream_settings["wsSettings"]["headers"]["Host"] = host

    elif network == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": config.transport.get("service_name", "") or ""
        }

    elif network == "tcp":
        header_type = config.transport.get("header_type", "")

        if header_type == "http":
            stream_settings["tcpSettings"] = {
                "header": {
                    "type": "http",
                    "request": {
                        "headers": {}
                    }
                }
            }

            host = config.transport.get("host", "")
            path = config.transport.get("path", "")

            if host:
                stream_settings["tcpSettings"]["header"]["request"]["headers"]["Host"] = [host]

            if path:
                stream_settings["tcpSettings"]["header"]["request"]["path"] = [path]

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": config.tls.get("sni", "") or config.server,
            "fingerprint": config.tls.get("fp", "chrome") or "chrome"
        }

        alpn = config.tls.get("alpn", "")
        if alpn:
            stream_settings["tlsSettings"]["alpn"] = [
                item.strip()
                for item in alpn.split(",")
                if item.strip()
            ]

    elif security == "reality":
        public_key = config.tls.get("pbk", "")
        if not public_key:
            raise ValueError("Invalid Trojan REALITY public key")

        stream_settings["realitySettings"] = {
            "serverName": config.tls.get("sni", "") or config.server,
            "fingerprint": config.tls.get("fp", "chrome") or "chrome",
            "publicKey": public_key,
            "shortId": config.tls.get("sid", "") or "",
            "spiderX": config.tls.get("spx", "/") or "/"
        }

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
                "protocol": "trojan",
                "settings": {
                    "servers": [
                        {
                            "address": config.server,
                            "port": config.port,
                            "password": password
                        }
                    ]
                },
                "streamSettings": stream_settings
            }
        ]
    }

    return xray_config