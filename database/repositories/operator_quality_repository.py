from datetime import datetime

from database.db import SessionLocal
from database.models import Config, ConfigOperatorResult


SUPPORTED_OPERATORS = {
    "mci",
    "irancell",
    "rightel",
    "home",
    "multi",
}


def validate_operator(operator):
    operator = (operator or "").strip().lower()

    if operator not in SUPPORTED_OPERATORS:
        raise ValueError(
            f"Unsupported operator: {operator}. "
            f"Supported: {', '.join(sorted(SUPPORTED_OPERATORS))}"
        )

    return operator


def calculate_operator_quality_score(
    latency,
    ping_avg,
    download,
    upload,
    success_count=0,
    fail_count=0,
):
    score = 0

    latency = latency or 0
    ping_avg = ping_avg or 0
    download = download or 0
    upload = upload or 0

    # Download weight: max 50
    if download >= 10:
        score += 50
    elif download >= 7:
        score += 44
    elif download >= 5:
        score += 38
    elif download >= 3:
        score += 30
    elif download >= 1:
        score += 18
    elif download >= 0.5:
        score += 10

    # Ping average weight: max 25
    if ping_avg > 0:
        if ping_avg <= 150:
            score += 25
        elif ping_avg <= 450:
            score += 20
        elif ping_avg <= 750:
            score += 14
        elif ping_avg <= 900:
            score += 6
        elif ping_avg <= 1200:
            score += 3

    # Initial connectivity latency weight: max 10
    if latency > 0:
        if latency <= 200:
            score += 10
        elif latency <= 500:
            score += 8
        elif latency <= 800:
            score += 5
        elif latency <= 1200:
            score += 2

    # Upload weight: max 10
    if upload >= 2:
        score += 10
    elif upload >= 1:
        score += 8
    elif upload >= 0.5:
        score += 5
    elif upload > 0:
        score += 2

    score += min(success_count, 3) * 1.5
    score -= min(fail_count, 5) * 2

    return max(min(round(score, 2), 100), 0)


def get_operator_candidates(operator, limit=None, retest=False):
    operator = validate_operator(operator)

    session = SessionLocal()

    try:

        query= (
            session.query(Config)
            .filter(Config.status == "alive")
        )

        if not retest:
            tested_ids= (
                session.query(ConfigOperatorResult.config_id)
                .filter(ConfigOperatorResult.operator == operator)
                .subquery()
            )

            query= query.filter(
                ~Config.id.in_(tested_ids)
            )

        rows = (
            query
            .order_by(
                Config.quality_status.desc(),
                Config.quality_score.desc(),
                Config.latency.asc(),
                Config.id.desc(),
            )
            .limit(limit)
            .all()
        )

        return [row.id for row in rows]

    finally:
        session.close()


def get_config_for_operator_test(config_id):
    session = SessionLocal()

    try:
        row = (
            session.query(Config)
            .filter(Config.id == config_id)
            .first()
        )

        if not row:
            return None

        return {
            "id": row.id,
            "protocol": row.protocol,
            "config_text": row.config_text,
            "latency": row.latency,
            "quality_score": row.quality_score,
        }

    finally:
        session.close()


def update_operator_result(
    config_id,
    operator,
    alive,
    latency=None,
    ping_min_ms=None,
    ping_avg_ms=None,
    download_mbps=None,
    upload_mbps=None,
    error=None,
    test_source="local_operator_scan",
):
    operator = validate_operator(operator)

    session = SessionLocal()

    try:
        row = (
            session.query(ConfigOperatorResult)
            .filter(
                ConfigOperatorResult.config_id == config_id,
                ConfigOperatorResult.operator == operator,
            )
            .first()
        )

        now = datetime.utcnow()

        if not row:
            row = ConfigOperatorResult(
                config_id=config_id,
                operator=operator,
                creat_at=now,
            )
            session.add(row)

        row.last_checked = now
        row.test_source = test_source

        row.latency = latency or 0
        row.ping_min_ms = ping_min_ms
        row.ping_avg_ms = ping_avg_ms
        row.download_mbps = download_mbps
        row.upload_mbps = upload_mbps

        if alive:
            row.status = "alive"
            row.success_count = (row.success_count or 0) + 1
            row.last_error = None

            score = calculate_operator_quality_score(
                latency=latency,
                ping_avg=ping_avg_ms,
                download=download_mbps,
                upload=upload_mbps,
                success_count=row.success_count or 0,
                fail_count=row.fail_count or 0,
            )

            row.quality_score = score

            if (
                ping_avg_ms
                and download_mbps
                and download_mbps >= 0.5
                and score >= 45
            ):
                row.quality_status = "qualified"
            else:
                row.quality_status = "rejected"

        else:
            row.status = "dead"
            row.fail_count = (row.fail_count or 0) + 1
            row.last_error = error
            row.quality_score = 0
            row.quality_status = "rejected"

        session.commit()

        return {
            "config_id": row.config_id,
            "operator": row.operator,
            "status": row.status,
            "quality_status": row.quality_status,
            "quality_score": row.quality_score,
            "success_count": row.success_count,
            "fail_count": row.fail_count,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def get_operator_summary():
    session = SessionLocal()

    try:
        rows = session.query(ConfigOperatorResult).all()

        summary = {}

        for row in rows:
            operator = row.operator

            if operator not in summary:
                summary[operator] = {
                    "total": 0,
                    "alive": 0,
                    "qualified": 0,
                    "rejected": 0,
                }

            summary[operator]["total"] += 1

            if row.status == "alive":
                summary[operator]["alive"] += 1

            if row.quality_status == "qualified":
                summary[operator]["qualified"] += 1

            if row.quality_status == "rejected":
                summary[operator]["rejected"] += 1

        return summary

    finally:
        session.close()