from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import or_, not_, select

from database.db import SessionLocal
from database.models import Config, ConfigPool


SUPPORTED_CONNECTIVITY_PROTOCOLS = [
    "vless",
    "vmess",
    "ss",
    "trojan",
]

CONNECTIVITY_EXCLUDED_STATUSES = [
    "testing",
    "archived",
]

# These rows are owned by the independent Pool cycles and must not be
# reclaimed by the Raw -> Reserve cycle.
RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES = [
    "reserve",
    "hot",
    "quarantined",
]


def get_config_by_id(config_id):
    session = SessionLocal()

    try:
        return (
            session.query(Config)
            .filter(
                Config.id == int(config_id)
            )
            .first()
        )

    finally:
        session.close()


def _safe_iso(value):
    if value is None:
        return None

    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _row_to_dict(row):
    return {
        "id": row.id,
        "protocol": row.protocol,
        "status": row.status,
        "quality_status": row.quality_status,
        "latency": row.latency,
        "quality_score": row.quality_score,
        "last_checked": _safe_iso(row.last_checked),
        "last_quality_checked": _safe_iso(row.last_quality_checked),
        "success_count": row.success_count,
        "fail_count": row.fail_count,
    }


def _summarize_rows(rows):
    status_counter = Counter()
    protocol_counter = Counter()

    for row in rows:
        status_counter[str(row.status or "NULL")] += 1
        protocol_counter[str(row.protocol or "NULL")] += 1

    return {
        "count": len(rows),
        "status": dict(status_counter),
        "protocol": dict(protocol_counter),
    }


def _base_connectivity_query(session):
    return (
        session.query(Config)
        .filter(Config.protocol.in_(SUPPORTED_CONNECTIVITY_PROTOCOLS))
        .filter(
            or_(
                Config.status.is_(None),
                not_(Config.status.in_(CONNECTIVITY_EXCLUDED_STATUSES)),
            )
        )
    )


def _normalize_operator(operator):
    value = str(operator or "").strip().lower()

    if not value:
        raise ValueError("operator is required")

    return value


def _base_raw_to_reserve_query(session, operator):
    """
    Base query for the independent Raw -> Reserve cycle.

    Configs currently owned by Reserve, Hot, or Quarantine for the
    selected operator are excluded. This prevents the Raw worker from
    changing their connectivity status to testing.
    """
    operator = _normalize_operator(operator)

    owned_pool_ids = (
        select(ConfigPool.config_id)
        .where(
            ConfigPool.operator == operator,
            ConfigPool.pool_status.in_(
                RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES
            ),
        )
    )

    return (
        _base_connectivity_query(session)
        .filter(not_(Config.id.in_(owned_pool_ids)))
    )


def _pick_rows(query, order_by, limit, already_ids):
    if limit <= 0:
        return []

    if already_ids:
        query = query.filter(
            not_(Config.id.in_(list(already_ids)))
        )

    return (
        query
        .order_by(*order_by)
        .limit(int(limit))
        .all()
    )


def _allocate_smart_limits(
    limit,
    newest_ratio,
    alive_ratio,
    retry_ratio,
):
    """
    Allocate the requested limit using all three ratios.

    Ratios are normalized when their sum is not exactly 1.0. Integer
    rounding leftovers are distributed by largest fractional remainder,
    so the final allocation always equals limit.
    """
    limit = max(int(limit), 0)

    ratios = {
        "newest": float(newest_ratio),
        "alive": float(alive_ratio),
        "retry": float(retry_ratio),
    }

    if any(value < 0 for value in ratios.values()):
        raise ValueError("smart batch ratios must be >= 0")

    ratio_total = sum(ratios.values())

    if ratio_total <= 0:
        raise ValueError(
            "at least one smart batch ratio must be greater than zero"
        )

    normalized = {
        name: value / ratio_total
        for name, value in ratios.items()
    }

    exact = {
        name: limit * value
        for name, value in normalized.items()
    }

    allocated = {
        name: int(value)
        for name, value in exact.items()
    }

    remaining = limit - sum(allocated.values())

    remainder_order = sorted(
        exact,
        key=lambda name: exact[name] - allocated[name],
        reverse=True,
    )

    for name in remainder_order[:remaining]:
        allocated[name] += 1

    return {
        "newest_limit": allocated["newest"],
        "alive_limit": allocated["alive"],
        "retry_limit": allocated["retry"],
        "normalized_ratios": normalized,
    }


