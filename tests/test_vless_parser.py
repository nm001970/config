from worker.vless_parser import parse_vless


url = (
    "vless://cabbfe13-038b-4dbb-9c45-5079c829abfa"
    "@167.82.0.1:80"
    "?type=ws"
    "&host=max-gb1.global.ssl.fastly.net"
    "&path=/"
)

result = parse_vless(url)

print(result)