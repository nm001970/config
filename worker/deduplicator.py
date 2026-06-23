import hashlib

def generate_hash(config_text: str) -> str:
    return hashlib.sha256(
        config_text.encode("utf-8")
        ).hexdigest()

def add_hashes(configs):
    result =[]

    for item in configs:
        config_text = item["config"]

        result.append(
            {
                "protocol": item["protocol"],
                "config": config_text,
                "config_hash": generate_hash(config_text)
            }
        )
    return result

def remove_duplicates(configs):
    unique_configs=[]
    seen_hashes= set()

    for item in configs:
        config_text= item["config"]
        config_hash= generate_hash(config_text)
        if config_hash in seen_hashes:
            continue
        seen_hashes.add(config_hash)
        item["config_hash"]= config_hash
        unique_configs.append(item)

    return unique_configs