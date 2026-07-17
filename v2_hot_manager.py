import argparse
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

from sqlalchemy import or_

from database.db import SessionLocal
from database.models import Config, ConfigPool
from database.repositories.pool_repository import (
    get_hot_items,
    mark_health_checking,
    normalize_operator,
    record_health_failure,
    record_health_success,
    recover_stuck_health_claims,
    release_health_checking,
    replace_hot_set,
)
from delivery_validate_public_configs import (
    get_direct_ip,
    validate_config_text,
)

from pool_maintenance_lock import (
    pool_maintenance_serialized,
)


DEFAULT_HOT_TARGET = 5

DEFAULT_MIN_QUALITY_SCORE = 30.0
DEFAULT_MAX_PING_AVG_MS = 2000.0
DEFAULT_MIN_QUALITY_DOWNLOAD_MBPS = 1.5

DEFAULT_HEALTH_ROUNDS = 3
DEFAULT_ROUND_DELAY_SECONDS = 15.0
DEFAULT_MIN_SUCCESSFUL_ROUNDS = 2

# Hot must not weaken the Quality admission floor.
DEFAULT_MIN_HOT_KEEP_DOWNLOAD_MBPS = 1.5
DEFAULT_MIN_PROMOTION_DOWNLOAD_MBPS = 1.5

DEFAULT_HEALTH_FAILURE_LIMIT = 2

DEFAULT_HOT_MIN_AGE_MINUTES = 60.0
DEFAULT_PROMOTION_RETRY_MIN_AGE_MINUTES = 30.0

DEFAULT_MIN_HOT_CAPACITY = 3
DEFAULT_CORRELATED_FAILURE_MIN_COUNT = 2
DEFAULT_CORRELATED_FAILURE_RATIO = 0.60

DEFAULT_RECOVER_STUCK = True
DEFAULT_STUCK_MINUTES = 30.0
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


def compact_text(value, limit=900):
    text = str(value or "").strip()
    limit = max(
        int(limit),
        1,
    )

    if len(text) <= limit:
        return text

    return text[:limit] + "..."


def utcnow_naive():
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


@contextmanager
def single_instance_lock(operator):
    """
    Prevent concurrent Hot managers for the same operator.
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
        / f"hot_manager_{operator}.lock"
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
                "Another Hot manager is already "
                "running for "
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


def health_check_due(
    health_checked_at,
    min_age_minutes,
):
    if health_checked_at is None:
        return True

    min_age_minutes = max(
        float(min_age_minutes),
        0.0,
    )

    if health_checked_at.tzinfo is not None:
        now = datetime.now(
            timezone.utc
        ).astimezone(
            health_checked_at.tzinfo
        )

    else:
        now = datetime.now(
            timezone.utc
        ).replace(
            tzinfo=None
        )

    due_at = (
        health_checked_at
        + timedelta(
            minutes=min_age_minutes
        )
    )

    return now >= due_at


def health_due_at_text(
    health_checked_at,
    min_age_minutes,
):
    if health_checked_at is None:
        return "NOW"

    due_at = (
        health_checked_at
        + timedelta(
            minutes=max(
                float(min_age_minutes),
                0.0,
            )
        )
    )

    return due_at.isoformat()


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
                "[HOT HEALTH CLAIM RELEASED] "
                f"ID={config_id}"
            )

        return result

    except Exception as exception:
        print(
            "[HOT HEALTH CLAIM RELEASE ERROR] "
            f"ID={config_id} "
            f"ERROR={repr(exception)}"
        )

        return {
            "config_id": config_id,
            "operator": operator,
            "released": False,
            "error": repr(exception),
        }


def release_claim_ids(
    config_ids,
    operator,
    reason,
):
    released = []

    for config_id in sorted(
        set(config_ids)
    ):
        result = (
            release_health_claim_safely(
                config_id=config_id,
                operator=operator,
                reason=reason,
            )
        )

        if result.get("released"):
            released.append(
                config_id
            )

    return released


def hold_hot_failure(
    config_id,
    operator,
    error,
    avg_download_mbps,
    median_download_mbps,
    increment_fail_streak,
    hold_reason,
):
    """
    Keep a non-structural failed item in Hot when removing it would
    violate the configured capacity policy or when the circuit breaker
    detects correlated infrastructure failure.

    The row must still be an accepted, claimed Hot item.
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
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id
                == config_id,
                ConfigPool.operator
                == operator,
            )
            .one_or_none()
        )

        if row is None:
            raise LookupError(
                "pool item not found: "
                f"config_id={config_id} "
                f"operator={operator}"
            )

        if (
            row.pool_status != "hot"
            or row.gate_status
            != "accepted"
            or row.health_status
            != "checking"
        ):
            raise RuntimeError(
                "Hot hold requires an accepted, "
                "claimed Hot item: "
                f"config_id={config_id} "
                f"pool_status={row.pool_status} "
                f"gate_status={row.gate_status} "
                "health_status="
                f"{row.health_status}"
            )

        row.health_status = "suspect"
        row.health_checked_at = (
            utcnow_naive()
        )
        row.health_success_streak = 0

        if increment_fail_streak:
            row.health_fail_streak = (
                int(
                    row.health_fail_streak
                    or 0
                )
                + 1
            )

        row.health_avg_download_mbps = (
            safe_float(
                avg_download_mbps
            )
        )
        row.health_median_download_mbps = (
            safe_float(
                median_download_mbps
            )
        )
        row.health_error = compact_text(
            f"{hold_reason}: {error}",
            1800,
        )

        session.commit()
        session.refresh(row)

        return {
            "config_id": row.config_id,
            "pool_status": (
                row.pool_status
            ),
            "health_status": (
                row.health_status
            ),
            "health_fail_streak": int(
                row.health_fail_streak
                or 0
            ),
            "health_checked_at": (
                row.health_checked_at
                .isoformat()
                if row.health_checked_at
                else None
            ),
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def row_to_candidate(row):
    return {
        "config_id": int(row.id),
        "protocol": row.protocol,
        "config_text": row.config_text,
        "status": row.status,
        "quality_status": (
            row.quality_status
        ),
        "latency": safe_float(
            row.latency
        ),
        "quality_score": safe_float(
            row.quality_score
        ),
        "ping_avg_ms": safe_float(
            row.ping_avg_ms
        ),
        "quality_download_mbps": (
            safe_float(
                row.download_mbps
            )
        ),
        "upload_mbps": safe_float(
            row.upload_mbps
        ),
        "pool_status": row.pool_status,
        "hot_slot": row.hot_slot,
        "previous_gate_download_mbps": (
            safe_float(
                row.gate_download_mbps
            )
        ),
        "gate_checked_at": (
            row.gate_checked_at
        ),
        "health_status": (
            row.health_status
        ),
        "health_checked_at": (
            row.health_checked_at
        ),
        "health_success_streak": int(
            row.health_success_streak
            or 0
        ),
        "health_fail_streak": int(
            row.health_fail_streak
            or 0
        ),
        "health_avg_download_mbps": (
            safe_float(
                row.health_avg_download_mbps
            )
        ),
        "health_median_download_mbps": (
            safe_float(
                row.health_median_download_mbps
            )
        ),
        "health_error": row.health_error,
    }


def _candidate_columns():
    return (
        Config.id,
        Config.protocol,
        Config.config_text,
        Config.status,
        Config.quality_status,
        Config.latency,
        Config.quality_score,
        Config.ping_avg_ms,
        Config.download_mbps,
        Config.upload_mbps,
        ConfigPool.pool_status,
        ConfigPool.hot_slot,
        ConfigPool.gate_download_mbps,
        ConfigPool.gate_checked_at,
        ConfigPool.health_status,
        ConfigPool.health_checked_at,
        ConfigPool.health_success_streak,
        ConfigPool.health_fail_streak,
        ConfigPool.health_avg_download_mbps,
        ConfigPool.health_median_download_mbps,
        ConfigPool.health_error,
    )


def load_hot_candidates(operator):
    operator = normalize_operator(
        operator
    )

    session = SessionLocal()

    try:
        rows = (
            session.query(
                *_candidate_columns()
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
                == "hot",
                ConfigPool.gate_status
                == "accepted",
            )
            .order_by(
                ConfigPool.hot_slot.asc(),
                Config.id.asc(),
            )
            .all()
        )

        return [
            row_to_candidate(row)
            for row in rows
        ]

    finally:
        session.close()


def load_reserve_candidates(
    operator,
    limit,
):
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
                *_candidate_columns()
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
                Config.status == "alive",
                Config.quality_status
                == "qualified",
                or_(
                    ConfigPool.health_status
                    .is_(None),
                    ConfigPool.health_status
                    != "checking",
                ),
            )
            .order_by(
                Config.quality_score.desc(),
                Config.download_mbps.desc(),
                Config.ping_avg_ms.asc(),
                ConfigPool.gate_download_mbps
                .desc(),
                Config.id.desc(),
            )
            .limit(limit)
            .all()
        )

        return [
            row_to_candidate(row)
            for row in rows
        ]

    finally:
        session.close()


