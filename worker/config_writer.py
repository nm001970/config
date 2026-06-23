import json
import os


TEMP_DIR = "temp_configs"


def save_temp_config(config_dict, port):

    os.makedirs(TEMP_DIR, exist_ok=True)

    path = os.path.join(
        TEMP_DIR,
        f"temp_{port}.json"
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            config_dict,
            f,
            ensure_ascii=False,
            indent=2
        )

    return path


def delete_temp_config(path):

    if path and os.path.exists(path):
        os.remove(path)