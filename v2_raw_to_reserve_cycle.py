import argparse
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from database.repositories.config_repository import (
    build_smart_raw_to_reserve_plan,
    claim_config_ids_for_raw_to_reserve,
    recover_stuck_raw_to_reserve_claims,
    release_raw_to_reserve_claim,
)
from database.repositories.pool_repository import (
    get_reserve_count,
    recover_stuck_gate_claims,
    release_gate_testing,
)
from delivery_validate_public_configs import get_direct_ip
from v2_live_worker import process_config


DEFAULT_BATCH_SIZE = 10
DEFAULT_NEWEST_RATIO = 0.60
DEFAULT_ALIVE_RATIO = 0.25
DEFAULT_RETRY_RATIO = 0.15

DEFAULT_IDLE_SLEEP_SECONDS = 15.0
DEFAULT_CYCLE_SLEEP_SECONDS = 2.0
DEFAULT_ERROR_SLEEP_SECONDS = 30.0

DEFAULT_RECOVER_STUCK = True
DEFAULT_STUCK_MINUTES = 30.0
DEFAULT_RECOVER_LIMIT = 100

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"


def normalize_operator(operator):
    value = str(operator or "").strip().lower()

    if not value:
        raise ValueError("operator is required")

    return value


def normalize_ratios(
    newest_ratio,
    alive_ratio,
    retry_ratio,
):
    values = {
        "newest_ratio": float(newest_ratio),
        "alive_ratio": float(alive_ratio),
        "retry_ratio": float(retry_ratio),
    }

    if any(value < 0 for value in values.values()):
        raise ValueError(
            "Smart selection ratios must be greater than or equal to zero"
        )

    if sum(values.values()) <= 0:
        raise ValueError(
            "At least one Smart selection ratio must be greater than zero"
        )

    return values


def resolve_output_path(path_value):
    path = Path(path_value)

    if not path.is_absolute():
        path = PROJECT_ROOT / path

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    return path


def resolve_runtime_paths(
    operator,
    plan_output=None,
    ids_output=None,
):
    operator = normalize_operator(operator)

    if plan_output is None:
        plan_output = (
            RUNTIME_DIR
            / f"raw_to_reserve_{operator}_plan.json"
        )

    if ids_output is None:
        ids_output = (
            RUNTIME_DIR
            / f"raw_to_reserve_{operator}_ids.json"
        )

    return (
        resolve_output_path(plan_output),
        resolve_output_path(ids_output),
    )


def write_json_atomic(path_value, payload):
    path = resolve_output_path(path_value)
    temporary_path = path.with_name(
        f".{path.name}.tmp"
    )

    with open(
        temporary_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            payload,
            file,
            ensure_ascii=False,
            indent=2,
        )
        file.flush()
        os.fsync(file.fileno())

    os.replace(
        temporary_path,
        path,
    )

    return path


@contextmanager
def single_instance_lock(operator):
    """
    Prevent two Raw -> Reserve cycles for the same operator from
    running at the same time.
    """
    operator = normalize_operator(operator)

    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock_path = (
        RUNTIME_DIR
        / f"raw_to_reserve_{operator}.lock"
    )

    lock_file = open(
        lock_path,
        "a+b",
    )
    lock_acquired = False

    try:
        lock_file.seek(0, 2)

        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()

        lock_file.seek(0)

        try:
            import msvcrt

            msvcrt.locking(
                lock_file.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )

        except ImportError:
            import fcntl

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX
                | fcntl.LOCK_NB,
            )

        except OSError as exception:
            raise RuntimeError(
                "Another Raw -> Reserve cycle is already running for "
                f"operator={operator}"
            ) from exception

        lock_acquired = True
        yield lock_path

    finally:
        if lock_acquired:
            try:
                lock_file.seek(0)

                try:
                    import msvcrt

                    msvcrt.locking(
                        lock_file.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )

                except ImportError:
                    import fcntl

                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )

                except OSError:
                    pass

            finally:
                lock_file.close()

        else:
            lock_file.close()


def add_result_count(
    result_counts,
    result,
):
    key = str(
        result or "unknown"
    )

    result_counts[key] = (
        result_counts.get(key, 0) + 1
    )