def load_current_candidate(
    config_id,
    operator,
    expected_pool_status,
):
    """
    Reload a candidate after acquiring its Health claim.
    """
    config_id = normalize_config_id(
        config_id
    )
    operator = normalize_operator(
        operator
    )
    expected_pool_status = str(
        expected_pool_status or ""
    ).strip().lower()

    if expected_pool_status not in {
        "reserve",
        "hot",
    }:
        raise ValueError(
            "expected_pool_status must be "
            "reserve or hot"
        )

    session = SessionLocal()

    try:
        row = (
            session.query(
                *_candidate_columns()
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
                == expected_pool_status,
                ConfigPool.gate_status
                == "accepted",
                ConfigPool.health_status
                == "checking",
            )
            .first()
        )

        if row is None:
            return None

        return row_to_candidate(
            row
        )

    finally:
        session.close()


def evaluate_quality_floor(
    candidate,
    min_quality_score,
    max_ping_avg_ms,
    min_quality_download_mbps,
):
    reasons = []

    quality_score = candidate.get(
        "quality_score"
    )
    ping_avg = candidate.get(
        "ping_avg_ms"
    )
    quality_download = candidate.get(
        "quality_download_mbps"
    )
    upload = candidate.get(
        "upload_mbps"
    )

    if quality_score is None:
        reasons.append(
            "missing_quality_score"
        )

    elif (
        quality_score
        < min_quality_score
    ):
        reasons.append(
            "quality_score_too_low:"
            f"{quality_score}<"
            f"{min_quality_score}"
        )

    if ping_avg is None:
        reasons.append(
            "missing_ping_avg"
        )

    elif ping_avg > max_ping_avg_ms:
        reasons.append(
            "ping_too_high:"
            f"{ping_avg}>"
            f"{max_ping_avg_ms}"
        )

    if quality_download is None:
        reasons.append(
            "missing_quality_download"
        )

    elif (
        quality_download
        < min_quality_download_mbps
    ):
        reasons.append(
            "quality_download_too_low:"
            f"{quality_download}<"
            f"{min_quality_download_mbps}"
        )

    if (
        upload is not None
        and upload < 0.3
    ):
        reasons.append(
            f"upload_too_low:{upload}<0.3"
        )

    return reasons


def is_structural_error(error):
    text = str(
        error or ""
    ).strip().lower()

    structural_markers = (
        "unsupported protocol",
        "unsupported configuration",
        "parse error",
        "parser error",
        "invalid config",
        "invalid configuration",
        "builder error",
        "build error",
        "failed to build",
        "missing builder",
        "missing server",
        "missing port",
        "missing uuid",
        "state_mismatch",
        "quality_policy_failure",
    )

    return any(
        marker in text
        for marker in structural_markers
    )


def run_long_validation(
    candidate,
    direct_ip,
    rounds,
    round_delay_seconds,
):
    """
    Run independent Admission-style validations over one candidate.

    validate_config_text() owns its own Xray lifecycle. This function
    does not start an additional Xray process itself.
    """
    candidate = dict(
        candidate or {}
    )
    rounds = max(
        int(rounds),
        1,
    )
    round_delay_seconds = max(
        float(round_delay_seconds),
        0.0,
    )

    if not candidate.get(
        "config_text"
    ):
        raise ValueError(
            "candidate config_text is required"
        )

    downloads = []
    exit_ips = []
    errors = []
    results = []
    structural = False

    for round_number in range(
        1,
        rounds + 1,
    ):
        try:
            result = validate_config_text(
                candidate["config_text"],
                direct_ip=direct_ip,
            )

        except Exception as exception:
            result = {
                "ok": False,
                "error": repr(exception),
                "exit_ip": None,
                "download_mbps": None,
            }

        result = dict(
            result or {}
        )

        download = safe_float(
            result.get(
                "download_mbps"
            )
        )

        exit_ip = result.get(
            "exit_ip"
        )

        if exit_ip is not None:
            exit_ip = (
                str(exit_ip).strip()
                or None
            )

        ok = bool(
            result.get("ok") is True
            and download is not None
            and download > 0
            and exit_ip
        )

        normalized = {
            "round": round_number,
            "ok": ok,
            "download_mbps": download,
            "exit_ip": exit_ip,
            "error": result.get(
                "error"
            ),
        }
        results.append(
            normalized
        )

        if ok:
            downloads.append(
                download
            )
            exit_ips.append(
                exit_ip
            )

            print(
                f"[ROUND {round_number}/"
                f"{rounds} PASS] "
                "ID="
                f"{candidate.get('config_id')} "
                f"EXIT_IP={exit_ip} "
                f"DOWNLOAD={download}"
            )

        else:
            error = (
                result.get("error")
                or "invalid_round_result"
            )
            error = compact_text(
                error
            )
            errors.append(
                error
            )

            print(
                f"[ROUND {round_number}/"
                f"{rounds} FAIL] "
                "ID="
                f"{candidate.get('config_id')} "
                "ERROR="
                f"{compact_text(error, 500)}"
            )

            if is_structural_error(
                error
            ):
                structural = True

                print(
                    "[LONG VALIDATION "
                    "FAIL-FAST] "
                    "REASON=structural_error"
                )
                break

        if (
            round_number < rounds
            and round_delay_seconds > 0
        ):
            time.sleep(
                round_delay_seconds
            )

    average_download = (
        round(
            mean(downloads),
            2,
        )
        if downloads
        else None
    )

    median_download = (
        round(
            median(downloads),
            2,
        )
        if downloads
        else None
    )

    selected_exit_ip = (
        Counter(
            exit_ips
        ).most_common(1)[0][0]
        if exit_ips
        else None
    )

    return {
        "rounds": rounds,
        "attempted_rounds": len(
            results
        ),
        "success_count": len(
            downloads
        ),
        "failure_count": (
            len(results)
            - len(downloads)
        ),
        "downloads": downloads,
        "average_download_mbps": (
            average_download
        ),
        "median_download_mbps": (
            median_download
        ),
        "exit_ip": (
            selected_exit_ip
        ),
        "errors": errors,
        "structural": (
            structural
            or any(
                is_structural_error(
                    error
                )
                for error in errors
            )
        ),
        "results": results,
    }


