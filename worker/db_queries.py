from database.db import SessionLocal
from database.models import Config
from datetime import datetime, timedelta

def reset_quality_fields(row, reason=None):
    row.quality_status = "not_tested"
    row.quality_score = 0
    row.ping_min_ms = None
    row.ping_avg_ms = None
    row.download_mbps = None
    row.upload_mbps = None
    row.quality_error = reason
    row.last_quality_checked = None


def reject_quality_fields(row, reason=None):
    row.quality_status = "rejected"
    row.quality_score = 0
    row.ping_min_ms = None
    row.ping_avg_ms = None
    row.download_mbps = None
    row.upload_mbps = None
    row.quality_error = reason


SUPPORTED_CONNECTIVITY_PROTOCOLS= [
    "vless",
    "vmess",
    "ss",
    "trojan",
]

def get_next_config():
    session= SessionLocal()
    rows= (
        session.query(Config).filter(Config.status == "new").first()
    )
    session.close()
    return rows

def update_result(config_id, status, latency, error=None, exist_ip=None):
    session = SessionLocal()

    try:
        row = (
            session.query(Config)
            .filter(Config.id == config_id)
            .first()
        )

        if row:
            previous_quality_status = row.quality_status

            row.status = status
            row.latency = latency
            row.last_error = error
            row.exit_ip = exist_ip
            row.last_checked = datetime.utcnow()

            if status == "alive":
                row.success_count += 1

                # A fresh connectivity pass invalidates old quality data.
                # The config must be quality-tested again in the same/new cycle.
                if previous_quality_status == "qualified":
                    reset_quality_fields(
                        row,
                        reason="quality_reset_after_connectivity_recheck"
                    )

            elif status in ("dead", "error"):
                row.fail_count += 1

                # A dead/error connectivity result must invalidate quality.
                reject_quality_fields(
                    row,
                    reason=error or f"connectivity_{status}"
                )

            session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()

def get_next_vless():

    session= SessionLocal()

    rows= (
        session.query(Config).filter(Config.status == "new",
                                     Config.protocol == "vless").order_by(Config.id.desc()).first()
    )
    session.close()
    return rows

def get_vless_batch(limit= None):
    session= SessionLocal()

    rows= (
        session.query(Config).filter(Config.status== "new",
            Config.protocol =="vless").order_by(Config.id.desc()).limit(limit).all()
    )

    session.close()
    return rows

def get_top_configs(limit= 50):
    session= SessionLocal()

    rows=(
        session.query(Config).filter(Config.status=="alive").order_by(
            Config.latency.asc()
        ).limit(limit).all()
    )
    session.close()
    return rows

def claim_next_vless():

    session = SessionLocal()

    row = (
        session.query(Config)
        .filter(
            Config.status == "new",
            Config.protocol == "vless"
        )
        .order_by(
            Config.id.desc()
        )
        .first()
    )

    if row:

        row.status = "testing"
        row.last_checked = datetime.utcnow()

        session.commit()

        session.refresh(row)

    session.close()

    return row

def reset_stuck_configs(timeout_minutes=2):
    """
    Recover connectivity rows left in status='testing' by an interrupted cycle.

    Only the connectivity state is reset. Quality fields are intentionally
    preserved until the config receives a fresh connectivity result.
    """

    session = SessionLocal()

    try:
        timeout = datetime.utcnow() - timedelta(
            minutes=timeout_minutes
        )

        rows = (
            session.query(Config)
            .filter(Config.status == "testing")
            .filter(
                (Config.last_checked.is_(None))
                | (Config.last_checked < timeout)
            )
            .all()
        )

        for row in rows:
            row.status = "new"
            row.last_checked = None
            row.last_error = "recovered_from_stuck_testing"

        count = len(rows)

        session.commit()

        return count, 0

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def claim_supported_connectivity_batch(limit=None):

    session = SessionLocal()

    rows = (
        session.query(Config)
        .filter(
            Config.status == "new",
            Config.protocol.in_(SUPPORTED_CONNECTIVITY_PROTOCOLS)
        )
        .order_by(Config.id.desc())
        .limit(limit)
        .all()
    )

    for row in rows:
        row.status = "testing"
        row.last_checked = datetime.utcnow()

    session.commit()

    ids = [row.id for row in rows]

    session.close()

    return ids


def claim_vless_batch(limit=None):
    return claim_supported_connectivity_batch(limit)
