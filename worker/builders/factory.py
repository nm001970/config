from worker.builders.vless_builder import build as build_vless

def build_xray_config(config, socks_port):
    if config.protocol == "vless":
        return build_vless(config, socks_port)
    raise ValueError(
        f"Unsupported protocol:"
        f"{config.protocol}"
    )