def long_validation_passed(
    summary,
    min_successful_rounds,
    min_download_mbps,
):
    summary = dict(
        summary or {}
    )

    average_download = safe_float(
        summary.get(
            "average_download_mbps"
        )
    )
    median_download = safe_float(
        summary.get(
            "median_download_mbps"
        )
    )

    return bool(
        int(
            summary.get(
                "success_count"
            )
            or 0
        )
        >= int(
            min_successful_rounds
        )
        and average_download is not None
        and median_download is not None
        and average_download
        >= float(min_download_mbps)
        and median_download
        >= float(min_download_mbps)
        and summary.get("exit_ip")
        and not summary.get(
            "structural"
        )
    )


def build_failure_reason(
    summary,
    min_successful_rounds,
    min_download_mbps,
):
    summary = dict(
        summary or {}
    )
    reasons = []

    success_count = int(
        summary.get(
            "success_count"
        )
        or 0
    )

    if (
        success_count
        < min_successful_rounds
    ):
        reasons.append(
            "successful_rounds_too_low:"
            f"{success_count}<"
            f"{min_successful_rounds}"
        )

    average_download = safe_float(
        summary.get(
            "average_download_mbps"
        )
    )
    median_download = safe_float(
        summary.get(
            "median_download_mbps"
        )
    )

    if average_download is None:
        reasons.append(
            "missing_average_download"
        )

    elif (
        average_download
        < min_download_mbps
    ):
        reasons.append(
            "average_download_too_low:"
            f"{average_download}<"
            f"{min_download_mbps}"
        )

    if median_download is None:
        reasons.append(
            "missing_median_download"
        )

    elif (
        median_download
        < min_download_mbps
    ):
        reasons.append(
            "median_download_too_low:"
            f"{median_download}<"
            f"{min_download_mbps}"
        )

    if not summary.get(
        "exit_ip"
    ):
        reasons.append(
            "missing_exit_ip"
        )

    errors = list(
        summary.get("errors")
        or []
    )

    if errors:
        reasons.append(
            "round_errors:"
            + " || ".join(
                errors[-2:]
            )
        )

    return compact_text(
        " | ".join(reasons)
        or "long_health_check_failed",
        1800,
    )


def build_policy_failure_summary(
    reasons,
):
    error = (
        "quality_policy_failure:"
        + " | ".join(
            str(value)
            for value in reasons
        )
    )

    return {
        "rounds": 0,
        "attempted_rounds": 0,
        "success_count": 0,
        "failure_count": 0,
        "downloads": [],
        "average_download_mbps": None,
        "median_download_mbps": None,
        "exit_ip": None,
        "errors": [error],
        "structural": True,
        "results": [],
    }


def print_candidate_header(
    label,
    candidate,
):
    print()
    print("-" * 70)
    print(
        f"{label} "
        "ID="
        f"{candidate['config_id']} "
        "PROTOCOL="
        f"{candidate['protocol']} "
        "CONNECTIVITY="
        f"{candidate['status']} "
        "QUALITY_STATUS="
        f"{candidate['quality_status']} "
        "SCORE="
        f"{candidate['quality_score']} "
        "PING="
        f"{candidate['ping_avg_ms']} "
        "QUALITY_DOWN="
        f"{candidate['quality_download_mbps']} "
        "HEALTH="
        f"{candidate['health_status']} "
        "FAIL_STREAK="
        f"{candidate['health_fail_streak']}"
    )


def claim_and_reload_candidate(
    candidate,
    operator,
    expected_pool_status,
):
    config_id = normalize_config_id(
        candidate["config_id"]
    )

    claim_result = mark_health_checking(
        config_id=config_id,
        operator=operator,
    )

    if (
        claim_result.get(
            "health_status"
        )
        != "checking"
    ):
        raise RuntimeError(
            "Health claim was not persisted: "
            f"config_id={config_id}"
        )

    current = load_current_candidate(
        config_id=config_id,
        operator=operator,
        expected_pool_status=(
            expected_pool_status
        ),
    )

    if current is None:
        raise RuntimeError(
            "Candidate disappeared after "
            "Health claim: "
            f"config_id={config_id}"
        )

    return current


