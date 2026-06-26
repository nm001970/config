from worker.parsers.vless_parser import parse as parse_vless
from worker.parsers.vmess_parser import parse as parse_vmess
from worker.parsers.ss_parser import parse as parse_ss
from worker.parsers.trojan_parser import parse as parse_trojan


def parse_config(config_url: str):

    if config_url.startswith("vless://"):
        return parse_vless(config_url)

    if config_url.startswith("vmess://"):
        return parse_vmess(config_url)

    if config_url.startswith("ss://"):
        return parse_ss(config_url)

    if config_url.startswith("trojan://"):
        return parse_trojan(config_url)

    raise ValueError(
        "Unsupported protocol"
    )