def release_item_claims(
    config_id,
    operator,
    reason,
):
    """
    Release unfinished Raw and Gate claims independently.

    The repository functions revalidate current DB state, so progressed
    Config or ConfigPool rows are not overwritten.
    """
    summary = {
        "raw_released": False,
        "gate_released": False,
        "raw_release_error": None,
        "gate_release_error": None,
    }

    try:
        raw_result = (
            release_raw_to_reserve_claim(
                config_id=config_id,
                operator=operator,
                reason=reason,
            )
        )
        summary["raw_released"] = bool(
            raw_result.get("released")
        )

        if summary["raw_released"]:
            print(
                "[RAW CLAIM RELEASED] "
                f"ID={config_id} STATUS=not_tested"
            )
        else:
            print(
                "[RAW CLAIM NOT RELEASED] "
                f"ID={config_id} "
                "REASON=status_progressed_or_pool_owned"
            )

    except Exception as exception:
        summary["raw_release_error"] = repr(
            exception
        )

        print(
            "[RAW CLAIM RELEASE ERROR] "
            f"ID={config_id} "
            f"ERROR={repr(exception)}"
        )

    try:
        gate_result = release_gate_testing(
            config_id=config_id,
            operator=operator,
            reason=reason,
        )
        summary["gate_released"] = bool(
            gate_result.get("released")
        )

        if summary["gate_released"]:
            print(
                "[GATE CLAIM RELEASED] "
                f"ID={config_id} GATE_STATUS=not_tested"
            )
        else:
            print(
                "[GATE CLAIM NOT RELEASED] "
                f"ID={config_id} "
                "REASON=gate_progressed_or_pool_owned"
            )

    except Exception as exception:
        summary["gate_release_error"] = repr(
            exception
        )

        print(
            "[GATE CLAIM RELEASE ERROR] "
            f"ID={config_id} "
            f"ERROR={repr(exception)}"
        )

    return summary


def process_smart_plan(
    plan,
    operator,
    direct_ip,
):
    """
    Process exactly the IDs selected by one Smart plan.

    Each ID is claimed immediately before processing. The full batch is
    never marked as testing in advance.
    """
    operator = normalize_operator(operator)

    selected_ids = [
        int(config_id)
        for config_id
        in (plan.get("selected_ids") or [])
    ]

    result_counts = {}
    claimed_ids = []
    skipped_claim_ids = []
    processed_ids = []
    release_summaries = {}

    for position, config_id in enumerate(
        selected_ids,
        start=1,
    ):
        print()
        print("-" * 70)
        print(
            "[SMART ITEM] "
            f"{position}/{len(selected_ids)} "
            f"ID={config_id}"
        )
        print("-" * 70)

        claimed_now = (
            claim_config_ids_for_raw_to_reserve(
                [config_id],
                operator=operator,
            )
            or []
        )
        claimed_now = {
            int(value)
            for value in claimed_now
        }

        if config_id not in claimed_now:
            skipped_claim_ids.append(
                config_id
            )
            add_result_count(
                result_counts,
                "claim_skipped",
            )

            print(
                "[SMART CLAIM SKIPPED] "
                f"ID={config_id} "
                "REASON=no_longer_eligible"
            )
            continue

        claimed_ids.append(config_id)

        try:
            result = process_config(
                config_id=config_id,
                operator=operator,
                direct_ip=direct_ip,
            )

        except KeyboardInterrupt:
            print()
            print(
                "[RAW TO RESERVE] "
                "STOPPED BY USER"
            )

            release_summaries[str(config_id)] = (
                release_item_claims(
                    config_id=config_id,
                    operator=operator,
                    reason=(
                        "process_config_keyboard_interrupt"
                    ),
                )
            )
            raise

        except Exception as exception:
            result = "worker_error"

            print(
                "[RAW TO RESERVE ITEM ERROR] "
                f"ID={config_id} "
                f"ERROR={repr(exception)}"
            )

            release_summaries[str(config_id)] = (
                release_item_claims(
                    config_id=config_id,
                    operator=operator,
                    reason=(
                        "process_config_exception:"
                        f"{type(exception).__name__}"
                    ),
                )
            )

        processed_ids.append(config_id)
        add_result_count(
            result_counts,
            result,
        )

        print(
            "[SMART ITEM RESULT] "
            f"ID={config_id} "
            f"RESULT={result}"
        )

    return {
        "selected_total": len(
            selected_ids
        ),
        "claimed_total": len(
            claimed_ids
        ),
        "processed_total": len(
            processed_ids
        ),
        "claim_skipped_total": len(
            skipped_claim_ids
        ),
        "claimed_ids": claimed_ids,
        "processed_ids": processed_ids,
        "claim_skipped_ids": (
            skipped_claim_ids
        ),
        "results": result_counts,
        "claim_releases": release_summaries,
    }


