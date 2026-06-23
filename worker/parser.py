SUPPORTED_PROTOCOLS= (
        "vmess://",
        "vless://",
        "trojan://",
        "ss://"
)

def parser_confifgs(raw_text):

    configs= []

    for line in raw_text.splitlines():
        line = line.strip()

        if not line:
            continue

        for protocol in SUPPORTED_PROTOCOLS:
            if line.startswith(protocol):
                configs.append(
                    {"protocol": protocol.replace(
                        "://",
                        ""
                    ),
                    "config": line}
                )
                break
    return configs

def parse_configs(raw_text):
    return parser_confifgs(raw_text)