from datetime import datetime
from sqlalchemy import or_

from database.db import SessionLocal
from database.models import Config 

def claim_quality_batch(limit=5):
    session= SessionLocal()

    rows= (
        session.query(Config)
        .filter(
            Config.status == "alive",
            or_(
                Config.quality_status== None,
                Config.quality_status== "not_tested"
            )
        )
        .order_by(Config.latency.asc())
        .limit(limit)
        .all()
    )

    for row in rows:
        row.quality_status = "testing"
        row.last_quality_checked= datetime.utcnow()
    
    session.commit()
    ids= [row.id for row in rows]
    session.close()
    return ids

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default

def calculate_quality_score(
        ping_avg_ms,
        download_mbps,
        upload_mbps,
        success_count=0,
        fail_count=0,
        latency_ms= None
):
    
    ping= safe_float(ping_avg_ms)
    download= safe_float(download_mbps)
    upload= safe_float(upload_mbps)
    latency= safe_float(latency_ms)

    success_count= int(success_count or 0)
    fail_count= int(fail_count or 0)
    score = 0.0

    if download >= 8:
        score += 50
    elif download >=5:
        score += 45
    elif download >=3:
        score += 38
    elif download >=2:
        score += 32
    elif download >=1:
        score += 22
    elif download >= 0.5:
        score +=12
    elif download >0:
        score +=5

    if ping > 0:
        if ping <= 150:
            score += 25
        elif ping <= 450:
            score +=20
        elif ping <= 750:
            score += 14
        elif ping <= 900:
            score += 3

    if latency >0:
        if latency <= 200:
            score += 10
        elif latency <= 500:
            score +=8
        elif latency <=800:
            score +=5
        elif latency <= 1000:
            score += 2

    if upload >= 2:
        score += 10
    elif upload >= 1:
        score += 8
    elif upload >= 0.5:
        score += 5
    elif upload > 0:
        score += 2
    
    score += min(success_count, 3)* 1.5
    score -= min(fail_count, 5) *2

    return max(min(round(score, 2), 100), 0)

def update_quality_result(config_id, result):
    session = SessionLocal()

    row= (
        session.query(Config)
        .filter(Config.id == config_id)
        .first()
    )

    if not row:
        session.close()
        return
    
    row.last_quality_checked= datetime.utcnow()

    if not result.get("alive"):
        row.quality_status = "rejected"
        row.quality_error = result.get("error")
        row.quality_score = 0
        row.score= 0
        session.commit()
        session.close()
        return
    
    ping_avg= result.get("ping_avg_ms") or 0
    ping_min= result.get("ping_min_ms") or 0
    download= result.get("download_mbps") or 0
    upload= result.get("upload_mbps") or 0

    score = calculate_quality_score(
        ping_avg,
        download,
        upload,
        row.success_count,
        row.fail_count,
        row.latency
    )

    row.ping_min_ms = ping_min
    row.ping_avg_ms= ping_avg
    row.download_mbps = download
    row.upload_mbps = upload
    row.quality_score = score
    row.score = score

    error_parts = []

    if result.get("ping_error"):
        error_parts.append(f"ping: {result.get('ping_error')}")
    
    if result.get("download_error"):
        error_parts.append(f"download: {result.get('download_error')}")
    
    if result.get("upload_error"):
        error_parts.append(f"upload: {result.get('upload_error')}")
    
    row.quality_error= "|".join(error_parts) if error_parts else None

    if ping_avg > 0 and download > 0.5 and score >= 45:
        row.quality_status = "qualified"
    
    else:
        row.quality_status = "rejected"

    session.commit()
    session.close()


