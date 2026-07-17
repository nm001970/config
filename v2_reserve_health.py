import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_

from database.db import SessionLocal
from database.models import Config, ConfigPool
from database.repositories.pool_repository import (
    mark_health_checking,
    normalize_operator,
    record_health_failure,
    record_health_success,
    recover_stuck_health_claims,
    release_health_checking,
)
from delivery_validate_public_configs import (
    get_direct_ip,
)
from v2_hot_manager import (
    build_failure_reason,
    long_validation_passed,
    run_long_validation,
)

from pool_maintenance_lock import (
    pool_maintenance_serialized,
)

DEFAULT_BATCH_LIMIT = 5

# Reserve items must continue meeting the same minimum Download policy
# used during Quality admission.
DEFAULT_MIN_DOWNLOAD_MBPS = 1.5
DEFAULT_FAILURE_LIMIT = 2

DEFAULT_HEALTHY_MIN_AGE_MINUTES = 60.0
DEFAULT_SUSPECT_MIN_AGE_MINUTES = 30.0

DEFAULT_RECOVER_STUCK = True
DEFAULT_STUCK_MINUTES = 15.0
DEFAULT_RECOVER_LIMIT = 100

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"

ELIGIBLE_HEALTH_STATUSES = {
    None,
    "unknown",
    "healthy",
    "suspect",
}


def safe_float(value):
    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def utc_now_naive():
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