def run_one_smart_batch(
    operator,
    batch_size,
    newest_ratio,
    alive_ratio,
    retry_ratio,
    plan_output,
    ids_output,
    cycle_number,
):
    operator = normalize_operator(operator)

    plan = build_smart_raw_to_reserve_plan(
        operator=operator,
        limit=batch_size,
        newest_ratio=newest_ratio,
        alive_ratio=alive_ratio,
        retry_ratio=retry_ratio,
    )

    plan_payload = {
        **dict(plan),
        "created_at": datetime.now().isoformat(),
        "cycle_number": cycle_number,
        "operator": operator,
    }

    plan_path = write_json_atomic(
        plan_output,
        plan_payload,
    )

    print(
        "[SMART PLAN WRITTEN]",
        plan_path,
    )
    print(
        "[SMART PLAN] "
        f"REQUESTED={plan.get('requested_limit')} "
        f"SELECTED={plan.get('selected_total')}"
    )

    for bucket_name, summary in (
        plan.get("bucket_summary") or {}
    ).items():
        print(
            "[SMART BUCKET] "
            f"{bucket_name} "
            f"COUNT={summary.get('count')} "
            f"STATUS={summary.get('status')} "
            f"PROTOCOL={summary.get('protocol')}"
        )

    selected_ids = (
        plan.get("selected_ids") or []
    )

    if selected_ids:
        direct_ip = get_direct_ip()

        print(
            "[DIRECT IP]",
            direct_ip or "UNKNOWN",
        )

        execution = process_smart_plan(
            plan=plan,
            operator=operator,
            direct_ip=direct_ip,
        )

    else:
        execution = {
            "selected_total": 0,
            "claimed_total": 0,
            "processed_total": 0,
            "claim_skipped_total": 0,
            "claimed_ids": [],
            "processed_ids": [],
            "claim_skipped_ids": [],
            "results": {},
            "claim_releases": {},
        }

    ids_payload = {
        "created_at": datetime.now().isoformat(),
        "cycle_number": cycle_number,
        "operator": operator,
        **execution,
        "ids": execution["claimed_ids"],
    }

    ids_path = write_json_atomic(
        ids_output,
        ids_payload,
    )

    print(
        "[SMART IDS WRITTEN]",
        ids_path,
    )

    return {
        "plan": plan,
        "execution": execution,
    }


def run_startup_recovery(
    operator,
    enabled,
    stale_minutes,
    limit,
):
    """
    Recover stale Raw and Gate claims once after acquiring the lock and
    before building the first Smart plan.
    """
    operator = normalize_operator(operator)

    if not enabled:
        print(
            "[STUCK RECOVERY SKIPPED] "
            "REASON=disabled"
        )

        return {
            "operator": operator,
            "enabled": False,
            "raw_claims": None,
            "gate_claims": None,
            "recovered_total": 0,
        }

    print()
    print("=" * 70)
    print("RAW TO RESERVE STARTUP RECOVERY")
    print("OPERATOR:", operator)
    print("STALE MINUTES:", stale_minutes)
    print("RECOVERY LIMIT:", limit)
    print("=" * 70)

    raw_result = (
        recover_stuck_raw_to_reserve_claims(
            operator=operator,
            stale_minutes=stale_minutes,
            limit=limit,
        )
    )

    gate_result = recover_stuck_gate_claims(
        operator=operator,
        stale_minutes=stale_minutes,
        limit=limit,
    )

    print(
        "[RAW CLAIM RECOVERY RESULT] "
        f"CANDIDATES={raw_result.get('candidate_total')} "
        f"RECOVERED={raw_result.get('recovered_total')} "
        f"IDS={raw_result.get('recovered_ids')}"
    )

    print(
        "[GATE CLAIM RECOVERY RESULT] "
        f"CANDIDATES={gate_result.get('candidate_total')} "
        f"RECOVERED={gate_result.get('recovered_total')} "
        f"IDS={gate_result.get('recovered_ids')}"
    )

    return {
        "operator": operator,
        "enabled": True,
        "raw_claims": raw_result,
        "gate_claims": gate_result,
        "recovered_total": (
            int(
                raw_result.get(
                    "recovered_total",
                    0,
                )
            )
            + int(
                gate_result.get(
                    "recovered_total",
                    0,
                )
            )
        ),
    }