def build_smart_connectivity_plan(
    limit=3500,
    newest_ratio=0.60,
    alive_ratio=0.25,
    retry_ratio=0.15,
):
    """
    Preview-only smart connectivity selector.

    This function does not modify the database.
    It only selects candidate rows and returns a plan.
    """

    session = SessionLocal()

    try:
        total_configs = session.query(Config).count()

        allocation = _allocate_smart_limits(
            limit=limit,
            newest_ratio=newest_ratio,
            alive_ratio=alive_ratio,
            retry_ratio=retry_ratio,
        )

        newest_limit = allocation["newest_limit"]
        alive_limit = allocation["alive_limit"]
        retry_limit = allocation["retry_limit"]

        selected_ids = set()
        buckets = {}

        newest_query = (
            _base_connectivity_query(session)
            .filter(
                or_(
                    Config.status.is_(None),
                    Config.status.in_(["new", "not_tested"]),
                )
            )
        )

        newest_rows = _pick_rows(
            newest_query,
            [Config.id.desc()],
            newest_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in newest_rows)
        buckets["newest_untested"] = newest_rows

        alive_query = (
            _base_connectivity_query(session)
            .filter(Config.status == "alive")
        )

        alive_rows = _pick_rows(
            alive_query,
            [
                Config.last_checked.is_(None).desc(),
                Config.last_checked.asc(),
                Config.id.desc(),
            ],
            alive_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in alive_rows)
        buckets["stale_alive_recheck"] = alive_rows

        retry_query = (
            _base_connectivity_query(session)
            .filter(
                Config.status.in_(
                    [
                        "dead",
                        "error",
                        "rejected",
                        "timeout",
                        "failed",
                    ]
                )
            )
        )

        retry_rows = _pick_rows(
            retry_query,
            [
                Config.last_checked.is_(None).desc(),
                Config.last_checked.asc(),
                Config.id.desc(),
            ],
            retry_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in retry_rows)
        buckets["old_failed_retry"] = retry_rows

        remaining = limit - len(selected_ids)

        fill_query = _base_connectivity_query(session)

        fill_rows = _pick_rows(
            fill_query,
            [Config.id.desc()],
            remaining,
            selected_ids,
        )

        selected_ids.update(row.id for row in fill_rows)
        buckets["newest_fill"] = fill_rows

        selected_rows = []
        for rows in buckets.values():
            selected_rows.extend(rows)

        selected_ids_ordered = [row.id for row in selected_rows]

        return {
            "created_at": datetime.now().isoformat(),
            "requested_limit": limit,
            "selected_total": len(selected_rows),
            "selected_min_id": min(selected_ids_ordered) if selected_ids_ordered else None,
            "selected_max_id": max(selected_ids_ordered) if selected_ids_ordered else None,
            "bucket_summary": {
                name: _summarize_rows(rows)
                for name, rows in buckets.items()
            },
            "overall_summary": _summarize_rows(selected_rows),
            "selected_ids": selected_ids_ordered,
            "selected_rows_preview": {
                "first_30": [_row_to_dict(row) for row in selected_rows[:30]],
                "last_30": [_row_to_dict(row) for row in selected_rows[-30:]],
            },
        }

    finally:
        session.close()