def check_existing_hot(
    operator,
    hot_candidates,
    direct_ip,
    rounds,
    round_delay_seconds,
    min_successful_rounds,
    min_hot_keep_download_mbps,
    failure_limit,
    hot_min_age_minutes,
    correlated_failure_min_count,
    correlated_failure_ratio,
    min_quality_score=(
        DEFAULT_MIN_QUALITY_SCORE
    ),
    max_ping_avg_ms=(
        DEFAULT_MAX_PING_AVG_MS
    ),
    min_quality_download_mbps=(
        DEFAULT_MIN_QUALITY_DOWNLOAD_MBPS
    ),
):
    """
    Validate due Hot rows.

    Failed rows remain claimed until the correlated-failure decision is
    complete. Unexpected exceptions release every unfinished claim.
    """
    operator = normalize_operator(
        operator
    )

    current_ids = [
        candidate["config_id"]
        for candidate in hot_candidates
    ]

    healthy_count = 0
    suspect_count = 0
    skipped_not_due_count = 0
    skipped_claimed_count = 0
    due_count = 0
    failures = []
    active_claim_ids = set()

    try:
        for candidate in hot_candidates:
            config_id = normalize_config_id(
                candidate["config_id"]
            )

            print_candidate_header(
                "HOT HEALTH",
                candidate,
            )

            if (
                candidate.get(
                    "health_status"
                )
                == "checking"
            ):
                skipped_claimed_count += 1

                print(
                    "[HOT HEALTH SKIPPED "
                    "- CLAIMED] "
                    f"ID={config_id}"
                )
                continue

            if not health_check_due(
                candidate.get(
                    "health_checked_at"
                ),
                hot_min_age_minutes,
            ):
                skipped_not_due_count += 1

                print(
                    "[HOT HEALTH SKIPPED "
                    "- NOT DUE] "
                    f"ID={config_id} "
                    "CHECKED_AT="
                    f"{candidate.get('health_checked_at')} "
                    "DUE_AT="
                    f"{health_due_at_text(candidate.get('health_checked_at'), hot_min_age_minutes)}"
                )
                continue

            due_count += 1

            current = (
                claim_and_reload_candidate(
                    candidate=candidate,
                    operator=operator,
                    expected_pool_status=(
                        "hot"
                    ),
                )
            )
            active_claim_ids.add(
                config_id
            )

            state_reasons = []

            if current["status"] != "alive":
                state_reasons.append(
                    "state_mismatch:"
                    "connectivity="
                    f"{current['status']}"
                )

            if (
                current["quality_status"]
                != "qualified"
            ):
                state_reasons.append(
                    "state_mismatch:"
                    "quality="
                    f"{current['quality_status']}"
                )

            quality_reasons = (
                evaluate_quality_floor(
                    candidate=current,
                    min_quality_score=(
                        min_quality_score
                    ),
                    max_ping_avg_ms=(
                        max_ping_avg_ms
                    ),
                    min_quality_download_mbps=(
                        min_quality_download_mbps
                    ),
                )
            )

            state_reasons.extend(
                quality_reasons
            )

            if state_reasons:
                summary = (
                    build_policy_failure_summary(
                        state_reasons
                    )
                )
                failure_reason = (
                    " | ".join(
                        state_reasons
                    )
                )

            else:
                summary = (
                    run_long_validation(
                        candidate=current,
                        direct_ip=direct_ip,
                        rounds=rounds,
                        round_delay_seconds=(
                            round_delay_seconds
                        ),
                    )
                )

                if long_validation_passed(
                    summary=summary,
                    min_successful_rounds=(
                        min_successful_rounds
                    ),
                    min_download_mbps=(
                        min_hot_keep_download_mbps
                    ),
                ):
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
                    active_claim_ids.discard(
                        config_id
                    )
                    healthy_count += 1

                    print(
                        "[HOT HEALTHY] "
                        f"ID={config_id} "
                        "SUCCESS="
                        f"{summary['success_count']}/"
                        f"{rounds} "
                        "AVG="
                        f"{summary.get('average_download_mbps')} "
                        "MEDIAN="
                        f"{summary.get('median_download_mbps')} "
                        "STREAK="
                        f"{result['health_success_streak']}"
                    )
                    continue

                failure_reason = (
                    build_failure_reason(
                        summary=summary,
                        min_successful_rounds=(
                            min_successful_rounds
                        ),
                        min_download_mbps=(
                            min_hot_keep_download_mbps
                        ),
                    )
                )

            failures.append(
                {
                    "candidate": current,
                    "summary": summary,
                    "error": (
                        failure_reason
                    ),
                    "projected_fail_streak": (
                        int(
                            current[
                                "health_fail_streak"
                            ]
                            or 0
                        )
                        + 1
                    ),
                }
            )

        non_structural_failures = [
            item
            for item in failures
            if not item[
                "summary"
            ].get("structural")
        ]

        failure_ratio = (
            len(
                non_structural_failures
            )
            / due_count
            if due_count > 0
            else 0.0
        )

        circuit_breaker_active = bool(
            len(
                non_structural_failures
            )
            >= correlated_failure_min_count
            and failure_ratio
            >= correlated_failure_ratio
        )

        pending_removals = []

        if circuit_breaker_active:
            print()
            print("!" * 70)
            print(
                "HOT CIRCUIT BREAKER ACTIVE"
            )
            print("DUE HOT:", due_count)
            print(
                "NON-STRUCTURAL FAILURES:",
                len(
                    non_structural_failures
                ),
            )
            print(
                "FAILURE RATIO:",
                round(
                    failure_ratio,
                    2,
                ),
            )
            print(
                "ACTION: preserve only "
                "non-structural failures; "
                "structural failures remain "
                "removal candidates"
            )
            print("!" * 70)

        for item in failures:
            candidate = item[
                "candidate"
            ]
            summary = item[
                "summary"
            ]
            config_id = candidate[
                "config_id"
            ]

            if summary.get(
                "structural"
            ):
                pending_removals.append(
                    item
                )

                print(
                    "[HOT STRUCTURAL "
                    "REMOVAL PENDING] "
                    f"ID={config_id}"
                )
                continue

            if circuit_breaker_active:
                result = hold_hot_failure(
                    config_id=config_id,
                    operator=operator,
                    error=item["error"],
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
                    increment_fail_streak=False,
                    hold_reason=(
                        "correlated_failure_"
                        "circuit_breaker"
                    ),
                )
                active_claim_ids.discard(
                    config_id
                )
                suspect_count += 1

                print(
                    "[HOT CIRCUIT HOLD] "
                    f"ID={config_id} "
                    "FAIL_STREAK="
                    f"{result['health_fail_streak']}"
                )
                continue

            removal_eligible = bool(
                item[
                    "projected_fail_streak"
                ]
                >= failure_limit
            )

            if removal_eligible:
                pending_removals.append(
                    item
                )

                print(
                    "[HOT REMOVAL PENDING "
                    "REPLACEMENT] "
                    f"ID={config_id} "
                    "PROJECTED_FAIL_STREAK="
                    f"{item['projected_fail_streak']}"
                )
                continue

            result = record_health_failure(
                config_id=config_id,
                operator=operator,
                error=item["error"],
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
                structural=False,
            )
            active_claim_ids.discard(
                config_id
            )
            suspect_count += 1

            print(
                "[HOT SUSPECT - KEPT] "
                f"ID={config_id} "
                "FAIL_STREAK="
                f"{result['health_fail_streak']} "
                f"ERROR={item['error']}"
            )

        pending_removals.sort(
            key=lambda item: (
                0
                if item[
                    "summary"
                ].get("structural")
                else 1,
                -item[
                    "projected_fail_streak"
                ],
                item[
                    "summary"
                ].get(
                    "success_count",
                    0,
                ),
                (
                    item[
                        "summary"
                    ].get(
                        "average_download_mbps"
                    )
                    if item[
                        "summary"
                    ].get(
                        "average_download_mbps"
                    )
                    is not None
                    else -1.0
                ),
            )
        )

        pending_ids = {
            item["candidate"][
                "config_id"
            ]
            for item in pending_removals
        }

        unexpected_active = (
            active_claim_ids
            - pending_ids
        )

        if unexpected_active:
            raise RuntimeError(
                "Unresolved Hot Health claims: "
                f"{sorted(unexpected_active)}"
            )

        return {
            "current_ids": current_ids,
            "healthy_count": (
                healthy_count
            ),
            "suspect_count": (
                suspect_count
            ),
            "skipped_not_due_count": (
                skipped_not_due_count
            ),
            "skipped_claimed_count": (
                skipped_claimed_count
            ),
            "due_count": due_count,
            "failure_count": len(
                failures
            ),
            "failure_ratio": (
                failure_ratio
            ),
            "circuit_breaker_active": (
                circuit_breaker_active
            ),
            "pending_removals": (
                pending_removals
            ),
            "pending_claim_ids": sorted(
                pending_ids
            ),
        }

    except Exception:
        release_claim_ids(
            config_ids=active_claim_ids,
            operator=operator,
            reason=(
                "hot_existing_check_exception"
            ),
        )
        raise


