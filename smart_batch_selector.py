import argparse
import json
import os
from pathlib import Path

from database.repositories.config_repository import (
    build_smart_connectivity_plan,
    build_smart_raw_to_reserve_plan,
    claim_config_ids_for_connectivity,
    claim_config_ids_for_raw_to_reserve,
)


PROJECT_ROOT = Path(__file__).resolve().parent

MODE_CONNECTIVITY = "connectivity"
MODE_RAW_TO_RESERVE = "raw_to_reserve"

SUPPORTED_MODES = [
    MODE_CONNECTIVITY,
    MODE_RAW_TO_RESERVE,
]


def resolve_output_path(path_text):
    path = Path(path_text)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def write_json(path, payload):
    path = resolve_output_path(path)

    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )

    return path


def normalize_operator(operator):
    value = str(
        operator or ""
    ).strip().lower()

    if not value:
        raise ValueError(
            "operator is required when "
            "mode=raw_to_reserve"
        )

    return value


def print_summary(plan):
    print("=" * 70)
    print("SMART BATCH SELECTOR")
    print("=" * 70)
    print(
        "MODE:",
        plan.get(
            "mode",
            MODE_CONNECTIVITY,
        ),
    )

    if plan.get("operator"):
        print(
            "OPERATOR:",
            plan["operator"],
        )

    print(
        "REQUESTED LIMIT:",
        plan["requested_limit"],
    )
    print(
        "SELECTED TOTAL:",
        plan["selected_total"],
    )
    print(
        "MIN ID:",
        plan["selected_min_id"],
    )
    print(
        "MAX ID:",
        plan["selected_max_id"],
    )

    bucket_limits = plan.get(
        "bucket_limits"
    )

    if bucket_limits:
        print()
        print("[bucket limits]")

        for name, value in (
            bucket_limits.items()
        ):
            print(
                f"  {name}:",
                value,
            )

    print()

    for name, summary in (
        plan["bucket_summary"].items()
    ):
        print(f"[{name}]")
        print(
            "  count:",
            summary["count"],
        )
        print(
            "  status:",
            summary["status"],
        )
        print(
            "  protocol:",
            summary["protocol"],
        )
        print()

    print("[overall]")
    print(
        "  count:",
        plan[
            "overall_summary"
        ]["count"],
    )
    print(
        "  status:",
        plan[
            "overall_summary"
        ]["status"],
    )
    print(
        "  protocol:",
        plan[
            "overall_summary"
        ]["protocol"],
    )


def build_plan(args):
    if args.mode == MODE_CONNECTIVITY:
        return build_smart_connectivity_plan(
            limit=args.limit,
            newest_ratio=args.newest_ratio,
            alive_ratio=args.alive_ratio,
            retry_ratio=args.retry_ratio,
        )

    if args.mode == MODE_RAW_TO_RESERVE:
        operator = normalize_operator(
            args.operator
        )

        return build_smart_raw_to_reserve_plan(
            operator=operator,
            limit=args.limit,
            newest_ratio=args.newest_ratio,
            alive_ratio=args.alive_ratio,
            retry_ratio=args.retry_ratio,
        )

    raise ValueError(
        f"unsupported mode: {args.mode}"
    )


def claim_selected_ids(
    *,
    mode,
    selected_ids,
    operator,
):
    if mode == MODE_CONNECTIVITY:
        return (
            claim_config_ids_for_connectivity(
                selected_ids
            )
        )

    if mode == MODE_RAW_TO_RESERVE:
        return (
            claim_config_ids_for_raw_to_reserve(
                selected_ids,
                operator=normalize_operator(
                    operator
                ),
            )
        )

    raise ValueError(
        f"unsupported mode: {mode}"
    )


