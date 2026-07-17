from datetime import datetime, timedelta

from sqlalchemy import or_

from database.db import SessionLocal
from database.models import ConfigPool


VALID_POOL_STATUSES = {
    "none",
    "reserve",
    "hot",
    "quarantined",
}

VALID_HEALTH_STATUSES = {
    "unknown",
    "checking",
    "healthy",
    "suspect",
    "failed",
}


def normalize_operator(operator):
    value = str(operator or "").strip().lower()

    if not value:
        raise ValueError("operator is required")

    return value

def normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def normalize_limit(limit):
    if limit is None:
        return None

    value = int(limit)

    if value < 0:
        raise ValueError(
            "limit must be greater than or equal to zero"
        )

    return value



def safe_float(value):
    if value is None:
        return None

    try:
        number = float(value)

    except (TypeError, ValueError):
        return None

    if number < 0:
        return None

    return number



def pool_row_to_dict(row):
    if row is None:
        return None

    return {
        "id": row.id,
        "config_id": row.config_id,
        "operator": row.operator,
        "pool_status": row.pool_status,
        "gate_status": row.gate_status,
        "gate_checked_at": (
            row.gate_checked_at.isoformat()
            if row.gate_checked_at
            else None
        ),
        "gate_error": row.gate_error,
        "gate_exit_ip": row.gate_exit_ip,
        "gate_download_mbps": row.gate_download_mbps,
        "health_status": row.health_status,
        "health_checked_at": (
            row.health_checked_at.isoformat()
            if row.health_checked_at
            else None
        ),
        "health_success_streak": (
            row.health_success_streak or 0
        ),
        "health_fail_streak": (
            row.health_fail_streak or 0
        ),
        "health_avg_download_mbps": (
            row.health_avg_download_mbps
        ),
        "health_median_download_mbps": (
            row.health_median_download_mbps
        ),
        "health_error": row.health_error,
        "hot_slot": row.hot_slot,
    }


def get_pool_item(config_id, operator):
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)

    session = SessionLocal()

    try:
        row = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id == config_id,
                ConfigPool.operator == operator,
            )
            .first()
        )

        return pool_row_to_dict(row)

    finally:
        session.close()



