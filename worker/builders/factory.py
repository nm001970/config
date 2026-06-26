from worker.builders.vless_builder import build as build_vless
from worker.builders.vmess_builder import build as build_vmess


def build_xray_config(config, socks_port):
    if config.protocol == "vless":
        return build_vless(config, socks_port)

    if config.protocol == "vmess":
        return build_vmess(config, socks_port)

    raise ValueError(
        f"Unsupported protocol: {config.protocol}"
    )