def build_ids_payload(
    *,
    plan,
    claimed_ids,
):
    return {
        "created_at": plan["created_at"],
        "mode": plan.get(
            "mode",
            MODE_CONNECTIVITY,
        ),
        "operator": plan.get(
            "operator"
        ),
        "requested_limit": plan[
            "requested_limit"
        ],
        "selected_total": plan[
            "selected_total"
        ],
        "claimed_total": len(
            claimed_ids
        ),
        "ids": list(claimed_ids),
    }


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Smart batch selector for "
            "connectivity or Raw -> Reserve."
        )
    )

    parser.add_argument(
        "--mode",
        choices=SUPPORTED_MODES,
        default=os.getenv(
            "SMART_BATCH_MODE",
            MODE_CONNECTIVITY,
        ),
        help=(
            "connectivity keeps the legacy "
            "selector behavior; raw_to_reserve "
            "excludes Pool-owned configs."
        ),
    )

    parser.add_argument(
        "--operator",
        default=os.getenv(
            "SMART_BATCH_OPERATOR",
            os.getenv(
                "PUBLIC_POOL_OPERATOR",
                "",
            ),
        ),
        help=(
            "Required only for "
            "mode=raw_to_reserve."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=int(
            os.getenv(
                "SMART_BATCH_LIMIT",
                "3500",
            )
        ),
    )

    parser.add_argument(
        "--newest-ratio",
        type=float,
        default=float(
            os.getenv(
                "SMART_BATCH_NEWEST_RATIO",
                "0.60",
            )
        ),
    )

    parser.add_argument(
        "--alive-ratio",
        type=float,
        default=float(
            os.getenv(
                "SMART_BATCH_ALIVE_RATIO",
                "0.25",
            )
        ),
    )

    parser.add_argument(
        "--retry-ratio",
        type=float,
        default=float(
            os.getenv(
                "SMART_BATCH_RETRY_RATIO",
                "0.15",
            )
        ),
    )

    parser.add_argument(
        "--plan-output",
        default=os.getenv(
            "SMART_BATCH_PLAN_FILE",
            "smart_batch_plan.json",
        ),
    )

    parser.add_argument(
        "--ids-output",
        default=os.getenv(
            "SMART_BATCH_IDS_FILE",
            "smart_batch_ids.json",
        ),
    )

    parser.add_argument(
        "--write-ids",
        action="store_true",
        help=(
            "Write selected IDs without "
            "modifying the database."
        ),
    )

    parser.add_argument(
        "--claim",
        action="store_true",
        help=(
            "Claim selected IDs as testing. "
            "This modifies the database."
        ),
    )

    return parser


def main():
    args = (
        build_parser()
        .parse_args()
    )

    plan = build_plan(args)

    print_summary(plan)

    plan_path = write_json(
        args.plan_output,
        plan,
    )

    print()
    print(
        "PLAN WRITTEN:",
        plan_path,
    )

    selected_ids = plan.get(
        "selected_ids",
        [],
    )

    if args.claim:
        claimed_ids = claim_selected_ids(
            mode=args.mode,
            selected_ids=selected_ids,
            operator=args.operator,
        )

        ids_payload = build_ids_payload(
            plan=plan,
            claimed_ids=claimed_ids,
        )

        ids_path = write_json(
            args.ids_output,
            ids_payload,
        )

        print(
            "CLAIMED TOTAL:",
            len(claimed_ids),
        )
        print(
            "IDS WRITTEN:",
            ids_path,
        )
        print(
            "RESULT: DATABASE MODIFIED - "
            "IDS CLAIMED AS testing"
        )

    elif args.write_ids:
        ids_payload = build_ids_payload(
            plan=plan,
            claimed_ids=selected_ids,
        )

        # Preview output must not report the IDs as claimed.
        ids_payload[
            "claimed_total"
        ] = 0

        ids_path = write_json(
            args.ids_output,
            ids_payload,
        )

        print(
            "IDS WRITTEN:",
            ids_path,
        )
        print(
            "RESULT: PREVIEW IDS WRITTEN - "
            "DATABASE WAS NOT MODIFIED"
        )

    else:
        print(
            "RESULT: PREVIEW ONLY - "
            "DATABASE WAS NOT MODIFIED"
        )


if __name__ == "__main__":
    main()