def promote_from_reserve(
    operator,
    reserve_candidates,
    needed,
    direct_ip,
    rounds,
    round_delay_seconds,
    min_successful_rounds,
    min_quality_score,
    max_ping_avg_ms,
    min_quality_download_mbps,
    min_promotion_download_mbps,
    failure_limit,
    promotion_retry_min_age_minutes,
):
    """
    Validate Reserve candidates for Hot promotion.

    Promotion does not rerun or overwrite Admission Gate. A candidate
    already has pool_status=reserve and gate_status=accepted. Successful
    long validation is stored as Health, and replace_hot_set() performs
    the final Reserve -> Hot transition.
    """
    operator = normalize_operator(
        operator
    )
    needed = max(
        int(needed),
        0,
    )

    promoted_ids = []
    quality_rejected = 0
    long_check_rejected = 0
    cooldown_skipped = 0
    claimed_skipped = 0
    checked = 0

    if needed <= 0:
        return {
            "promoted_ids": [],
            "quality_rejected": 0,
            "long_check_rejected": 0,
            "cooldown_skipped": 0,
            "claimed_skipped": 0,
            "checked": 0,
        }

    for candidate in reserve_candidates:
        if len(promoted_ids) >= needed:
            break

        config_id = normalize_config_id(
            candidate["config_id"]
        )

        print_candidate_header(
            "PROMOTION CANDIDATE",
            candidate,
        )

        if (
            candidate.get(
                "health_status"
            )
            == "checking"
        ):
            claimed_skipped += 1

            print(
                "[PROMOTION SKIPPED "
                "- CLAIMED] "
                f"ID={config_id}"
            )
            continue

        if (
            candidate.get(
                "health_status"
            )
            == "suspect"
            and not health_check_due(
                candidate.get(
                    "health_checked_at"
                ),
                promotion_retry_min_age_minutes,
            )
        ):
            cooldown_skipped += 1

            print(
                "[PROMOTION SKIPPED "
                "- SUSPECT COOLDOWN] "
                f"ID={config_id} "
                "CHECKED_AT="
                f"{candidate.get('health_checked_at')} "
                "DUE_AT="
                f"{health_due_at_text(candidate.get('health_checked_at'), promotion_retry_min_age_minutes)}"
            )
            continue

        quality_reasons = (
            evaluate_quality_floor(
                candidate=candidate,
                min_quality_score=(
                    min_quality_score
                ),
                max_ping_avg_ms=(
                    max_ping_avg_ms
                ),
                min_quality_download_mbps=(
                    min_quality_download_mbps
                ),
            )
        )

        if quality_reasons:
            quality_rejected += 1

            print(
                "[PROMOTION QUALITY "
                "SKIPPED] "
                f"ID={config_id} "
                "ERROR="
                f"{' | '.join(quality_reasons)}"
            )
            continue

        checked += 1
        claimed = False

        try:
            current = (
                claim_and_reload_candidate(
                    candidate=candidate,
                    operator=operator,
                    expected_pool_status=(
                        "reserve"
                    ),
                )
            )
            claimed = True

            current_reasons = []

            if current["status"] != "alive":
                current_reasons.append(
                    "state_mismatch:"
                    "connectivity="
                    f"{current['status']}"
                )

            if (
                current["quality_status"]
                != "qualified"
            ):
                current_reasons.append(
                    "state_mismatch:"
                    "quality="
                    f"{current['quality_status']}"
                )

            current_reasons.extend(
                evaluate_quality_floor(
                    candidate=current,
                    min_quality_score=(
                        min_quality_score
                    ),
                    max_ping_avg_ms=(
                        max_ping_avg_ms
                    ),
                    min_quality_download_mbps=(
                        min_quality_download_mbps
                    ),
                )
            )

            if current_reasons:
                quality_rejected += 1

                failure_reason = (
                    " | ".join(
                        current_reasons
                    )
                )

                result = (
                    record_health_failure(
                        config_id=config_id,
                        operator=operator,
                        error=failure_reason,
                        failure_limit=(
                            failure_limit
                        ),
                        structural=True,
                    )
                )
                claimed = False

                print(
                    "[PROMOTION STATE "
                    "REJECTED] "
                    f"ID={config_id} "
                    "POOL="
                    f"{result['pool_status']} "
                    f"ERROR={failure_reason}"
                )
                continue

            summary = run_long_validation(
                candidate=current,
                direct_ip=direct_ip,
                rounds=rounds,
                round_delay_seconds=(
                    round_delay_seconds
                ),
            )

            passed = long_validation_passed(
                summary=summary,
                min_successful_rounds=(
                    min_successful_rounds
                ),
                min_download_mbps=(
                    min_promotion_download_mbps
                ),
            )

            if not passed:
                long_check_rejected += 1

                failure_reason = (
                    build_failure_reason(
                        summary=summary,
                        min_successful_rounds=(
                            min_successful_rounds
                        ),
                        min_download_mbps=(
                            min_promotion_download_mbps
                        ),
                    )
                )

                result = (
                    record_health_failure(
                        config_id=config_id,
                        operator=operator,
                        error=failure_reason,
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
                )
                claimed = False

                print(
                    "[PROMOTION REJECTED] "
                    f"ID={config_id} "
                    "POOL="
                    f"{result['pool_status']} "
                    "HEALTH="
                    f"{result['health_status']} "
                    "FAIL_STREAK="
                    f"{result['health_fail_streak']} "
                    f"ERROR={failure_reason}"
                )
                continue

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
            claimed = False

            promoted_ids.append(
                config_id
            )

            print(
                "[PROMOTION PASSED] "
                f"ID={config_id} "
                "SUCCESS="
                f"{summary['success_count']}/"
                f"{rounds} "
                "AVG="
                f"{summary.get('average_download_mbps')} "
                "MEDIAN="
                f"{summary.get('median_download_mbps')} "
                "EXIT_IP="
                f"{summary.get('exit_ip')}"
            )

        except Exception:
            if claimed:
                release_health_claim_safely(
                    config_id=config_id,
                    operator=operator,
                    reason=(
                        "hot_promotion_exception"
                    ),
                )
            raise

    return {
        "promoted_ids": promoted_ids,
        "quality_rejected": (
            quality_rejected
        ),
        "long_check_rejected": (
            long_check_rejected
        ),
        "cooldown_skipped": (
            cooldown_skipped
        ),
        "claimed_skipped": (
            claimed_skipped
        ),
        "checked": checked,
    }


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
            "[HOT HEALTH RECOVERY "
            "SKIPPED] REASON=disabled"
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
        pool_status=None,
    )

    print(
        "[HOT HEALTH RECOVERY RESULT] "
        "CANDIDATES="
        f"{result.get('candidate_total')} "
        "RECOVERED="
        f"{result.get('recovered_total')} "
        "IDS="
        f"{result.get('recovered_ids')}"
    )

    return {
        **result,
        "enabled": True,
    }