def upsert_gate_result(config_id, operator, result):
    """
    Persist the final result of an already-claimed Admission Gate.

    Preconditions:
        - mark_gate_testing() has created/claimed the row.
        - pool_status is still none.
        - gate_status is still testing.

    PASS:
        pool_status = reserve
        gate_status = accepted

    FAIL:
        pool_status = quarantined
        gate_status = rejected
    """
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)
    result = dict(result or {})

    session = SessionLocal()

    try:
        row = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id == config_id,
                ConfigPool.operator == operator,
            )
            .first()
        )

        if row is None:
            raise LookupError(
                "gate claim not found: "
                f"config_id={config_id} "
                f"operator={operator}"
            )

        if (
            row.pool_status != "none"
            or row.gate_status != "testing"
        ):
            raise RuntimeError(
                "gate result cannot be persisted because "
                "the claim is not owned by this pipeline: "
                f"config_id={config_id} "
                f"operator={operator} "
                f"pool_status={row.pool_status} "
                f"gate_status={row.gate_status}"
            )

        accepted = (
            result.get("ok") is True
        )

        row.gate_status = (
            "accepted"
            if accepted
            else "rejected"
        )
        row.pool_status = (
            "reserve"
            if accepted
            else "quarantined"
        )
        row.gate_checked_at = datetime.utcnow()
        row.gate_error = (
            None
            if accepted
            else str(
                result.get("error")
                or "gate_rejected"
            )
        )

        exit_ip = result.get("exit_ip")

        if exit_ip is not None:
            exit_ip = (
                str(exit_ip).strip()
                or None
            )

        row.gate_exit_ip = exit_ip
        row.gate_download_mbps = safe_float(
            result.get("download_mbps")
        )

        # A new Gate result starts a new Pool-health lifecycle.
        row.health_status = "unknown"
        row.health_checked_at = None
        row.health_success_streak = 0
        row.health_fail_streak = 0
        row.health_avg_download_mbps = None
        row.health_median_download_mbps = None
        row.health_error = None
        row.hot_slot = None

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def mark_gate_testing(config_id, operator):
    """
    Claim the Reserve Admission Gate for one config.

    Existing Reserve, Hot, or Quarantined ownership is never overwritten.
    """
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)

    session = SessionLocal()

    try:
        row = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id == config_id,
                ConfigPool.operator == operator,
            )
            .first()
        )

        if row is None:
            row = ConfigPool(
                config_id=config_id,
                operator=operator,
                pool_status="none",
                gate_status="not_tested",
                health_status="unknown",
                health_success_streak=0,
                health_fail_streak=0,
            )
            session.add(row)
            session.flush()

        if row.pool_status != "none":
            raise RuntimeError(
                "gate claim refused because the config "
                "is already owned by a Pool: "
                f"config_id={config_id} "
                f"operator={operator} "
                f"pool_status={row.pool_status}"
            )

        if row.gate_status == "testing":
            raise RuntimeError(
                "gate claim refused because another "
                "Gate test is already active: "
                f"config_id={config_id} "
                f"operator={operator}"
            )

        row.gate_status = "testing"
        row.gate_checked_at = datetime.utcnow()
        row.gate_error = None
        row.gate_exit_ip = None
        row.gate_download_mbps = None
        row.hot_slot = None

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def release_gate_testing(
    config_id,
    operator,
    reason="raw_to_reserve_gate_exception",
):
    """
    Release one unfinished Reserve Admission Gate claim.

    Accepted Reserve, Hot, and Quarantined rows are never modified.
    """
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)

    reason_text = str(
        reason
        or "raw_to_reserve_gate_exception"
    ).strip()

    if not reason_text:
        reason_text = (
            "raw_to_reserve_gate_exception"
        )

    session = SessionLocal()

    try:
        updated = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id == config_id,
                ConfigPool.operator == operator,
                ConfigPool.gate_status == "testing",
                ConfigPool.pool_status == "none",
            )
            .update(
                {
                    ConfigPool.gate_status: "not_tested",
                    ConfigPool.gate_checked_at: datetime.utcnow(),
                    ConfigPool.gate_error: (
                        "gate_claim_released:"
                        f"{reason_text}"
                    ),
                    ConfigPool.gate_exit_ip: None,
                    ConfigPool.gate_download_mbps: None,
                    ConfigPool.hot_slot: None,
                },
                synchronize_session=False,
            )
        )

        session.commit()

        return {
            "config_id": config_id,
            "operator": operator,
            "released": updated == 1,
            "reason": reason_text,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def recover_stuck_gate_claims(
    operator,
    stale_minutes=30,
    limit=100,
):
    """
    Recover stale Admission Gate claims owned by no Pool.
    """
    operator = normalize_operator(operator)
    stale_minutes = max(
        float(stale_minutes),
        1.0,
    )
    limit = max(
        int(limit),
        1,
    )

    cutoff = (
        datetime.utcnow()
        - timedelta(
            minutes=stale_minutes
        )
    )

    session = SessionLocal()

    try:
        candidates = (
            session.query(
                ConfigPool.id,
                ConfigPool.config_id,
                ConfigPool.gate_checked_at,
            )
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.gate_status == "testing",
                ConfigPool.pool_status == "none",
                or_(
                    ConfigPool.gate_checked_at.is_(None),
                    ConfigPool.gate_checked_at <= cutoff,
                ),
            )
            .order_by(
                ConfigPool.gate_checked_at
                .is_(None)
                .desc(),
                ConfigPool.gate_checked_at.asc(),
                ConfigPool.id.asc(),
            )
            .limit(limit)
            .all()
        )

        recovered_ids = []
        recovered_at = datetime.utcnow()

        for candidate in candidates:
            updated = (
                session.query(ConfigPool)
                .filter(
                    ConfigPool.id == candidate.id,
                    ConfigPool.operator == operator,
                    ConfigPool.gate_status == "testing",
                    ConfigPool.pool_status == "none",
                    or_(
                        ConfigPool.gate_checked_at.is_(None),
                        ConfigPool.gate_checked_at <= cutoff,
                    ),
                )
                .update(
                    {
                        ConfigPool.gate_status: "not_tested",
                        ConfigPool.gate_checked_at: recovered_at,
                        ConfigPool.gate_error: (
                            "recovered_stuck_gate_claim"
                        ),
                        ConfigPool.gate_exit_ip: None,
                        ConfigPool.gate_download_mbps: None,
                        ConfigPool.hot_slot: None,
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                recovered_ids.append(
                    int(candidate.config_id)
                )

        session.commit()

        return {
            "operator": operator,
            "stale_minutes": stale_minutes,
            "cutoff": cutoff.isoformat(),
            "candidate_total": len(candidates),
            "recovered_total": len(recovered_ids),
            "recovered_ids": recovered_ids,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def get_reserve_count(operator):
    operator = normalize_operator(operator)

    session = SessionLocal()

    try:
        return (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == "reserve",
                ConfigPool.gate_status == "accepted",
            )
            .count()
        )

    finally:
        session.close()


def get_reserve_items(operator, limit=None):
    operator = normalize_operator(operator)
    limit = normalize_limit(limit)

    session = SessionLocal()

    try:
        query = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == "reserve",
                ConfigPool.gate_status == "accepted",
            )
            .order_by(
                ConfigPool.gate_checked_at.desc(),
                ConfigPool.gate_download_mbps.desc(),
                ConfigPool.id.desc(),
            )
        )

        if limit is not None:
            query = query.limit(limit)

        rows = query.all()

        return [
            pool_row_to_dict(row)
            for row in rows
        ]

    finally:
        session.close()



def set_pool_status(
    config_id,
    operator,
    pool_status,
    hot_slot=None,
):
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)
    pool_status = str(
        pool_status or ""
    ).strip().lower()

    if pool_status not in VALID_POOL_STATUSES:
        raise ValueError(
            f"invalid pool_status: {pool_status}"
        )

    if hot_slot is not None:
        hot_slot = int(hot_slot)

        if hot_slot <= 0:
            raise ValueError(
                "hot_slot must be greater than zero"
            )

    session = SessionLocal()

    try:
        row = _get_required_pool_row(
            session,
            config_id,
            operator,
        )

        if (
            pool_status in {"reserve", "hot"}
            and row.gate_status != "accepted"
        ):
            raise RuntimeError(
                "reserve/hot status requires "
                "gate_status=accepted"
            )

        if (
            pool_status == "hot"
            and hot_slot is None
        ):
            raise ValueError(
                "hot_slot is required for hot status"
            )

        row.pool_status = pool_status
        row.hot_slot = (
            hot_slot
            if pool_status == "hot"
            else None
        )

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def get_hot_count(operator):
    operator = normalize_operator(operator)
    session = SessionLocal()

    try:
        return (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == "hot",
                ConfigPool.gate_status == "accepted",
            )
            .count()
        )

    finally:
        session.close()