def build_smart_raw_to_reserve_plan(
    operator,
    limit=3500,
    newest_ratio=0.60,
    alive_ratio=0.25,
    retry_ratio=0.15,
):
    """
    Preview-only Smart selector for the independent Raw -> Reserve cycle.

    Eligible buckets:
        1. Newest untested configs.
        2. Stale alive configs not owned by a Pool.
        3. Old failed configs not owned by a Pool.

    Excluded:
        - status testing / archived
        - current Reserve rows
        - current Hot rows
        - current Quarantined rows

    This function does not modify the database.
    """
    operator = _normalize_operator(operator)
    limit = max(int(limit), 0)

    allocation = _allocate_smart_limits(
        limit=limit,
        newest_ratio=newest_ratio,
        alive_ratio=alive_ratio,
        retry_ratio=retry_ratio,
    )

    newest_limit = allocation["newest_limit"]
    alive_limit = allocation["alive_limit"]
    retry_limit = allocation["retry_limit"]

    session = SessionLocal()

    try:
        selected_ids = set()
        buckets = {}

        newest_query = (
            _base_raw_to_reserve_query(session, operator)
            .filter(
                or_(
                    Config.status.is_(None),
                    Config.status.in_(["new", "not_tested"]),
                )
            )
        )

        newest_rows = _pick_rows(
            newest_query,
            [Config.id.desc()],
            newest_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in newest_rows)
        buckets["newest_untested"] = newest_rows

        alive_query = (
            _base_raw_to_reserve_query(session, operator)
            .filter(Config.status == "alive")
        )

        alive_rows = _pick_rows(
            alive_query,
            [
                Config.last_checked.is_(None).desc(),
                Config.last_checked.asc(),
                Config.id.desc(),
            ],
            alive_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in alive_rows)
        buckets["stale_alive_recheck"] = alive_rows

        retry_query = (
            _base_raw_to_reserve_query(session, operator)
            .filter(
                Config.status.in_(
                    [
                        "dead",
                        "error",
                        "rejected",
                        "timeout",
                        "failed",
                    ]
                )
            )
        )

        retry_rows = _pick_rows(
            retry_query,
            [
                Config.last_checked.is_(None).desc(),
                Config.last_checked.asc(),
                Config.id.desc(),
            ],
            retry_limit,
            selected_ids,
        )

        selected_ids.update(row.id for row in retry_rows)
        buckets["old_failed_retry"] = retry_rows

        # Fill shortages without violating Pool ownership.
        remaining = limit - len(selected_ids)

        fill_rows = _pick_rows(
            _base_raw_to_reserve_query(session, operator),
            [
                Config.last_checked.is_(None).desc(),
                Config.last_checked.asc(),
                Config.id.desc(),
            ],
            remaining,
            selected_ids,
        )

        selected_ids.update(row.id for row in fill_rows)
        buckets["eligible_fill"] = fill_rows

        selected_rows = []
        for rows in buckets.values():
            selected_rows.extend(rows)

        selected_ids_ordered = [row.id for row in selected_rows]

        return {
            "created_at": datetime.now().isoformat(),
            "mode": "smart_raw_to_reserve",
            "operator": operator,
            "requested_limit": limit,
            "requested_ratios": {
                "newest": float(newest_ratio),
                "alive": float(alive_ratio),
                "retry": float(retry_ratio),
            },
            "normalized_ratios": allocation["normalized_ratios"],
            "bucket_limits": {
                "newest_untested": newest_limit,
                "stale_alive_recheck": alive_limit,
                "old_failed_retry": retry_limit,
            },
            "excluded_pool_statuses": list(
                RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES
            ),
            "selected_total": len(selected_rows),
            "selected_min_id": min(selected_ids_ordered) if selected_ids_ordered else None,
            "selected_max_id": max(selected_ids_ordered) if selected_ids_ordered else None,
            "bucket_summary": {
                name: _summarize_rows(rows)
                for name, rows in buckets.items()
            },
            "overall_summary": _summarize_rows(selected_rows),
            "selected_ids": selected_ids_ordered,
            "selected_rows_preview": {
                "first_30": [_row_to_dict(row) for row in selected_rows[:30]],
                "last_30": [_row_to_dict(row) for row in selected_rows[-30:]],
            },
        }

    finally:
        session.close()