def _manage_hot_locked(
    operator,
    target,
    max_candidates,
    min_quality_score,
    max_ping_avg_ms,
    min_quality_download_mbps,
    min_promotion_download_mbps,
    min_hot_keep_download_mbps,
    rounds,
    round_delay_seconds,
    min_successful_rounds,
    failure_limit,
    hot_min_age_minutes,
    promotion_retry_min_age_minutes,
    min_hot_capacity,
    correlated_failure_min_count,
    correlated_failure_ratio,
):
    hot_candidates = load_hot_candidates(
        operator=operator,
    )

    print("=" * 70)
    print("V2 HOT CONTROLLER")
    print("OPERATOR:", operator)
    print("TARGET:", target)
    print(
        "CURRENT HOT:",
        len(hot_candidates),
    )
    print("ROUNDS:", rounds)
    print(
        "MIN SUCCESSFUL ROUNDS:",
        min_successful_rounds,
    )
    print(
        "ROUND DELAY:",
        round_delay_seconds,
    )
    print(
        "MIN QUALITY DOWNLOAD:",
        min_quality_download_mbps,
    )
    print(
        "MIN HOT KEEP DOWNLOAD:",
        min_hot_keep_download_mbps,
    )
    print(
        "MIN PROMOTION DOWNLOAD:",
        min_promotion_download_mbps,
    )
    print(
        "FAILURE LIMIT:",
        failure_limit,
    )
    print(
        "HOT MIN AGE MINUTES:",
        hot_min_age_minutes,
    )
    print(
        "PROMOTION RETRY MIN "
        "AGE MINUTES:",
        promotion_retry_min_age_minutes,
    )
    print(
        "MIN HOT CAPACITY:",
        min_hot_capacity,
    )
    print(
        "CORRELATED FAILURE "
        "MIN COUNT:",
        correlated_failure_min_count,
    )
    print(
        "CORRELATED FAILURE RATIO:",
        correlated_failure_ratio,
    )
    print("=" * 70)

    direct_ip = get_direct_ip()

    print(
        "DIRECT IP:",
        direct_ip or "UNKNOWN",
    )

    hot_result = check_existing_hot(
        operator=operator,
        hot_candidates=hot_candidates,
        direct_ip=direct_ip,
        rounds=rounds,
        round_delay_seconds=(
            round_delay_seconds
        ),
        min_successful_rounds=(
            min_successful_rounds
        ),
        min_hot_keep_download_mbps=(
            min_hot_keep_download_mbps
        ),
        failure_limit=failure_limit,
        hot_min_age_minutes=(
            hot_min_age_minutes
        ),
        correlated_failure_min_count=(
            correlated_failure_min_count
        ),
        correlated_failure_ratio=(
            correlated_failure_ratio
        ),
        min_quality_score=(
            min_quality_score
        ),
        max_ping_avg_ms=(
            max_ping_avg_ms
        ),
        min_quality_download_mbps=(
            min_quality_download_mbps
        ),
    )

    pending_removals = (
        hot_result[
            "pending_removals"
        ]
    )
    unresolved_claim_ids = set(
        hot_result[
            "pending_claim_ids"
        ]
    )

    try:
        current_ids = hot_result[
            "current_ids"
        ]
        current_hot_count = len(
            current_ids
        )

        structural_removals = [
            item
            for item in pending_removals
            if item[
                "summary"
            ].get("structural")
        ]
        non_structural_removals = [
            item
            for item in pending_removals
            if not item[
                "summary"
            ].get("structural")
        ]

        base_vacancies = max(
            target
            - current_hot_count,
            0,
        )

        post_structural_capacity = max(
            current_hot_count
            - len(
                structural_removals
            ),
            0,
        )

        max_safe_non_structural_demotions = min(
            len(
                non_structural_removals
            ),
            max(
                post_structural_capacity
                + base_vacancies
                - min_hot_capacity,
                0,
            ),
        )

        needed = (
            base_vacancies
            + len(
                structural_removals
            )
            + max_safe_non_structural_demotions
        )

        reserve_candidates = []

        if needed > 0:
            reserve_candidates = (
                load_reserve_candidates(
                    operator=operator,
                    limit=max_candidates,
                )
            )

        print()
        print("=" * 70)
        print("PROMOTION PHASE")
        print(
            "CURRENT HOT HELD:",
            current_hot_count,
        )
        print(
            "BASE VACANCIES:",
            base_vacancies,
        )
        print(
            "STRUCTURAL REMOVALS:",
            len(
                structural_removals
            ),
        )
        print(
            "NON-STRUCTURAL "
            "REMOVALS:",
            len(
                non_structural_removals
            ),
        )
        print(
            "MAX SAFE NON-STRUCTURAL "
            "DEMOTIONS:",
            max_safe_non_structural_demotions,
        )
        print(
            "PROMOTIONS NEEDED:",
            needed,
        )
        print(
            "RESERVE CANDIDATES:",
            len(
                reserve_candidates
            ),
        )
        print("=" * 70)

        promotion_result = (
            promote_from_reserve(
                operator=operator,
                reserve_candidates=(
                    reserve_candidates
                ),
                needed=needed,
                direct_ip=direct_ip,
                rounds=rounds,
                round_delay_seconds=(
                    round_delay_seconds
                ),
                min_successful_rounds=(
                    min_successful_rounds
                ),
                min_quality_score=(
                    min_quality_score
                ),
                max_ping_avg_ms=(
                    max_ping_avg_ms
                ),
                min_quality_download_mbps=(
                    min_quality_download_mbps
                ),
                min_promotion_download_mbps=(
                    min_promotion_download_mbps
                ),
                failure_limit=(
                    failure_limit
                ),
                promotion_retry_min_age_minutes=(
                    promotion_retry_min_age_minutes
                ),
            )
        )

        promoted_ids = (
            promotion_result[
                "promoted_ids"
            ]
        )

        base_fill_count = min(
            base_vacancies,
            len(promoted_ids),
        )

        remaining_promotions = max(
            len(promoted_ids)
            - base_fill_count,
            0,
        )

        structural_replacement_count = min(
            len(
                structural_removals
            ),
            remaining_promotions,
        )

        remaining_promotions -= (
            structural_replacement_count
        )

        non_structural_demotion_count = min(
            len(
                non_structural_removals
            ),
            max_safe_non_structural_demotions,
            remaining_promotions,
        )

        demoted_ids = []
        structural_demoted_ids = []
        held_removal_ids = []

        # Structural/policy-invalid Hot rows are always quarantined.
        for item in structural_removals:
            candidate = item[
                "candidate"
            ]
            summary = item[
                "summary"
            ]
            config_id = candidate[
                "config_id"
            ]

            result = record_health_failure(
                config_id=config_id,
                operator=operator,
                error=item["error"],
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
                structural=True,
            )
            unresolved_claim_ids.discard(
                config_id
            )
            demoted_ids.append(
                config_id
            )
            structural_demoted_ids.append(
                config_id
            )

            print(
                "[HOT STRUCTURAL "
                "QUARANTINED] "
                f"ID={config_id} "
                "POOL="
                f"{result['pool_status']} "
                "FAIL_STREAK="
                f"{result['health_fail_streak']}"
            )

        for index, item in enumerate(
            non_structural_removals
        ):
            candidate = item[
                "candidate"
            ]
            summary = item[
                "summary"
            ]
            config_id = candidate[
                "config_id"
            ]

            if (
                index
                < non_structural_demotion_count
            ):
                result = (
                    record_health_failure(
                        config_id=config_id,
                        operator=operator,
                        error=item["error"],
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
                        structural=False,
                    )
                )
                unresolved_claim_ids.discard(
                    config_id
                )
                demoted_ids.append(
                    config_id
                )

                print(
                    "[HOT DEMOTED AFTER "
                    "REPLACEMENT] "
                    f"ID={config_id} "
                    "POOL="
                    f"{result['pool_status']} "
                    "FAIL_STREAK="
                    f"{result['health_fail_streak']}"
                )
                continue

            result = hold_hot_failure(
                config_id=config_id,
                operator=operator,
                error=item["error"],
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
                increment_fail_streak=True,
                hold_reason=(
                    "replacement_unavailable_"
                    "capacity_hold"
                ),
            )
            unresolved_claim_ids.discard(
                config_id
            )
            held_removal_ids.append(
                config_id
            )

            print(
                "[HOT REMOVAL HELD "
                "- NO REPLACEMENT] "
                f"ID={config_id} "
                "FAIL_STREAK="
                f"{result['health_fail_streak']}"
            )

        retained_ids = [
            config_id
            for config_id in current_ids
            if config_id
            not in set(demoted_ids)
        ]

        used_promotion_count = (
            base_fill_count
            + structural_replacement_count
            + non_structural_demotion_count
        )

        selected_ids = (
            retained_ids
            + promoted_ids[
                :used_promotion_count
            ]
        )[:target]

        capacity_degraded = (
            len(selected_ids)
            < min_hot_capacity
        )

        if capacity_degraded:
            print()
            print("!" * 70)
            print(
                "HOT CAPACITY DEGRADED"
            )
            print(
                "QUALITY POLICY PREVENTED "
                "KEEPING STRUCTURAL OR "
                "INVALID HOT ITEMS"
            )
            print(
                "SELECTED:",
                len(selected_ids),
            )
            print(
                "MINIMUM TARGET:",
                min_hot_capacity,
            )
            print("!" * 70)

        hot_items = replace_hot_set(
            operator=operator,
            config_ids=selected_ids,
        )

        if unresolved_claim_ids:
            raise RuntimeError(
                "Unresolved Hot Health claims "
                "after finalization: "
                f"{sorted(unresolved_claim_ids)}"
            )

        print()
        print("=" * 70)
        print(
            "V2 HOT CONTROLLER FINISHED"
        )
        print(
            "HOT HEALTHY:",
            hot_result[
                "healthy_count"
            ],
        )
        print(
            "HOT SUSPECT KEPT:",
            hot_result[
                "suspect_count"
            ],
        )
        print(
            "HOT STRUCTURAL "
            "QUARANTINED:",
            len(
                structural_demoted_ids
            ),
        )
        print(
            "HOT DEMOTED:",
            len(demoted_ids),
        )
        print(
            "HOT REMOVAL HELD:",
            len(
                held_removal_ids
            ),
        )
        print(
            "HOT CIRCUIT BREAKER:",
            hot_result[
                "circuit_breaker_active"
            ],
        )
        print(
            "HOT SKIPPED NOT DUE:",
            hot_result[
                "skipped_not_due_count"
            ],
        )
        print(
            "HOT SKIPPED CLAIMED:",
            hot_result[
                "skipped_claimed_count"
            ],
        )
        print(
            "PROMOTION CHECKED:",
            promotion_result[
                "checked"
            ],
        )
        print(
            "PROMOTION QUALITY "
            "SKIPPED:",
            promotion_result[
                "quality_rejected"
            ],
        )
        print(
            "PROMOTION LONG "
            "REJECTED:",
            promotion_result[
                "long_check_rejected"
            ],
        )
        print(
            "PROMOTION COOLDOWN "
            "SKIPPED:",
            promotion_result[
                "cooldown_skipped"
            ],
        )
        print(
            "PROMOTION CLAIMED "
            "SKIPPED:",
            promotion_result[
                "claimed_skipped"
            ],
        )
        print(
            "PROMOTED:",
            len(
                promoted_ids
            ),
        )
        print(
            "HOT READY:",
            len(hot_items),
        )
        print(
            "TARGET:",
            target,
        )
        print(
            "CAPACITY DEGRADED:",
            capacity_degraded,
        )

        for item in get_hot_items(
            operator
        ):
            print(
                "HOT SLOT="
                f"{item['hot_slot']} "
                "ID="
                f"{item['config_id']} "
                "HEALTH="
                f"{item['health_status']} "
                "AVG="
                f"{item['health_avg_download_mbps']} "
                "MEDIAN="
                f"{item['health_median_download_mbps']} "
                "FAIL_STREAK="
                f"{item['health_fail_streak']} "
                "EXIT_IP="
                f"{item['gate_exit_ip']}"
            )

        print("=" * 70)

        return {
            "operator": operator,
            "hot_items": hot_items,
            "hot_ready": len(
                hot_items
            ),
            "target": target,
            "capacity_degraded": (
                capacity_degraded
            ),
            "structural_quarantined_ids": (
                structural_demoted_ids
            ),
            "demoted_ids": demoted_ids,
            "held_removal_ids": (
                held_removal_ids
            ),
            "promoted_ids": (
                promoted_ids[
                    :used_promotion_count
                ]
            ),
            "unused_promoted_ids": (
                promoted_ids[
                    used_promotion_count:
                ]
            ),
            "hot_result": hot_result,
            "promotion_result": (
                promotion_result
            ),
        }

    except Exception:
        release_claim_ids(
            config_ids=(
                unresolved_claim_ids
            ),
            operator=operator,
            reason=(
                "hot_manager_finalization_"
                "exception"
            ),
        )
        raise