def get_hot_items(operator):
    operator = normalize_operator(operator)
    session = SessionLocal()

    try:
        rows = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == "hot",
                ConfigPool.gate_status == "accepted",
            )
            .order_by(
                ConfigPool.hot_slot.asc(),
                ConfigPool.id.asc(),
            )
            .all()
        )

        return [
            pool_row_to_dict(row)
            for row in rows
        ]

    finally:
        session.close()


def replace_hot_set(operator, config_ids):
    """
    Replace one operator's Hot set in a single transaction.

    Rules:
        - maximum five configs;
        - Gate must be accepted;
        - candidate must currently be Reserve or Hot;
        - Slots start at 1;
        - unselected previous Hot rows return to Reserve.
    """
    operator = normalize_operator(operator)

    normalized_ids = []
    seen_ids = set()

    for value in config_ids or []:
        config_id = normalize_config_id(
            value
        )

        if config_id in seen_ids:
            continue

        seen_ids.add(config_id)
        normalized_ids.append(config_id)

    if len(normalized_ids) > 5:
        raise ValueError(
            "hot pool cannot contain more than 5 configs"
        )

    session = SessionLocal()

    try:
        selected_rows = []

        if normalized_ids:
            rows = (
                session.query(ConfigPool)
                .filter(
                    ConfigPool.operator == operator,
                    ConfigPool.config_id.in_(
                        normalized_ids
                    ),
                    ConfigPool.gate_status == "accepted",
                    ConfigPool.pool_status.in_(
                        ["reserve", "hot"]
                    ),
                )
                .all()
            )

            row_by_config_id = {
                int(row.config_id): row
                for row in rows
            }

            missing_ids = [
                config_id
                for config_id in normalized_ids
                if config_id not in row_by_config_id
            ]

            if missing_ids:
                raise LookupError(
                    "hot candidates are missing or not accepted: "
                    + ",".join(
                        str(config_id)
                        for config_id in missing_ids
                    )
                )

            selected_rows = [
                row_by_config_id[config_id]
                for config_id in normalized_ids
            ]

        current_hot_rows = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == "hot",
            )
            .all()
        )

        # Free all existing slots first. The flush prevents a transient
        # unique-slot collision while the new order is assigned.
        for row in current_hot_rows:
            row.hot_slot = None

            if row.gate_status == "accepted":
                row.pool_status = "reserve"
            else:
                row.pool_status = "quarantined"

        session.flush()

        for hot_slot, row in enumerate(
            selected_rows,
            start=1,
        ):
            row.pool_status = "hot"
            row.hot_slot = hot_slot

        session.commit()

        for row in selected_rows:
            session.refresh(row)

        return [
            pool_row_to_dict(row)
            for row in sorted(
                selected_rows,
                key=lambda item: item.hot_slot,
            )
        ]

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def _get_required_pool_row(
    session,
    config_id,
    operator,
):
    config_id = normalize_config_id(
        config_id
    )

    row = (
        session.query(ConfigPool)
        .filter(
            ConfigPool.config_id == config_id,
            ConfigPool.operator == operator,
        )
        .first()
    )

    if row is None:
        raise LookupError(
            "pool item not found: "
            f"config_id={config_id} "
            f"operator={operator}"
        )

    return row



