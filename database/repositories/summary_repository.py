from sqlalchemy import func

from database.db import SessionLocal
from database.models import Config


def get_status_summary():
    session = SessionLocal()

    rows = (
        session.query(
            Config.status,
            func.count(Config.id)
        )
        .group_by(Config.status)
        .all()
    )

    session.close()

    return {
        status: count
        for status, count in rows
    }


def get_quality_summary():
    session = SessionLocal()

    rows = (
        session.query(
            Config.quality_status,
            func.count(Config.id)
        )
        .group_by(Config.quality_status)
        .all()
    )

    session.close()

    return {
        status: count
        for status, count in rows
    }


def get_total_count():
    session = SessionLocal()

    count = (
        session.query(Config)
        .count()
    )

    session.close()

    return count


def get_top_alive(limit=10):
    session = SessionLocal()

    rows = (
        session.query(Config)
        .filter(Config.status == "alive")
        .order_by(Config.latency.asc())
        .limit(limit)
        .all()
    )

    result = []

    for row in rows:
        result.append(
            {
                "id": row.id,
                "protocol": row.protocol,
                "latency": row.latency,
                "quality_status": row.quality_status,
                "quality_score": row.quality_score,
                "download_mbps": row.download_mbps,
                "upload_mbps": row.upload_mbps,
            }
        )

    session.close()

    return result


def get_top_qualified(limit=10):
    session = SessionLocal()

    rows = (
        session.query(Config)
        .filter(
            Config.status == "alive",
            Config.quality_status == "qualified"
        )
        .order_by(
            Config.quality_score.desc(),
            Config.latency.asc()
        )
        .limit(limit)
        .all()
    )

    result = []

    for row in rows:
        result.append(
            {
                "id": row.id,
                "protocol": row.protocol,
                "latency": row.latency,
                "quality_score": row.quality_score,
                "ping_avg_ms": row.ping_avg_ms,
                "download_mbps": row.download_mbps,
                "upload_mbps": row.upload_mbps,
            }
        )

    session.close()

    return result