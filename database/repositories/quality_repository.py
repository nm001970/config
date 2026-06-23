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

def calculate_quality_score(
        ping_avg_ms,
        download_mbps,
        upload_mbps,
        success_count=0,
        fail_count=0
):
    score = 0

    if ping_avg_ms and ping_avg_ms > 0:
        if ping_avg_ms <= 500:
            score +=40
        elif ping_avg_ms <= 1000:
            score +=30
        elif ping_avg_ms <= 2000:
            score +=20
        else:
            score += 10

    if download_mbps and download_mbps > 0:
        if download_mbps >= 2:
            score +=20
        elif download_mbps >= 0.5:
            score +=15
        else:
            score +=5
    
    if upload_mbps and upload_mbps > 0 :
        if upload_mbps >= 2:
            score +=20
        elif upload_mbps >= 0.5:
            score += 15
        else : 
            score += 5
    
    
    score += success_count *2
    score -= fail_count

    return max(round(score, 2), 0)

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
        row.fail_count
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

    if ping_avg > 0 and download > 0 and score >= 15:
        row.quality_status = "qualified"
    
    else:
        row.quality_status = "rejected"

    session.commit()
    session.close()


