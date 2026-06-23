def build(config, socks_port):

    user = {
        "id": config.auth["uuid"],
        "encryption": "none"
    }

    if config.auth.get("flow"):
        user["flow"] = config.auth["flow"]

    stream_settings = {
        "network": config.transport["type"],
        "security": config.tls["security"]
    }

    if config.transport["type"] == "ws":

        stream_settings["wsSettings"] = {
            "path": config.transport.get(
                "path",
                "/"
            ),
            "headers": {
                "Host": config.transport.get(
                    "host",
                    ""
                )
            }
        }

    if config.tls["security"] == "reality":

        stream_settings["realitySettings"] = {
            "serverName": config.tls.get(
                "sni",
                ""
            ),
            "fingerprint": config.tls.get(
                "fp",
                "chrome"
            ),
            "publicKey": config.tls.get(
                "pbk",
                ""
            ),
            "shortId": config.tls.get(
                "sid",
                ""
            ),
            "spiderX": config.tls.get(
                "spx",
                "/"
            )
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
                "protocol": "vless",

                "settings": {
                    "vnext": [
                        {
                            "address": config.server,
                            "port": config.port,

                            "users": [
                                user
                            ]
                        }
                    ]
                },

                "streamSettings": stream_settings
            }
        ]
    }

    return xray_config