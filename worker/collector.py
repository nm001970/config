import requests

URL= (
    "https://raw.githubusercontent.com/SoliSpirit/v2ray-configs/refs/heads/main/all_configs.txt"
)

def download_configs():

    response= requests.get(
        URL,
        timeout= 60
    )

    response.raise_for_status()

    return response.text