def run_cycle(
    operator,
    batch_size=DEFAULT_BATCH_SIZE,
    newest_ratio=DEFAULT_NEWEST_RATIO,
    alive_ratio=DEFAULT_ALIVE_RATIO,
    retry_ratio=DEFAULT_RETRY_RATIO,
    plan_output=None,
    ids_output=None,
    idle_sleep_seconds=(
        DEFAULT_IDLE_SLEEP_SECONDS
    ),
    cycle_sleep_seconds=(
        DEFAULT_CYCLE_SLEEP_SECONDS
    ),
    error_sleep_seconds=(
        DEFAULT_ERROR_SLEEP_SECONDS
    ),
    recover_stuck=DEFAULT_RECOVER_STUCK,
    stuck_minutes=DEFAULT_STUCK_MINUTES,
    recover_limit=DEFAULT_RECOVER_LIMIT,
    once=False,
):
    operator = normalize_operator(operator)

    ratios = normalize_ratios(
        newest_ratio=newest_ratio,
        alive_ratio=alive_ratio,
        retry_ratio=retry_ratio,
    )

    batch_size = max(
        int(batch_size),
        1,
    )
    idle_sleep_seconds = max(
        float(idle_sleep_seconds),
        0.0,
    )
    cycle_sleep_seconds = max(
        float(cycle_sleep_seconds),
        0.0,
    )
    error_sleep_seconds = max(
        float(error_sleep_seconds),
        0.0,
    )
    stuck_minutes = max(
        float(stuck_minutes),
        1.0,
    )
    recover_limit = max(
        int(recover_limit),
        1,
    )

    plan_output, ids_output = (
        resolve_runtime_paths(
            operator=operator,
            plan_output=plan_output,
            ids_output=ids_output,
        )
    )

    print("=" * 70)
    print(
        "INDEPENDENT SMART "
        "RAW TO RESERVE CYCLE"
    )
    print("OPERATOR:", operator)
    print("BATCH SIZE:", batch_size)
    print(
        "NEWEST RATIO:",
        ratios["newest_ratio"],
    )
    print(
        "ALIVE RATIO:",
        ratios["alive_ratio"],
    )
    print(
        "RETRY RATIO:",
        ratios["retry_ratio"],
    )
    print("PLAN OUTPUT:", plan_output)
    print("IDS OUTPUT:", ids_output)
    print("RECOVER STUCK:", recover_stuck)
    print("STUCK MINUTES:", stuck_minutes)
    print("RECOVER LIMIT:", recover_limit)
    print("ONCE:", once)
    print("=" * 70)

    cycle_number = 0

    with single_instance_lock(
        operator
    ) as lock_path:
        print(
            "LOCK ACQUIRED:",
            lock_path,
        )

        run_startup_recovery(
            operator=operator,
            enabled=recover_stuck,
            stale_minutes=stuck_minutes,
            limit=recover_limit,
        )

        while True:
            cycle_number += 1

            print()
            print("#" * 70)
            print(
                "SMART RAW TO RESERVE "
                f"CYCLE: {cycle_number}"
            )
            print("#" * 70)

            try:
                cycle_result = (
                    run_one_smart_batch(
                        operator=operator,
                        batch_size=batch_size,
                        newest_ratio=(
                            ratios["newest_ratio"]
                        ),
                        alive_ratio=(
                            ratios["alive_ratio"]
                        ),
                        retry_ratio=(
                            ratios["retry_ratio"]
                        ),
                        plan_output=plan_output,
                        ids_output=ids_output,
                        cycle_number=cycle_number,
                    )
                )

            except KeyboardInterrupt:
                print()
                print(
                    "[RAW TO RESERVE] "
                    "STOPPED BY USER"
                )
                break

            except Exception as exception:
                print(
                    "[SMART RAW TO RESERVE "
                    "CYCLE ERROR] "
                    f"CYCLE={cycle_number} "
                    f"ERROR={repr(exception)}"
                )

                if once:
                    raise

                time.sleep(
                    error_sleep_seconds
                )
                continue

            execution = (
                cycle_result["execution"]
            )

            selected_total = int(
                execution.get(
                    "selected_total",
                    0,
                )
            )
            processed_total = int(
                execution.get(
                    "processed_total",
                    0,
                )
            )

            reserve_count = (
                get_reserve_count(
                    operator
                )
            )

            print(
                "[SMART CYCLE RESULT] "
                f"CYCLE={cycle_number} "
                f"SELECTED={selected_total} "
                f"CLAIMED={execution.get('claimed_total')} "
                f"PROCESSED={processed_total} "
                f"RESULTS={execution.get('results')} "
                f"RESERVE_READY={reserve_count}"
            )

            if once:
                break

            if selected_total == 0:
                print(
                    "[SMART RAW IDLE] "
                    "NO ELIGIBLE CANDIDATE"
                )
                time.sleep(
                    idle_sleep_seconds
                )
                continue

            time.sleep(
                cycle_sleep_seconds
            )

    print()
    print("=" * 70)
    print(
        "SMART RAW TO RESERVE "
        "CYCLE FINISHED"
    )
    print(
        "TOTAL CYCLES:",
        cycle_number,
    )
    print("=" * 70)


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Run the independent Smart "
            "Raw -> Reserve pipeline."
        )
    )

    parser.add_argument(
        "--operator",
        required=True,
        help=(
            "Operator name, for example "
            "irancell or mci."
        ),
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
    )

    parser.add_argument(
        "--newest-ratio",
        type=float,
        default=DEFAULT_NEWEST_RATIO,
    )

    parser.add_argument(
        "--alive-ratio",
        type=float,
        default=DEFAULT_ALIVE_RATIO,
    )

    parser.add_argument(
        "--retry-ratio",
        type=float,
        default=DEFAULT_RETRY_RATIO,
    )

    parser.add_argument(
        "--plan-output",
        default=None,
        help=(
            "Optional Smart plan path. Default: "
            ".runtime/raw_to_reserve_<operator>_plan.json"
        ),
    )

    parser.add_argument(
        "--ids-output",
        default=None,
        help=(
            "Optional execution path. Default: "
            ".runtime/raw_to_reserve_<operator>_ids.json"
        ),
    )

    parser.add_argument(
        "--idle-sleep",
        type=float,
        default=DEFAULT_IDLE_SLEEP_SECONDS,
    )

    parser.add_argument(
        "--cycle-sleep",
        type=float,
        default=DEFAULT_CYCLE_SLEEP_SECONDS,
    )

    parser.add_argument(
        "--error-sleep",
        type=float,
        default=DEFAULT_ERROR_SLEEP_SECONDS,
    )

    parser.add_argument(
        "--recover-stuck",
        dest="recover_stuck",
        action="store_true",
        default=DEFAULT_RECOVER_STUCK,
        help=(
            "Recover stale Raw and Gate claims "
            "at process startup."
        ),
    )

    parser.add_argument(
        "--no-recover-stuck",
        dest="recover_stuck",
        action="store_false",
    )

    parser.add_argument(
        "--stuck-minutes",
        type=float,
        default=DEFAULT_STUCK_MINUTES,
    )

    parser.add_argument(
        "--recover-limit",
        type=int,
        default=DEFAULT_RECOVER_LIMIT,
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one Smart batch and exit."
        ),
    )

    return parser


def main():
    args = build_parser().parse_args()

    run_cycle(
        operator=args.operator,
        batch_size=args.batch_size,
        newest_ratio=args.newest_ratio,
        alive_ratio=args.alive_ratio,
        retry_ratio=args.retry_ratio,
        plan_output=args.plan_output,
        ids_output=args.ids_output,
        idle_sleep_seconds=(
            args.idle_sleep
        ),
        cycle_sleep_seconds=(
            args.cycle_sleep
        ),
        error_sleep_seconds=(
            args.error_sleep
        ),
        recover_stuck=(
            args.recover_stuck
        ),
        stuck_minutes=(
            args.stuck_minutes
        ),
        recover_limit=(
            args.recover_limit
        ),
        once=args.once,
    )


if __name__ == "__main__":
    main()