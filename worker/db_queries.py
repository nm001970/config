from database.db import SessionLocal
from database.models import Config
from datetime import datetime, timedelta

def get_next_config():
    session= SessionLocal()
    rows= (
        session.query(Config).filter(Config.status == "new").first()
    )
    session.close()
    return rows

def update_result(config_id, status, latency, error=None, exist_ip=None):
    session = SessionLocal()

    row = (
        session.query(Config)
        .filter(Config.id == config_id)
        .first()
    )

    if row:
        row.status = status
        row.latency = latency
        row.last_error = error
        row.exit_ip = exist_ip
        row.last_checked = datetime.utcnow()

        if status == "alive":
            row.success_count += 1

        elif status in ("dead", "error"):
            row.fail_count += 1

        session.commit()

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

def reset_stuck_configs():

    session = SessionLocal()

    timeout = datetime.utcnow() - timedelta(
        minutes=2
    )

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

    q_rows = (
        session.query(Config)
        .filter(
            Config.quality_status != "not_tested"
        ).order_by(Config.latency.asc())
        .all()
    )

    for q_row in q_rows:
        q_row.quality_status = "not_tested"
        q_row.quality_score= 0
        q_row.quality_error= None

    session.commit()

    q_count = len(q_rows)

    session.close()


    return count, q_count


def claim_vless_batch(limit=None):

    session = SessionLocal()

    rows = (
        session.query(Config)
        .filter(
            Config.status == "new",
            Config.protocol == "vless"
        )
        .order_by(Config.id.desc())
        .limit(limit)
        .all()
    )
    for row in rows:

        row.status = "testing"
        row.last_checked= datetime.utcnow()

    session.commit()
    ids= [row.id for row in rows]

    session.close()

    return ids

