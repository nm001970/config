from datetime import datetime, timedelta

from sqlalchemy import not_, or_, select

from database.db import SessionLocal
from database.models import Config, ConfigPool


SUPPORTED_CONNECTIVITY_PROTOCOLS = [
    "vless",
    "vmess",
    "ss",
    "trojan",
]

POOL_OWNED_STATUSES = [
    "reserve",
    "hot",
    "quarantined",
]

FINAL_CONNECTIVITY_STATUSES = {
    "alive",
    "dead",
    "error",
}


def _normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def _normalize_limit(limit):
    if limit is None:
        return None

    value = int(limit)

    if value <= 0:
        return 0

    return value


def _normalize_latency(latency):
    try:
        value = float(latency)

    except (TypeError, ValueError):
        return 0.0

    if value < 0:
        return 0.0

    return value


def _unowned_pool_subquery():
    """
    Return config IDs currently owned by any active delivery pool.

    Legacy helpers do not receive an operator, so the safe behavior is
    to exclude ownership for every operator.
    """
    return (
        select(ConfigPool.config_id)
        .where(
            ConfigPool.pool_status.in_(
                POOL_OWNED_STATUSES
            )
        )
    )


def reset_quality_fields(
    row,
    reason=None,
):
    """
    Invalidate stale Quality data after a fresh alive Connectivity pass.
    """
    row.quality_status = "not_tested"
    row.quality_score = 0
    row.ping_min_ms = None
    row.ping_avg_ms = None
    row.download_mbps = None
    row.upload_mbps = None
    row.quality_error = reason
    row.last_quality_checked = None


def reject_quality_fields(
    row,
    reason=None,
):
    """
    Invalidate Quality when Connectivity is dead or errors.
    """
    row.quality_status = "rejected"
    row.quality_score = 0
    row.ping_min_ms = None
    row.ping_avg_ms = None
    row.download_mbps = None
    row.upload_mbps = None
    row.quality_error = reason
    row.last_quality_checked = None


def get_next_config():
    session = SessionLocal()

    try:
        return (
            session.query(Config)
            .filter(Config.status == "new")
            .order_by(Config.id.desc())
            .first()
        )

    finally:
        session.close()


