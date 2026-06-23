from database.db import SessionLocal
from database.models import Config

from worker.config_sanitizer import sanitize_config_for_delivery

def get_configs_for_bot(limit=5):
    session = SessionLocal()

    result = []

    qualified_rows = (
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

    for row in qualified_rows:
        result.append(row)

    remaining = limit - len(result)

    if remaining > 0:
        used_ids = [row.id for row in result]

        fallback_query = (
            session.query(Config)
            .filter(
                Config.status == "alive",
                Config.quality_status == "not_tested"
            )
        )

        if used_ids:
            fallback_query = fallback_query.filter(
                Config.id.notin_(used_ids)
            )

        fallback_rows = (
            fallback_query
            .order_by(Config.latency.asc())
            .limit(remaining)
            .all()
        )

        for row in fallback_rows:
            result.append(row)

    configs = []

    for row in result:
        configs.append(
            {
                "id": row.id,
                "protocol": row.protocol,
                "config_text": sanitize_config_for_delivery(row.config_text),
                "latency": row.latency,
                "quality_status": row.quality_status,
                "quality_score": row.quality_score,
                "download_mbps": row.download_mbps,
                "ping_avg_ms": row.ping_avg_ms
            }
        )

    session.close()

    return configs