@pool_maintenance_serialized
def manage_hot(
    operator,
    target,
    max_candidates,
    min_quality_score,
    max_ping_avg_ms,
    min_quality_download_mbps,
    min_promotion_download_mbps,
    min_hot_keep_download_mbps,
    rounds,
    round_delay_seconds,
    min_successful_rounds,
    failure_limit,
    hot_min_age_minutes,
    promotion_retry_min_age_minutes,
    min_hot_capacity,
    correlated_failure_min_count,
    correlated_failure_ratio,
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
    target = max(
        min(
            int(target),
            5,
        ),
        1,
    )
    max_candidates = max(
        int(max_candidates),
        target,
    )
    rounds = max(
        int(rounds),
        1,
    )
    min_successful_rounds = max(
        min(
            int(
                min_successful_rounds
            ),
            rounds,
        ),
        1,
    )
    round_delay_seconds = max(
        float(
            round_delay_seconds
        ),
        0.0,
    )
    failure_limit = max(
        int(failure_limit),
        1,
    )
    hot_min_age_minutes = max(
        float(
            hot_min_age_minutes
        ),
        0.0,
    )
    promotion_retry_min_age_minutes = max(
        float(
            promotion_retry_min_age_minutes
        ),
        0.0,
    )
    min_hot_capacity = max(
        min(
            int(min_hot_capacity),
            target,
        ),
        1,
    )
    correlated_failure_min_count = max(
        int(
            correlated_failure_min_count
        ),
        1,
    )
    correlated_failure_ratio = min(
        max(
            float(
                correlated_failure_ratio
            ),
            0.0,
        ),
        1.0,
    )

    min_quality_score = max(
        float(min_quality_score),
        0.0,
    )
    max_ping_avg_ms = max(
        float(max_ping_avg_ms),
        0.0,
    )
    min_quality_download_mbps = max(
        float(
            min_quality_download_mbps
        ),
        1.5,
    )

    # Promotion and Hot retention may be stricter than Quality, but
    # never weaker than the official Quality Download floor.
    min_promotion_download_mbps = max(
        float(
            min_promotion_download_mbps
        ),
        min_quality_download_mbps,
    )
    min_hot_keep_download_mbps = max(
        float(
            min_hot_keep_download_mbps
        ),
        min_quality_download_mbps,
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
            stale_minutes=(
                stuck_minutes
            ),
            limit=recover_limit,
        )

        return _manage_hot_locked(
            operator=operator,
            target=target,
            max_candidates=(
                max_candidates
            ),
            min_quality_score=(
                min_quality_score
            ),
            max_ping_avg_ms=(
                max_ping_avg_ms
            ),
            min_quality_download_mbps=(
                min_quality_download_mbps
            ),
            min_promotion_download_mbps=(
                min_promotion_download_mbps
            ),
            min_hot_keep_download_mbps=(
                min_hot_keep_download_mbps
            ),
            rounds=rounds,
            round_delay_seconds=(
                round_delay_seconds
            ),
            min_successful_rounds=(
                min_successful_rounds
            ),
            failure_limit=(
                failure_limit
            ),
            hot_min_age_minutes=(
                hot_min_age_minutes
            ),
            promotion_retry_min_age_minutes=(
                promotion_retry_min_age_minutes
            ),
            min_hot_capacity=(
                min_hot_capacity
            ),
            correlated_failure_min_count=(
                correlated_failure_min_count
            ),
            correlated_failure_ratio=(
                correlated_failure_ratio
            ),
        )


def build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Run the independent V2 Hot "
            "health/fill controller."
        )
    )

    parser.add_argument(
        "--operator",
        required=True,
    )

    parser.add_argument(
        "--target",
        type=int,
        default=DEFAULT_HOT_TARGET,
    )

    parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--min-score",
        type=float,
        default=(
            DEFAULT_MIN_QUALITY_SCORE
        ),
    )

    parser.add_argument(
        "--max-ping",
        type=float,
        default=(
            DEFAULT_MAX_PING_AVG_MS
        ),
    )

    parser.add_argument(
        "--min-quality-download",
        type=float,
        default=(
            DEFAULT_MIN_QUALITY_DOWNLOAD_MBPS
        ),
    )

    parser.add_argument(
        "--min-gate-download",
        type=float,
        default=(
            DEFAULT_MIN_PROMOTION_DOWNLOAD_MBPS
        ),
        help=(
            "Minimum long-validation Download "
            "for Reserve -> Hot promotion."
        ),
    )

    parser.add_argument(
        "--min-hot-keep-download",
        type=float,
        default=(
            DEFAULT_MIN_HOT_KEEP_DOWNLOAD_MBPS
        ),
    )

    parser.add_argument(
        "--rounds",
        type=int,
        default=DEFAULT_HEALTH_ROUNDS,
    )

    parser.add_argument(
        "--round-delay",
        type=float,
        default=(
            DEFAULT_ROUND_DELAY_SECONDS
        ),
    )

    parser.add_argument(
        "--min-successful-rounds",
        type=int,
        default=(
            DEFAULT_MIN_SUCCESSFUL_ROUNDS
        ),
    )

    parser.add_argument(
        "--failure-limit",
        type=int,
        default=(
            DEFAULT_HEALTH_FAILURE_LIMIT
        ),
    )

    parser.add_argument(
        "--hot-min-age-minutes",
        type=float,
        default=(
            DEFAULT_HOT_MIN_AGE_MINUTES
        ),
    )

    parser.add_argument(
        "--promotion-retry-min-age-minutes",
        type=float,
        default=(
            DEFAULT_PROMOTION_RETRY_MIN_AGE_MINUTES
        ),
    )

    parser.add_argument(
        "--min-hot-capacity",
        type=int,
        default=(
            DEFAULT_MIN_HOT_CAPACITY
        ),
    )

    parser.add_argument(
        "--correlated-failure-min-count",
        type=int,
        default=(
            DEFAULT_CORRELATED_FAILURE_MIN_COUNT
        ),
    )

    parser.add_argument(
        "--correlated-failure-ratio",
        type=float,
        default=(
            DEFAULT_CORRELATED_FAILURE_RATIO
        ),
    )

    parser.add_argument(
        "--recover-stuck",
        dest="recover_stuck",
        action="store_true",
        default=DEFAULT_RECOVER_STUCK,
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

    manage_hot(
        operator=args.operator,
        target=args.target,
        max_candidates=(
            args.max_candidates
        ),
        min_quality_score=(
            args.min_score
        ),
        max_ping_avg_ms=(
            args.max_ping
        ),
        min_quality_download_mbps=(
            args.min_quality_download
        ),
        min_promotion_download_mbps=(
            args.min_gate_download
        ),
        min_hot_keep_download_mbps=(
            args.min_hot_keep_download
        ),
        rounds=args.rounds,
        round_delay_seconds=(
            args.round_delay
        ),
        min_successful_rounds=(
            args.min_successful_rounds
        ),
        failure_limit=(
            args.failure_limit
        ),
        hot_min_age_minutes=(
            args.hot_min_age_minutes
        ),
        promotion_retry_min_age_minutes=(
            args.promotion_retry_min_age_minutes
        ),
        min_hot_capacity=(
            args.min_hot_capacity
        ),
        correlated_failure_min_count=(
            args.correlated_failure_min_count
        ),
        correlated_failure_ratio=(
            args.correlated_failure_ratio
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