def update_result(
    config_id,
    status,
    latency,
    error=None,
    exist_ip=None,
    exit_ip=None,
):
    """
    Persist one final Connectivity result.

    ``exist_ip`` is retained as a backward-compatible keyword because
    older callers use that spelling. New callers may use ``exit_ip``.
    When both are supplied, ``exit_ip`` takes precedence.
    """
    config_id = _normalize_config_id(
        config_id
    )
    status = str(
        status or ""
    ).strip().lower()

    if status not in FINAL_CONNECTIVITY_STATUSES:
        raise ValueError(
            "status must be one of: "
            "alive, dead, error"
        )

    latency = _normalize_latency(
        latency
    )

    resolved_exit_ip = (
        exit_ip
        if exit_ip is not None
        else exist_ip
    )

    if resolved_exit_ip is not None:
        resolved_exit_ip = (
            str(resolved_exit_ip).strip()
            or None
        )

    error_text = (
        str(error)
        if error is not None
        else None
    )

    session = SessionLocal()

    try:
        row = (
            session.query(Config)
            .filter(Config.id == config_id)
            .first()
        )

        if row is None:
            raise LookupError(
                f"Config not found: {config_id}"
            )

        now = datetime.utcnow()

        row.status = status
        row.latency = (
            latency
            if status == "alive"
            else 0.0
        )
        row.last_error = (
            None
            if status == "alive"
            else (
                error_text
                or f"connectivity_{status}"
            )
        )
        row.exit_ip = (
            resolved_exit_ip
            if status == "alive"
            else None
        )
        row.last_checked = now

        if status == "alive":
            row.success_count = (
                int(row.success_count or 0)
                + 1
            )

            # Every fresh Connectivity pass invalidates any previous
            # Quality result. The current pipeline immediately runs a
            # new Quality test after this commit.
            reset_quality_fields(
                row,
                reason=(
                    "quality_reset_after_"
                    "connectivity_pass"
                ),
            )

        else:
            row.fail_count = (
                int(row.fail_count or 0)
                + 1
            )

            reject_quality_fields(
                row,
                reason=(
                    row.last_error
                    or f"connectivity_{status}"
                ),
            )

        session.commit()

        return {
            "config_id": config_id,
            "status": status,
            "latency": row.latency,
            "exit_ip": row.exit_ip,
            "error": row.last_error,
            "updated": True,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def get_next_vless():
    session = SessionLocal()

    try:
        return (
            session.query(Config)
            .filter(
                Config.status == "new",
                Config.protocol == "vless",
            )
            .order_by(Config.id.desc())
            .first()
        )

    finally:
        session.close()


def get_vless_batch(limit=None):
    limit = _normalize_limit(
        limit
    )
    session = SessionLocal()

    try:
        query = (
            session.query(Config)
            .filter(
                Config.status == "new",
                Config.protocol == "vless",
            )
            .order_by(Config.id.desc())
        )

        if limit is not None:
            query = query.limit(limit)

        return query.all()

    finally:
        session.close()


def get_top_configs(limit=50):
    limit = max(
        int(limit),
        0,
    )
    session = SessionLocal()

    try:
        return (
            session.query(Config)
            .filter(Config.status == "alive")
            .order_by(
                Config.latency.asc(),
                Config.id.desc(),
            )
            .limit(limit)
            .all()
        )

    finally:
        session.close()


def claim_next_vless():
    """
    Legacy claim helper.

    It excludes configs owned by any Reserve, Hot, or Quarantine pool.
    The Smart cycle does not use this function.
    """
    session = SessionLocal()

    try:
        owned_ids = (
            _unowned_pool_subquery()
        )

        while True:
            candidate = (
                session.query(Config.id)
                .filter(
                    Config.status == "new",
                    Config.protocol == "vless",
                    not_(
                        Config.id.in_(owned_ids)
                    ),
                )
                .order_by(Config.id.desc())
                .first()
            )

            if candidate is None:
                return None

            config_id = int(
                candidate[0]
            )
            now = datetime.utcnow()

            updated = (
                session.query(Config)
                .filter(
                    Config.id == config_id,
                    Config.status == "new",
                    Config.protocol == "vless",
                    not_(
                        Config.id.in_(owned_ids)
                    ),
                )
                .update(
                    {
                        Config.status: "testing",
                        Config.last_checked: now,
                        Config.last_error: None,
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                session.commit()

                row = (
                    session.query(Config)
                    .filter(
                        Config.id == config_id
                    )
                    .first()
                )

                if row is not None:
                    session.expunge(row)

                return row

            session.rollback()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def reset_stuck_configs(
    timeout_minutes=2,
):
    """
    Legacy global recovery helper.

    The operator-aware Smart cycle uses
    recover_stuck_raw_to_reserve_claims() instead. This fallback only
    resets stale testing rows that are not owned by any delivery pool.
    """
    timeout_minutes = max(
        float(timeout_minutes),
        1.0,
    )

    cutoff = (
        datetime.utcnow()
        - timedelta(
            minutes=timeout_minutes
        )
    )

    session = SessionLocal()

    try:
        owned_ids = (
            _unowned_pool_subquery()
        )

        rows = (
            session.query(Config)
            .filter(
                Config.status == "testing",
                Config.protocol.in_(
                    SUPPORTED_CONNECTIVITY_PROTOCOLS
                ),
                or_(
                    Config.last_checked.is_(None),
                    Config.last_checked <= cutoff,
                ),
                not_(
                    Config.id.in_(owned_ids)
                ),
            )
            .order_by(
                Config.last_checked
                .is_(None)
                .desc(),
                Config.last_checked.asc(),
                Config.id.asc(),
            )
            .all()
        )

        for row in rows:
            row.status = "not_tested"
            row.last_checked = None
            row.last_error = (
                "recovered_from_stuck_testing"
            )

        count = len(rows)
        session.commit()

        # Preserve the historical tuple return contract.
        return count, 0

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def claim_supported_connectivity_batch(
    limit=None,
):
    """
    Legacy batch claim helper.

    The Smart cycle does not use this function. It remains available for
    old scripts and excludes configs owned by any delivery pool.
    """
    limit = _normalize_limit(
        limit
    )

    if limit == 0:
        return []

    session = SessionLocal()

    try:
        owned_ids = (
            _unowned_pool_subquery()
        )

        query = (
            session.query(Config.id)
            .filter(
                Config.status == "new",
                Config.protocol.in_(
                    SUPPORTED_CONNECTIVITY_PROTOCOLS
                ),
                not_(
                    Config.id.in_(owned_ids)
                ),
            )
            .order_by(Config.id.desc())
        )

        if limit is not None:
            query = query.limit(limit)

        candidate_ids = [
            int(row[0])
            for row in query.all()
        ]

        claimed_ids = []
        now = datetime.utcnow()

        for config_id in candidate_ids:
            updated = (
                session.query(Config)
                .filter(
                    Config.id == config_id,
                    Config.status == "new",
                    Config.protocol.in_(
                        SUPPORTED_CONNECTIVITY_PROTOCOLS
                    ),
                    not_(
                        Config.id.in_(owned_ids)
                    ),
                )
                .update(
                    {
                        Config.status: "testing",
                        Config.last_checked: now,
                        Config.last_error: None,
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                claimed_ids.append(
                    config_id
                )

        session.commit()
        return claimed_ids

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def claim_vless_batch(limit=None):
    """
    Historical alias retained for compatibility.
    """
    return claim_supported_connectivity_batch(
        limit
    )


__all__ = [
    "SUPPORTED_CONNECTIVITY_PROTOCOLS",
    "reset_quality_fields",
    "reject_quality_fields",
    "get_next_config",
    "update_result",
    "get_next_vless",
    "get_vless_batch",
    "get_top_configs",
    "claim_next_vless",
    "reset_stuck_configs",
    "claim_supported_connectivity_batch",
    "claim_vless_batch",
]