def claim_config_ids_for_connectivity(config_ids):
    """
    Mark selected configs as testing.

    This is used by smartcycle after a preview plan is accepted.
    It will not claim archived/testing rows.
    """

    ids = [int(x) for x in config_ids or []]

    if not ids:
        return []

    session = SessionLocal()

    try:
        rows = (
            _base_connectivity_query(session)
            .filter(Config.id.in_(ids))
            .all()
        )

        now = datetime.utcnow()

        for row in rows:
            row.status = "testing"
            row.last_checked = now

        session.commit()

        claimed = {row.id for row in rows}

        # Preserve the input order.
        return [
            config_id
            for config_id in ids
            if config_id in claimed
        ]

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def claim_config_ids_for_raw_to_reserve(config_ids, operator):
    """
    Atomically claim selected IDs for the independent Raw -> Reserve cycle.

    Every ID is revalidated at claim time:
        - supported protocol
        - not testing / archived
        - not owned by Reserve / Hot / Quarantine for operator

    Input order is preserved.
    """
    operator = _normalize_operator(operator)

    ids = []
    seen = set()

    for value in (config_ids or []):
        config_id = int(value)

        if config_id in seen:
            continue

        seen.add(config_id)
        ids.append(config_id)

    if not ids:
        return []

    session = SessionLocal()

    try:
        claimed_ids = []
        now = datetime.utcnow()

        owned_pool_exists = (
            session.query(ConfigPool.id)
            .filter(
                ConfigPool.config_id == Config.id,
                ConfigPool.operator == operator,
                ConfigPool.pool_status.in_(
                    RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES
                ),
            )
            .exists()
        )

        for config_id in ids:
            updated = (
                session.query(Config)
                .filter(
                    Config.id == config_id,
                    Config.protocol.in_(SUPPORTED_CONNECTIVITY_PROTOCOLS),
                    or_(
                        Config.status.is_(None),
                        not_(
                            Config.status.in_(
                                CONNECTIVITY_EXCLUDED_STATUSES
                            )
                        ),
                    ),
                    not_(owned_pool_exists),
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
                claimed_ids.append(config_id)

        session.commit()
        return claimed_ids

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def release_raw_to_reserve_claim(
    config_id,
    operator,
    reason="raw_to_reserve_worker_exception",
):
    """
    Release one unfinished Raw -> Reserve claim.

    The row is reset only when all safety conditions still hold:
        - Config.status is still testing
        - protocol is supported
        - config is not owned by Reserve / Hot / Quarantine
          for the selected operator

    If Connectivity or a later stage has already changed the status,
    this function intentionally does nothing.
    """
    operator = _normalize_operator(
        operator
    )
    config_id = int(config_id)

    reason_text = str(
        reason
        or "raw_to_reserve_worker_exception"
    ).strip()

    if not reason_text:
        reason_text = (
            "raw_to_reserve_worker_exception"
        )

    session = SessionLocal()

    try:
        owned_pool_exists = (
            session.query(ConfigPool.id)
            .filter(
                ConfigPool.config_id
                == Config.id,
                ConfigPool.operator
                == operator,
                ConfigPool.pool_status.in_(
                    RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES
                ),
            )
            .exists()
        )

        updated = (
            session.query(Config)
            .filter(
                Config.id == config_id,
                Config.status == "testing",
                Config.protocol.in_(
                    SUPPORTED_CONNECTIVITY_PROTOCOLS
                ),
                not_(owned_pool_exists),
            )
            .update(
                {
                    Config.status: "not_tested",
                    Config.last_error: (
                        "claim_released:"
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


def recover_stuck_raw_to_reserve_claims(
    operator,
    stale_minutes=30,
    limit=100,
):
    """
    Recover stale Raw -> Reserve claims.

    A config is recoverable only when:
        - status is testing
        - the claim is older than stale_minutes
        - it uses a supported protocol
        - it is not owned by Reserve, Hot, or Quarantine
          for the selected operator

    Recovered rows return to not_tested so Smart selection may pick
    them again. Recent testing rows are never touched.
    """
    operator = _normalize_operator(
        operator
    )
    stale_minutes = max(
        float(stale_minutes),
        1.0,
    )
    limit = max(int(limit), 1)

    cutoff = (
        datetime.utcnow()
        - timedelta(
            minutes=stale_minutes
        )
    )

    session = SessionLocal()

    try:
        owned_pool_exists = (
            session.query(ConfigPool.id)
            .filter(
                ConfigPool.config_id
                == Config.id,
                ConfigPool.operator
                == operator,
                ConfigPool.pool_status.in_(
                    RAW_TO_RESERVE_EXCLUDED_POOL_STATUSES
                ),
            )
            .exists()
        )

        candidates = (
            session.query(
                Config.id,
                Config.last_checked,
            )
            .filter(
                Config.status == "testing",
                Config.protocol.in_(
                    SUPPORTED_CONNECTIVITY_PROTOCOLS
                ),
                or_(
                    Config.last_checked
                    .is_(None),
                    Config.last_checked
                    <= cutoff,
                ),
                not_(owned_pool_exists),
            )
            .order_by(
                Config.last_checked
                .is_(None)
                .desc(),
                Config.last_checked.asc(),
                Config.id.asc(),
            )
            .limit(limit)
            .all()
        )

        recovered_ids = []

        for candidate in candidates:
            config_id = int(
                candidate.id
            )

            updated = (
                session.query(Config)
                .filter(
                    Config.id == config_id,
                    Config.status
                    == "testing",
                    Config.protocol.in_(
                        SUPPORTED_CONNECTIVITY_PROTOCOLS
                    ),
                    or_(
                        Config.last_checked
                        == None,
                        Config.last_checked
                        <= cutoff,
                    ),
                    not_(
                        owned_pool_exists
                    ),
                )
                .update(
                    {
                        Config.status: (
                            "not_tested"
                        ),
                        Config.last_error: (
                            "recovered_stuck_"
                            "raw_to_reserve_claim"
                        ),
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                recovered_ids.append(
                    config_id
                )

        session.commit()

        return {
            "operator": operator,
            "stale_minutes": (
                stale_minutes
            ),
            "cutoff": (
                cutoff.isoformat()
            ),
            "candidate_total": len(
                candidates
            ),
            "recovered_total": len(
                recovered_ids
            ),
            "recovered_ids": (
                recovered_ids
            ),
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def get_alive_config_ids_from_ids(config_ids):
    """
    Return the subset of provided IDs that are currently alive.
    Used after connectivity tests to run quality only for the smart batch.
    """

    ids = [int(x) for x in config_ids or []]

    if not ids:
        return []

    session = SessionLocal()

    try:
        rows = (
            session.query(Config.id)
            .filter(
                Config.id.in_(ids),
                Config.status == "alive",
            )
            .order_by(
                Config.quality_score.desc(),
                Config.latency.asc(),
                Config.id.desc(),
            )
            .all()
        )

        return [row[0] for row in rows]

    finally:
        session.close()


def get_alive_pending_quality_ids(limit=100, exclude_ids=None):
    """
    Return alive configs that still need quality validation.

    This prevents the pool from drifting into:
      status='alive'
      quality_status!='qualified'
    """

    exclude_ids = {
        int(x)
        for x in (exclude_ids or [])
    }

    session = SessionLocal()

    try:
        query = (
            session.query(Config.id)
            .filter(Config.status == "alive")
            .filter(
                or_(
                    Config.quality_status == None,
                    Config.quality_status != "qualified",
                )
            )
        )

        if exclude_ids:
            query = query.filter(
                not_(Config.id.in_(list(exclude_ids)))
            )

        rows = (
            query
            .order_by(
                Config.last_quality_checked.is_(None).desc(),
                Config.last_quality_checked.asc(),
                Config.latency.asc(),
                Config.id.desc(),
            )
            .limit(int(limit))
            .all()
        )

        return [row[0] for row in rows]

    finally:
        session.close()