@contextmanager
def single_instance_lock(operator):
    """
    Prevent two Reserve Health processes for the same operator from
    running simultaneously.
    """
    operator = normalize_operator(
        operator
    )

    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock_path = (
        RUNTIME_DIR
        / f"reserve_health_{operator}.lock"
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
                "Another Reserve Health process "
                "is already running for "
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


def candidate_min_age_minutes(
    candidate,
    healthy_min_age_minutes,
    suspect_min_age_minutes,
):
    health_status = candidate.get(
        "health_status"
    )

    if health_status == "suspect":
        return suspect_min_age_minutes

    return healthy_min_age_minutes


def health_check_due(
    candidate,
    healthy_min_age_minutes,
    suspect_min_age_minutes,
):
    health_status = candidate.get(
        "health_status"
    )

    if health_status not in ELIGIBLE_HEALTH_STATUSES:
        return False

    checked_at = candidate.get(
        "health_checked_at"
    )

    if checked_at is None:
        return True

    min_age_minutes = (
        candidate_min_age_minutes(
            candidate=candidate,
            healthy_min_age_minutes=(
                healthy_min_age_minutes
            ),
            suspect_min_age_minutes=(
                suspect_min_age_minutes
            ),
        )
    )

    return utc_now_naive() >= (
        checked_at
        + timedelta(
            minutes=max(
                float(min_age_minutes),
                0.0,
            )
        )
    )


def due_at_text(
    candidate,
    healthy_min_age_minutes,
    suspect_min_age_minutes,
):
    health_status = candidate.get(
        "health_status"
    )

    if health_status == "checking":
        return "CLAIMED"

    if health_status not in ELIGIBLE_HEALTH_STATUSES:
        return "NOT_ELIGIBLE"

    checked_at = candidate.get(
        "health_checked_at"
    )

    if checked_at is None:
        return "NOW"

    min_age_minutes = (
        candidate_min_age_minutes(
            candidate=candidate,
            healthy_min_age_minutes=(
                healthy_min_age_minutes
            ),
            suspect_min_age_minutes=(
                suspect_min_age_minutes
            ),
        )
    )

    return (
        checked_at
        + timedelta(
            minutes=max(
                float(min_age_minutes),
                0.0,
            )
        )
    ).isoformat()


def _row_to_candidate(row):
    return {
        "config_id": int(row.id),
        "protocol": row.protocol,
        "config_text": row.config_text,
        "status": row.status,
        "quality_status": (
            row.quality_status
        ),
        "health_status": (
            row.health_status
        ),
        "health_checked_at": (
            row.health_checked_at
        ),
        "health_fail_streak": int(
            row.health_fail_streak or 0
        ),
    }


def load_reserve_health_candidates(
    operator,
    limit,
    healthy_min_age_minutes,
    suspect_min_age_minutes,
):
    """
    Load accepted Reserve rows and select only due, unclaimed items.

    Active health_status=checking rows are never selected. Stale checking
    rows are recovered once at process startup.
    """
    operator = normalize_operator(
        operator
    )
    limit = max(
        int(limit),
        1,
    )

    session = SessionLocal()

    try:
        rows = (
            session.query(
                Config.id,
                Config.protocol,
                Config.config_text,
                Config.status,
                Config.quality_status,
                ConfigPool.health_status,
                ConfigPool.health_checked_at,
                ConfigPool.health_fail_streak,
            )
            .join(
                ConfigPool,
                ConfigPool.config_id
                == Config.id,
            )
            .filter(
                ConfigPool.operator
                == operator,
                ConfigPool.pool_status
                == "reserve",
                ConfigPool.gate_status
                == "accepted",
            )
            .order_by(
                ConfigPool.health_checked_at
                .is_(None)
                .desc(),
                ConfigPool.health_checked_at
                .asc(),
                ConfigPool.id.asc(),
            )
            .all()
        )

        candidates = [
            _row_to_candidate(row)
            for row in rows
        ]

        due_candidates = []
        skipped_not_due = []
        skipped_claimed = []
        skipped_invalid = []

        for candidate in candidates:
            health_status = candidate[
                "health_status"
            ]

            if health_status == "checking":
                skipped_claimed.append(
                    candidate
                )
                continue

            if (
                health_status
                not in ELIGIBLE_HEALTH_STATUSES
            ):
                skipped_invalid.append(
                    candidate
                )
                continue

            if health_check_due(
                candidate=candidate,
                healthy_min_age_minutes=(
                    healthy_min_age_minutes
                ),
                suspect_min_age_minutes=(
                    suspect_min_age_minutes
                ),
            ):
                if (
                    len(due_candidates)
                    < limit
                ):
                    due_candidates.append(
                        candidate
                    )
                continue

            skipped_not_due.append(
                candidate
            )

        return {
            "total": len(candidates),
            "due": due_candidates,
            "skipped_not_due": (
                skipped_not_due
            ),
            "skipped_claimed": (
                skipped_claimed
            ),
            "skipped_invalid": (
                skipped_invalid
            ),
        }

    finally:
        session.close()


def load_current_reserve_candidate(
    config_id,
    operator,
):
    """
    Revalidate Config and Pool state after acquiring the Health claim.
    """
    config_id = normalize_config_id(
        config_id
    )
    operator = normalize_operator(
        operator
    )

    session = SessionLocal()

    try:
        row = (
            session.query(
                Config.id,
                Config.protocol,
                Config.config_text,
                Config.status,
                Config.quality_status,
                ConfigPool.health_status,
                ConfigPool.health_checked_at,
                ConfigPool.health_fail_streak,
            )
            .join(
                ConfigPool,
                ConfigPool.config_id
                == Config.id,
            )
            .filter(
                Config.id == config_id,
                ConfigPool.operator
                == operator,
                ConfigPool.pool_status
                == "reserve",
                ConfigPool.gate_status
                == "accepted",
            )
            .first()
        )

        if row is None:
            return None

        return _row_to_candidate(
            row
        )

    finally:
        session.close()


def release_health_claim_safely(
    config_id,
    operator,
    reason,
):
    try:
        result = release_health_checking(
            config_id=config_id,
            operator=operator,
            reason=reason,
        )

        if result.get("released"):
            print(
                "[RESERVE HEALTH CLAIM RELEASED] "
                f"ID={config_id}"
            )

        else:
            print(
                "[RESERVE HEALTH CLAIM NOT RELEASED] "
                f"ID={config_id} "
                "REASON=status_progressed"
            )

        return result

    except Exception as exception:
        print(
            "[RESERVE HEALTH CLAIM RELEASE ERROR] "
            f"ID={config_id} "
            f"ERROR={repr(exception)}"
        )

        return {
            "config_id": config_id,
            "operator": operator,
            "released": False,
            "error": repr(exception),
        }


def check_reserve_candidate(
    candidate,
    operator,
    direct_ip,
    min_download_mbps,
    failure_limit,
):
    config_id = normalize_config_id(
        candidate["config_id"]
    )
    operator = normalize_operator(
        operator
    )

    print()
    print("-" * 70)
    print(
        f"RESERVE HEALTH ID={config_id} "
        f"PROTOCOL={candidate['protocol']} "
        f"STATUS={candidate['status']} "
        f"QUALITY={candidate['quality_status']} "
        f"HEALTH={candidate['health_status']} "
        "FAIL_STREAK="
        f"{candidate['health_fail_streak']}"
    )

    claimed = False

    try:
        claim_result = mark_health_checking(
            config_id=config_id,
            operator=operator,
        )
        claimed = True

        if (
            claim_result.get(
                "health_status"
            )
            != "checking"
        ):
            raise RuntimeError(
                "Health claim was not persisted "
                f"for config_id={config_id}"
            )

        current = (
            load_current_reserve_candidate(
                config_id=config_id,
                operator=operator,
            )
        )

        if current is None:
            raise RuntimeError(
                "Reserve item disappeared after "
                f"Health claim: {config_id}"
            )

        if current["status"] != "alive":
            result = record_health_failure(
                config_id=config_id,
                operator=operator,
                error=(
                    "state_mismatch:"
                    "connectivity="
                    f"{current['status']}"
                ),
                failure_limit=(
                    failure_limit
                ),
                structural=True,
            )
            claimed = False

            print(
                "[RESERVE QUARANTINED] "
                f"ID={config_id} "
                "ERROR=connectivity_not_alive"
            )

            return result[
                "health_status"
            ]

        if (
            current["quality_status"]
            != "qualified"
        ):
            result = record_health_failure(
                config_id=config_id,
                operator=operator,
                error=(
                    "state_mismatch:"
                    "quality="
                    f"{current['quality_status']}"
                ),
                failure_limit=(
                    failure_limit
                ),
                structural=True,
            )
            claimed = False

            print(
                "[RESERVE QUARANTINED] "
                f"ID={config_id} "
                "ERROR=quality_not_qualified"
            )

            return result[
                "health_status"
            ]

        summary = run_long_validation(
            candidate=current,
            direct_ip=direct_ip,
            rounds=1,
            round_delay_seconds=0,
        )

        if not isinstance(
            summary,
            dict,
        ):
            raise RuntimeError(
                "run_long_validation returned "
                "an invalid summary"
            )

        passed = long_validation_passed(
            summary=summary,
            min_successful_rounds=1,
            min_download_mbps=(
                min_download_mbps
            ),
        )

        if passed:
            result = (
                record_health_success(
                    config_id=config_id,
                    operator=operator,
                    avg_download_mbps=(
                        summary.get(
                            "average_download_mbps"
                        )
                    ),
                    median_download_mbps=(
                        summary.get(
                            "median_download_mbps"
                        )
                    ),
                )
            )
            claimed = False

            print(
                "[RESERVE HEALTHY] "
                f"ID={config_id} "
                "DOWNLOAD="
                f"{summary.get('average_download_mbps')} "
                "SUCCESS_STREAK="
                f"{result['health_success_streak']}"
            )

            return result[
                "health_status"
            ]

        error = build_failure_reason(
            summary=summary,
            min_successful_rounds=1,
            min_download_mbps=(
                min_download_mbps
            ),
        )

        result = record_health_failure(
            config_id=config_id,
            operator=operator,
            error=error,
            avg_download_mbps=(
                summary.get(
                    "average_download_mbps"
                )
            ),
            median_download_mbps=(
                summary.get(
                    "median_download_mbps"
                )
            ),
            failure_limit=(
                failure_limit
            ),
            structural=bool(
                summary.get(
                    "structural"
                )
            ),
        )
        claimed = False

        if (
            result["pool_status"]
            == "reserve"
        ):
            print(
                "[RESERVE SUSPECT - KEPT] "
                f"ID={config_id} "
                "FAIL_STREAK="
                f"{result['health_fail_streak']} "
                f"ERROR={error}"
            )

        else:
            print(
                "[RESERVE QUARANTINED] "
                f"ID={config_id} "
                "FAIL_STREAK="
                f"{result['health_fail_streak']} "
                f"ERROR={error}"
            )

        return result[
            "health_status"
        ]

    except KeyboardInterrupt:
        if claimed:
            release_health_claim_safely(
                config_id=config_id,
                operator=operator,
                reason=(
                    "reserve_health_"
                    "keyboard_interrupt"
                ),
            )
        raise

    except Exception as exception:
        if claimed:
            release_health_claim_safely(
                config_id=config_id,
                operator=operator,
                reason=(
                    "reserve_health_exception:"
                    f"{type(exception).__name__}"
                ),
            )
        raise


def run_startup_recovery(
    operator,
    enabled,
    stale_minutes,
    limit,
):
    operator = normalize_operator(
        operator
    )

    if not enabled:
        print(
            "[RESERVE HEALTH RECOVERY SKIPPED] "
            "REASON=disabled"
        )

        return {
            "operator": operator,
            "enabled": False,
            "recovered_total": 0,
            "recovered_ids": [],
        }

    result = recover_stuck_health_claims(
        operator=operator,
        stale_minutes=stale_minutes,
        limit=limit,
    )

    print(
        "[RESERVE HEALTH RECOVERY RESULT] "
        f"CANDIDATES="
        f"{result.get('candidate_total')} "
        f"RECOVERED="
        f"{result.get('recovered_total')} "
        f"IDS={result.get('recovered_ids')}"
    )

    return {
        **result,
        "enabled": True,
    }

@pool_maintenance_serialized
def run_reserve_health(
    operator,
    limit,
    min_download_mbps,
    failure_limit,
    healthy_min_age_minutes,
    suspect_min_age_minutes,
    recover_stuck=(
        DEFAULT_RECOVER_STUCK
    ),
    stuck_minutes=(
        DEFAULT_STUCK_MINUTES
    ),
    recover_limit=(
        DEFAULT_RECOVER_LIMIT
    ),
):
    operator = normalize_operator(
        operator
    )
    limit = max(
        int(limit),
        1,
    )
    min_download_mbps = max(
        safe_float(
            min_download_mbps
        )
        or 0.0,
        0.0,
    )
    failure_limit = max(
        int(failure_limit),
        1,
    )
    healthy_min_age_minutes = max(
        float(
            healthy_min_age_minutes
        ),
        0.0,
    )
    suspect_min_age_minutes = max(
        float(
            suspect_min_age_minutes
        ),
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

        selection = (
            load_reserve_health_candidates(
                operator=operator,
                limit=limit,
                healthy_min_age_minutes=(
                    healthy_min_age_minutes
                ),
                suspect_min_age_minutes=(
                    suspect_min_age_minutes
                ),
            )
        )
        candidates = selection["due"]

        print("=" * 70)
        print("V2 RESERVE HEALTH")
        print("OPERATOR:", operator)
        print(
            "RESERVE TOTAL:",
            selection["total"],
        )
        print(
            "BATCH DUE:",
            len(candidates),
        )
        print(
            "SKIPPED NOT DUE:",
            len(
                selection[
                    "skipped_not_due"
                ]
            ),
        )
        print(
            "SKIPPED CLAIMED:",
            len(
                selection[
                    "skipped_claimed"
                ]
            ),
        )
        print(
            "SKIPPED INVALID:",
            len(
                selection[
                    "skipped_invalid"
                ]
            ),
        )
        print(
            "MIN DOWNLOAD:",
            min_download_mbps,
        )
        print(
            "FAILURE LIMIT:",
            failure_limit,
        )
        print(
            "HEALTHY MIN AGE MINUTES:",
            healthy_min_age_minutes,
        )
        print(
            "SUSPECT MIN AGE MINUTES:",
            suspect_min_age_minutes,
        )
        print(
            "RECOVER STUCK:",
            recover_stuck,
        )
        print(
            "STUCK MINUTES:",
            stuck_minutes,
        )
        print("=" * 70)

        if not candidates:
            print(
                "NO RESERVE HEALTH "
                "CANDIDATES DUE"
            )

            for candidate in (
                selection[
                    "skipped_not_due"
                ][:limit]
            ):
                print(
                    "[RESERVE HEALTH SKIPPED "
                    "- NOT DUE] "
                    "ID="
                    f"{candidate['config_id']} "
                    "HEALTH="
                    f"{candidate['health_status']} "
                    "CHECKED_AT="
                    f"{candidate['health_checked_at']} "
                    "DUE_AT="
                    f"{due_at_text(candidate, healthy_min_age_minutes, suspect_min_age_minutes)}"
                )

            return {
                "healthy": 0,
                "suspect": 0,
                "failed": 0,
                "item_error": 0,
                "checked": 0,
                "skipped_not_due": len(
                    selection[
                        "skipped_not_due"
                    ]
                ),
                "skipped_claimed": len(
                    selection[
                        "skipped_claimed"
                    ]
                ),
                "skipped_invalid": len(
                    selection[
                        "skipped_invalid"
                    ]
                ),
            }

        direct_ip = get_direct_ip()

        print(
            "DIRECT IP:",
            direct_ip or "UNKNOWN",
        )

        counts = {
            "healthy": 0,
            "suspect": 0,
            "failed": 0,
            "item_error": 0,
        }

        checked = 0

        for candidate in candidates:
            try:
                status = (
                    check_reserve_candidate(
                        candidate=candidate,
                        operator=operator,
                        direct_ip=direct_ip,
                        min_download_mbps=(
                            min_download_mbps
                        ),
                        failure_limit=(
                            failure_limit
                        ),
                    )
                )

            except KeyboardInterrupt:
                print()
                print(
                    "[RESERVE HEALTH] "
                    "STOPPED BY USER"
                )
                raise

            except Exception as exception:
                status = "item_error"

                print(
                    "[RESERVE HEALTH ITEM ERROR] "
                    "ID="
                    f"{candidate['config_id']} "
                    f"ERROR={repr(exception)}"
                )

            checked += 1
            counts[status] = (
                counts.get(status, 0)
                + 1
            )

        print()
        print("=" * 70)
        print(
            "V2 RESERVE HEALTH FINISHED"
        )
        print("CHECKED:", checked)
        print(
            "HEALTHY:",
            counts.get("healthy", 0),
        )
        print(
            "SUSPECT:",
            counts.get("suspect", 0),
        )
        print(
            "FAILED:",
            counts.get("failed", 0),
        )
        print(
            "ITEM ERROR:",
            counts.get("item_error", 0),
        )
        print(
            "SKIPPED NOT DUE:",
            len(
                selection[
                    "skipped_not_due"
                ]
            ),
        )
        print(
            "SKIPPED CLAIMED:",
            len(
                selection[
                    "skipped_claimed"
                ]
            ),
        )
        print(
            "SKIPPED INVALID:",
            len(
                selection[
                    "skipped_invalid"
                ]
            ),
        )
        print("=" * 70)

        counts["checked"] = checked
        counts["skipped_not_due"] = len(
            selection[
                "skipped_not_due"
            ]
        )
        counts["skipped_claimed"] = len(
            selection[
                "skipped_claimed"
            ]
        )
        counts["skipped_invalid"] = len(
            selection[
                "skipped_invalid"
            ]
        )

        return counts


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Run one independent health batch "
            "over accepted V2 Reserve configs."
        )
    )

    parser.add_argument(
        "--operator",
        required=True,
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_BATCH_LIMIT,
    )

    parser.add_argument(
        "--min-download",
        type=float,
        default=(
            DEFAULT_MIN_DOWNLOAD_MBPS
        ),
    )

    parser.add_argument(
        "--failure-limit",
        type=int,
        default=DEFAULT_FAILURE_LIMIT,
    )

    parser.add_argument(
        "--healthy-min-age-minutes",
        type=float,
        default=(
            DEFAULT_HEALTHY_MIN_AGE_MINUTES
        ),
        help=(
            "Minimum age before rechecking "
            "a healthy Reserve item."
        ),
    )

    parser.add_argument(
        "--suspect-min-age-minutes",
        type=float,
        default=(
            DEFAULT_SUSPECT_MIN_AGE_MINUTES
        ),
        help=(
            "Minimum age before retrying "
            "a suspect Reserve item."
        ),
    )

    parser.add_argument(
        "--recover-stuck",
        dest="recover_stuck",
        action="store_true",
        default=DEFAULT_RECOVER_STUCK,
        help=(
            "Recover stale Health checking "
            "claims at process startup."
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

    return parser


def main():
    args = (
        build_parser()
        .parse_args()
    )

    run_reserve_health(
        operator=args.operator,
        limit=args.limit,
        min_download_mbps=(
            args.min_download
        ),
        failure_limit=(
            args.failure_limit
        ),
        healthy_min_age_minutes=(
            args.healthy_min_age_minutes
        ),
        suspect_min_age_minutes=(
            args.suspect_min_age_minutes
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
    )


if __name__ == "__main__":
    main()
