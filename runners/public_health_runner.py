import json
import os
from pathlib import Path

from runners.quality_runner import run_quality_ids


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
PUBLIC_CONFIGS_PATH = PROJECT_ROOT / PUBLIC_CONFIGS_FILE

HOT_HEALTH_WORKERS = int(os.getenv("HOT_HEALTH_WORKERS", "3"))
RESERVE_HEALTH_WORKERS = int(os.getenv("RESERVE_HEALTH_WORKERS", "3"))
RESERVE_HEALTH_LIMIT = int(os.getenv("RESERVE_HEALTH_LIMIT", "10"))


def load_public_payload():
    if not PUBLIC_CONFIGS_PATH.exists():
        raise FileNotFoundError(f"Public configs file not found: {PUBLIC_CONFIGS_PATH}")

    with open(PUBLIC_CONFIGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_source_ids(items):
    ids = []

    for item in items or []:
        source_id = item.get("source_id")

        if source_id is None:
            continue

        try:
            ids.append(int(source_id))
        except Exception:
            continue

    return ids


def load_hot_ids():
    payload = load_public_payload()
    return extract_source_ids(payload.get("hot", []))


def load_reserve_ids(limit=None):
    payload = load_public_payload()
    ids = extract_source_ids(payload.get("reserve", []))

    if limit is not None:
        return ids[:limit]

    return ids


def run_hot_health_scan():
    ids = load_hot_ids()

    print("\n" + "=" * 40)
    print("HOT HEALTH SCAN")
    print("=" * 40)
    print("HOT IDS:", ids)

    return run_quality_ids(
        ids,
        workers=HOT_HEALTH_WORKERS,
        title="HOT HEALTH"
    )


def run_reserve_health_scan(limit=None):
    if limit is None:
        limit = RESERVE_HEALTH_LIMIT

    ids = load_reserve_ids(limit=limit)

    print("\n" + "=" * 40)
    print("RESERVE HEALTH SCAN")
    print("=" * 40)
    print("RESERVE IDS:", ids)
    print("LIMIT:", limit)

    return run_quality_ids(
        ids,
        workers=RESERVE_HEALTH_WORKERS,
        title="RESERVE HEALTH"
    )


def run_public_health_scan():
    hot_tested = run_hot_health_scan()
    reserve_tested = run_reserve_health_scan()

    total = hot_tested + reserve_tested

    print("\n" + "=" * 40)
    print("PUBLIC HEALTH DONE")
    print("=" * 40)
    print("HOT TESTED:", hot_tested)
    print("RESERVE TESTED:", reserve_tested)
    print("TOTAL TESTED:", total)

    return total