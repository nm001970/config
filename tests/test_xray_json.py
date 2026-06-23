import json

from worker.vless_parser import parse_vless
from worker.xray_config_generator import (
    generate_vless_config
)

url= "vless://cabbfe13-038b-4dbb-9c45-5079c829abfa@167.82.0.1:80?type=ws&host=max-gb1.global.ssl.fastly.net&path=/"


data = parse_vless(url)

config = generate_vless_config(
    data
)

with open(
    "temp_config.json",
    "w",
    encoding="utf-8"
) as f:

    json.dump(
        config,
        f,
        indent=4
    )

print("JSON Created")