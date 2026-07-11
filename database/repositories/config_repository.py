from collections import Counter
from datetime import datetime

from sqlalchemy import or_, not_

from database.db import SessionLocal
from database.models import Config


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


def get_config_by_id(config_id):
    session = SessionLocal()

    row = (
        session.query(Config)
        .filter(Config.id == config_id)
        .first()
    )

    session.close()
    return row


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
                Config.status == None,
                not_(Config.status.in_(CONNECTIVITY_EXCLUDED_STATUSES)),
            )
        )
    )


def _pick_rows(query, order_by, limit, already_ids):
    if limit <= 0:
        return []

    if already_ids:
        query = query.filter(not_(Config.id.in_(list(already_ids))))

    return (
        query
        .order_by(*order_by)
        .limit(int(limit))
        .all()
    )


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

        newest_limit = int(limit * newest_ratio)
        alive_limit = int(limit * alive_ratio)
        retry_limit = int(limit * retry_ratio)

        # Make sure rounding never exceeds/loses the requested total.
        retry_limit = limit - newest_limit - alive_limit

        selected_ids = set()
        buckets = {}

        newest_query = (
            _base_connectivity_query(session)
            .filter(
                or_(
                    Config.status == None,
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
            query = query.filter(not_(Config.id.in_(list(exclude_ids))))

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