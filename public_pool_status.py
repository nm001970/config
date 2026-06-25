import json
import os
from pathlib import Path

from database.db import SessionLocal
from database.models import Config


PROJECT_ROOT = Path(__file__).resolve().parent
PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
PUBLIC_CONFIGS_PATH = PROJECT_ROOT / PUBLIC_CONFIGS_FILE

MIN_HOT_COUNT = int(os.getenv("MIN_HOT_COUNT", "5"))
MIN_RESERVE_COUNT = int(os.getenv("MIN_RESERVE_COUNT", "30"))
MIN_QUALIFIED_COUNT = int(os.getenv("MIN_QUALIFIED_COUNT", "80"))


def load_public_payload():
    if not PUBLIC_CONFIGS_PATH.exists():
        return None

    with open(PUBLIC_CONFIGS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_public_counts():
    payload = load_public_payload()

    if not payload:
        return {
            "hot_count": 0,
            "reserve_count": 0,
            "generated_at": None,
        }

    hot = payload.get("hot") or []
    reserve = payload.get("reserve") or []

    return {
        "hot_count": len(hot),
        "reserve_count": len(reserve),
        "generated_at": payload.get("generated_at"),
    }


def get_db_counts():
    session = SessionLocal()

    try:
        total = session.query(Config).count()

        alive = (
            session.query(Config)
            .filter(Config.status == "alive")
            .count()
        )

        qualified = (
            session.query(Config)
            .filter(
                Config.status == "alive",
                Config.quality_status == "qualified",
            )
            .count()
        )

        rejected = (
            session.query(Config)
            .filter(Config.quality_status == "rejected")
            .count()
        )

        not_tested = (
            session.query(Config)
            .filter(
                Config.status == "alive",
                (
                    (Config.quality_status == None)
                    | (Config.quality_status == "not_tested")
                ),
            )
            .count()
        )

        return {
            "total": total,
            "alive": alive,
            "qualified": qualified,
            "rejected": rejected,
            "alive_not_quality_tested": not_tested,
        }

    finally:
        session.close()


def evaluate_status(public_counts, db_counts):
    problems = []

    if public_counts["hot_count"] < MIN_HOT_COUNT:
        problems.append(
            f"HOT_LOW: {public_counts['hot_count']} < {MIN_HOT_COUNT}"
        )

    if public_counts["reserve_count"] < MIN_RESERVE_COUNT:
        problems.append(
            f"RESERVE_LOW: {public_counts['reserve_count']} < {MIN_RESERVE_COUNT}"
        )

    if db_counts["qualified"] < MIN_QUALIFIED_COUNT:
        problems.append(
            f"QUALIFIED_LOW: {db_counts['qualified']} < {MIN_QUALIFIED_COUNT}"
        )

    return {
        "ok": len(problems) == 0,
        "problems": problems,
    }


def main():
    public_counts = get_public_counts()
    db_counts = get_db_counts()
    status = evaluate_status(public_counts, db_counts)

    print("=" * 40)
    print("PUBLIC POOL STATUS")
    print("=" * 40)

    print("PUBLIC:")
    print(public_counts)

    print("\nDATABASE:")
    print(db_counts)

    print("\nTHRESHOLDS:")
    print({
        "MIN_HOT_COUNT": MIN_HOT_COUNT,
        "MIN_RESERVE_COUNT": MIN_RESERVE_COUNT,
        "MIN_QUALIFIED_COUNT": MIN_QUALIFIED_COUNT,
    })

    print("\nSTATUS:")
    print(status)

    if status["ok"]:
        print("\nRESULT: PUBLIC POOL IS HEALTHY")
        return 0

    print("\nRESULT: PUBLIC POOL NEEDS REFRESH")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())