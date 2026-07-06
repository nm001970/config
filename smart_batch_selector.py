import argparse
import json
import os
from pathlib import Path

from database.repositories.config_repository import (
    build_smart_connectivity_plan,
    claim_config_ids_for_connectivity,
)


PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_output_path(path_text):
    path = Path(path_text)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    return path


def write_json(path, payload):
    path = resolve_output_path(path)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path


def print_summary(plan):
    print("=" * 70)
    print("SMART BATCH SELECTOR")
    print("=" * 70)
    print("REQUESTED LIMIT:", plan["requested_limit"])
    print("SELECTED TOTAL:", plan["selected_total"])
    print("MIN ID:", plan["selected_min_id"])
    print("MAX ID:", plan["selected_max_id"])
    print()

    for name, summary in plan["bucket_summary"].items():
        print(f"[{name}]")
        print("  count:", summary["count"])
        print("  status:", summary["status"])
        print("  protocol:", summary["protocol"])
        print()

    print("[overall]")
    print("  count:", plan["overall_summary"]["count"])
    print("  status:", plan["overall_summary"]["status"])
    print("  protocol:", plan["overall_summary"]["protocol"])


def main():
    parser = argparse.ArgumentParser(
        description="Smart batch selector for MN Proxy connectivity testing."
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("SMART_BATCH_LIMIT", "3500")),
    )

    parser.add_argument(
        "--newest-ratio",
        type=float,
        default=float(os.getenv("SMART_BATCH_NEWEST_RATIO", "0.60")),
    )

    parser.add_argument(
        "--alive-ratio",
        type=float,
        default=float(os.getenv("SMART_BATCH_ALIVE_RATIO", "0.25")),
    )

    parser.add_argument(
        "--retry-ratio",
        type=float,
        default=float(os.getenv("SMART_BATCH_RETRY_RATIO", "0.15")),
    )

    parser.add_argument(
        "--plan-output",
        default=os.getenv("SMART_BATCH_PLAN_FILE", "smart_batch_plan.json"),
    )

    parser.add_argument(
        "--ids-output",
        default=os.getenv("SMART_BATCH_IDS_FILE", "smart_batch_ids.json"),
    )

    parser.add_argument(
        "--write-ids",
        action="store_true",
        help="Write selected IDs to smart_batch_ids.json.",
    )

    parser.add_argument(
        "--claim",
        action="store_true",
        help="Mark selected IDs as testing. This modifies the database.",
    )

    args = parser.parse_args()

    plan = build_smart_connectivity_plan(
        limit=args.limit,
        newest_ratio=args.newest_ratio,
        alive_ratio=args.alive_ratio,
        retry_ratio=args.retry_ratio,
    )

    print_summary(plan)

    plan_path = write_json(args.plan_output, plan)
    print()
    print("PLAN WRITTEN:", plan_path)

    selected_ids = plan.get("selected_ids", [])

    if args.claim:
        claimed_ids = claim_config_ids_for_connectivity(selected_ids)
        ids_payload = {
            "created_at": plan["created_at"],
            "requested_limit": plan["requested_limit"],
            "selected_total": plan["selected_total"],
            "claimed_total": len(claimed_ids),
            "ids": claimed_ids,
        }

        ids_path = write_json(args.ids_output, ids_payload)

        print("CLAIMED TOTAL:", len(claimed_ids))
        print("IDS WRITTEN:", ids_path)
        print("RESULT: DATABASE MODIFIED - IDS CLAIMED AS testing")

    elif args.write_ids:
        ids_payload = {
            "created_at": plan["created_at"],
            "requested_limit": plan["requested_limit"],
            "selected_total": plan["selected_total"],
            "claimed_total": 0,
            "ids": selected_ids,
        }

        ids_path = write_json(args.ids_output, ids_payload)

        print("IDS WRITTEN:", ids_path)
        print("RESULT: PREVIEW IDS WRITTEN - DATABASE WAS NOT MODIFIED")

    else:
        print("RESULT: PREVIEW ONLY - DATABASE WAS NOT MODIFIED")


if __name__ == "__main__":
    main()