def mark_health_checking(
    config_id,
    operator,
):
    """
    Claim one Reserve/Hot item for a Health check.
    """
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)

    session = SessionLocal()

    try:
        row = _get_required_pool_row(
            session,
            config_id,
            operator,
        )

        if (
            row.pool_status not in {"reserve", "hot"}
            or row.gate_status != "accepted"
        ):
            raise RuntimeError(
                "health claim requires an accepted "
                "Reserve or Hot item"
            )

        if row.health_status == "checking":
            raise RuntimeError(
                "health check is already active: "
                f"config_id={config_id} "
                f"operator={operator}"
            )

        row.health_status = "checking"
        row.health_checked_at = datetime.utcnow()
        row.health_error = None

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()

def release_health_checking(
    config_id,
    operator,
    reason="health_worker_exception",
):
    """
    Release one unfinished Health claim without changing Pool ownership.
    """
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)
    reason_text = str(
        reason or "health_worker_exception"
    ).strip()

    if not reason_text:
        reason_text = "health_worker_exception"

    session = SessionLocal()

    try:
        updated = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.config_id == config_id,
                ConfigPool.operator == operator,
                ConfigPool.pool_status.in_(
                    ["reserve", "hot"]
                ),
                ConfigPool.gate_status == "accepted",
                ConfigPool.health_status == "checking",
            )
            .update(
                {
                    ConfigPool.health_status: "unknown",
                    ConfigPool.health_checked_at: datetime.utcnow(),
                    ConfigPool.health_error: (
                        "health_claim_released:"
                        f"{reason_text}"
                    ),
                },
                synchronize_session=False,
            )
        )

        session.commit()

        return {
            "config_id": config_id,
            "operator": operator,
            "released": updated == 1,
            "reason": reason_text,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def recover_stuck_health_claims(
    operator,
    stale_minutes=30,
    limit=100,
    pool_status=None,
):
    """
    Recover stale Health claims for accepted Reserve/Hot rows.
    """
    operator = normalize_operator(operator)
    stale_minutes = max(
        float(stale_minutes),
        1.0,
    )
    limit = max(
        int(limit),
        1,
    )

    if pool_status is not None:
        pool_status = str(
            pool_status
        ).strip().lower()

        if pool_status not in {"reserve", "hot"}:
            raise ValueError(
                "pool_status must be reserve, hot, or None"
            )

    cutoff = (
        datetime.utcnow()
        - timedelta(
            minutes=stale_minutes
        )
    )

    session = SessionLocal()

    try:
        query = (
            session.query(
                ConfigPool.id,
                ConfigPool.config_id,
                ConfigPool.health_checked_at,
            )
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status.in_(
                    ["reserve", "hot"]
                ),
                ConfigPool.gate_status == "accepted",
                ConfigPool.health_status == "checking",
                or_(
                    ConfigPool.health_checked_at.is_(None),
                    ConfigPool.health_checked_at <= cutoff,
                ),
            )
        )

        if pool_status is not None:
            query = query.filter(
                ConfigPool.pool_status == pool_status
            )

        candidates = (
            query
            .order_by(
                ConfigPool.health_checked_at
                .is_(None)
                .desc(),
                ConfigPool.health_checked_at.asc(),
                ConfigPool.id.asc(),
            )
            .limit(limit)
            .all()
        )

        recovered_ids = []
        recovered_at = datetime.utcnow()

        for candidate in candidates:
            updated = (
                session.query(ConfigPool)
                .filter(
                    ConfigPool.id == candidate.id,
                    ConfigPool.operator == operator,
                    ConfigPool.pool_status.in_(
                        ["reserve", "hot"]
                    ),
                    ConfigPool.gate_status == "accepted",
                    ConfigPool.health_status == "checking",
                    or_(
                        ConfigPool.health_checked_at.is_(None),
                        ConfigPool.health_checked_at <= cutoff,
                    ),
                )
                .update(
                    {
                        ConfigPool.health_status: "unknown",
                        ConfigPool.health_checked_at: recovered_at,
                        ConfigPool.health_error: (
                            "recovered_stuck_health_claim"
                        ),
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                recovered_ids.append(
                    int(candidate.config_id)
                )

        session.commit()

        return {
            "operator": operator,
            "pool_status": pool_status,
            "stale_minutes": stale_minutes,
            "cutoff": cutoff.isoformat(),
            "candidate_total": len(candidates),
            "recovered_total": len(recovered_ids),
            "recovered_ids": recovered_ids,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()




def record_health_success(
    config_id,
    operator,
    avg_download_mbps,
    median_download_mbps,
):
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)

    session = SessionLocal()

    try:
        row = _get_required_pool_row(
            session,
            config_id,
            operator,
        )

        if (
            row.pool_status not in {"reserve", "hot"}
            or row.gate_status != "accepted"
        ):
            raise RuntimeError(
                "health success requires an accepted "
                "Reserve or Hot item"
            )

        row.health_status = "healthy"
        row.health_checked_at = datetime.utcnow()
        row.health_success_streak = (
            int(row.health_success_streak or 0)
            + 1
        )
        row.health_fail_streak = 0
        row.health_avg_download_mbps = safe_float(
            avg_download_mbps
        )
        row.health_median_download_mbps = safe_float(
            median_download_mbps
        )
        row.health_error = None

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def record_health_failure(
    config_id,
    operator,
    error,
    avg_download_mbps=None,
    median_download_mbps=None,
    failure_limit=2,
    structural=False,
):
    operator = normalize_operator(operator)
    config_id = normalize_config_id(config_id)
    failure_limit = max(
        1,
        int(failure_limit),
    )

    session = SessionLocal()

    try:
        row = _get_required_pool_row(
            session,
            config_id,
            operator,
        )

        if (
            row.pool_status not in {"reserve", "hot"}
            or row.gate_status != "accepted"
        ):
            raise RuntimeError(
                "health failure requires an accepted "
                "Reserve or Hot item"
            )

        row.health_checked_at = datetime.utcnow()
        row.health_success_streak = 0
        row.health_fail_streak = (
            int(row.health_fail_streak or 0)
            + 1
        )
        row.health_avg_download_mbps = safe_float(
            avg_download_mbps
        )
        row.health_median_download_mbps = safe_float(
            median_download_mbps
        )
        row.health_error = str(
            error or "health_check_failed"
        )

        permanently_failed = (
            bool(structural)
            or row.health_fail_streak
            >= failure_limit
        )

        if permanently_failed:
            row.health_status = "failed"
            row.pool_status = "quarantined"
            row.hot_slot = None

        else:
            row.health_status = "suspect"

        session.commit()
        session.refresh(row)

        return pool_row_to_dict(row)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()



def get_health_items(
    operator,
    pool_status,
    limit=None,
    health_statuses=None,
):
    operator = normalize_operator(operator)
    pool_status = str(
        pool_status or ""
    ).strip().lower()
    limit = normalize_limit(limit)

    if pool_status not in {"reserve", "hot"}:
        raise ValueError(
            "health pool_status must be "
            "'reserve' or 'hot'"
        )

    if health_statuses is not None:
        health_statuses = {
            str(value).strip().lower()
            for value in health_statuses
        }

        invalid_statuses = (
            health_statuses
            - VALID_HEALTH_STATUSES
        )

        if invalid_statuses:
            raise ValueError(
                "invalid health statuses: "
                f"{sorted(invalid_statuses)}"
            )

    session = SessionLocal()

    try:
        query = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == operator,
                ConfigPool.pool_status == pool_status,
                ConfigPool.gate_status == "accepted",
            )
        )

        if health_statuses:
            query = query.filter(
                ConfigPool.health_status.in_(
                    list(health_statuses)
                )
            )

        query = query.order_by(
            ConfigPool.health_checked_at
            .is_(None)
            .desc(),
            ConfigPool.health_checked_at.asc(),
            ConfigPool.id.asc(),
        )

        if limit is not None:
            query = query.limit(limit)

        rows = query.all()

        return [
            pool_row_to_dict(row)
            for row in rows
        ]

    finally:
        session.close()
