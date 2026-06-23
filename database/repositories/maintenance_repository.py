from datetime import datetime, timedelta

from database.db import SessionLocal
from database.models import Config


def reset_stuck_connectivity(minutes=2):
    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(minutes=minutes)

    rows = (
        session.query(Config)
        .filter(
            Config.status == "testing",
            Config.last_checked < timeout
        )
        .all()
    )

    for row in rows:
        row.status = "new"

    session.commit()

    count = len(rows)

    session.close()

    return count


def schedule_connectivity_retest(hours=6):
    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        session.query(Config)
        .filter(
            Config.status.in_(["alive", "dead", "error"]),
            Config.last_checked < timeout
        )
        .all()
    )

    for row in rows:
        row.status = "new"

    session.commit()

    count = len(rows)

    session.close()

    return count


def reset_stuck_quality(minutes=5):
    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(minutes=minutes)

    rows = (
        session.query(Config)
        .filter(
            Config.quality_status == "testing",
            Config.last_quality_checked < timeout
        )
        .all()
    )

    for row in rows:
        row.quality_status = "not_tested"

    session.commit()

    count = len(rows)

    session.close()

    return count


def schedule_quality_retest(hours=6):
    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(hours=hours)

    rows = (
        session.query(Config)
        .filter(
            Config.status == "alive",
            Config.quality_status.in_(["qualified", "rejected"]),
            Config.last_quality_checked < timeout
        )
        .all()
    )

    for row in rows:
        row.quality_status = "not_tested"
        row.quality_score = 0
        row.quality_error = None

    session.commit()

    count = len(rows)

    session.close()

    return count


def archive_dead_configs(days=3):
    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(days=days)

    rows = (
        session.query(Config)
        .filter(
            Config.status == "dead",
            Config.last_checked < timeout
        )
        .all()
    )

    for row in rows:
        row.status = "archived"

    session.commit()

    count = len(rows)

    session.close()

    return count