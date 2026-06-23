import json
from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config

url = (
    "vless://cabbfe13-038b-4dbb-9c45-5079c829abfa"
    "@167.82.0.1:80"
    "?type=ws"
    "&host=max-gb1.global.ssl.fastly.net"
    "&path=/"
)

config= parse_config(url)
xray_json= build_xray_config(config, 10808)

with open(
    "temp_config.json",
    "w",
    encoding="utf-8"
)as f:
    json.dump(
        xray_json,
        f,
        indent=4
    )

print(
    "temp_config.json created"
)