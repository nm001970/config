from datetime import datetime, timezone

from sqlalchemy import or_

from database.db import SessionLocal
from database.models import Config


QUALITY_PENDING_STATUSES = (
    None,
    "not_tested",
)


def utcnow_naive():
    """
    Return timezone-naive UTC for the current SQLite DateTime columns.
    """
    return datetime.now(
        timezone.utc
    ).replace(
        tzinfo=None
    )


def _normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def _normalize_limit(limit):
    value = int(limit or 0)

    if value <= 0:
        return 0

    return value


def safe_float(
    value,
    default=0.0,
):
    try:
        if value is None:
            return float(default)

        return float(value)

    except (TypeError, ValueError):
        return float(default)


def optional_float(value):
    """
    Convert a numeric value to float; return None when absent/invalid.
    """
    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def claim_quality_batch(limit=5):
    """
    Atomically claim Alive configs whose Quality is still pending.

    This helper is retained for legacy/standalone Quality cycles. The
    current Raw -> Reserve pipeline calls test_quality() directly after
    Connectivity and does not depend on this batch selector.
    """
    limit = _normalize_limit(
        limit
    )

    if limit == 0:
        return []

    session = SessionLocal()

    try:
        candidate_rows = (
            session.query(Config.id)
            .filter(
                Config.status == "alive",
                or_(
                    Config.quality_status.is_(None),
                    Config.quality_status
                    == "not_tested",
                ),
            )
            .order_by(
                Config.latency.is_(None),
                Config.latency.asc(),
                Config.id.desc(),
            )
            .limit(limit)
            .all()
        )

        claimed_at = utcnow_naive()
        claimed_ids = []

        for candidate in candidate_rows:
            config_id = int(
                candidate[0]
            )

            updated = (
                session.query(Config)
                .filter(
                    Config.id == config_id,
                    Config.status == "alive",
                    or_(
                        Config.quality_status
                        .is_(None),
                        Config.quality_status
                        == "not_tested",
                    ),
                )
                .update(
                    {
                        Config.quality_status: (
                            "testing"
                        ),
                        Config.last_quality_checked: (
                            claimed_at
                        ),
                        Config.quality_error: None,
                    },
                    synchronize_session=False,
                )
            )

            if updated == 1:
                claimed_ids.append(
                    config_id
                )

        session.commit()
        return claimed_ids

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def calculate_quality_score(
    ping_avg_ms,
    download_mbps,
    upload_mbps,
    success_count=0,
    fail_count=0,
    latency_ms=None,
):
    """
    Calculate a ranking score.

    This score does not decide qualified/rejected. The decision comes
    from the normalized worker result plus current Connectivity state.
    """
    ping = safe_float(
        ping_avg_ms
    )
    download = safe_float(
        download_mbps
    )
    upload = safe_float(
        upload_mbps
    )
    latency = safe_float(
        latency_ms
    )

    try:
        success_count = int(
            success_count or 0
        )

    except (TypeError, ValueError):
        success_count = 0

    try:
        fail_count = int(
            fail_count or 0
        )

    except (TypeError, ValueError):
        fail_count = 0

    score = 0.0

    # Download: maximum 50 points.
    if download >= 8:
        score += 50

    elif download >= 5:
        score += 45

    elif download >= 3:
        score += 38

    elif download >= 2:
        score += 32

    elif download >= 1.5:
        score += 25

    elif download >= 1:
        score += 18

    elif download >= 0.5:
        score += 10

    elif download > 0:
        score += 4

    # HTTP Ping: maximum 25 points.
    if ping > 0:
        if ping <= 150:
            score += 25

        elif ping <= 450:
            score += 20

        elif ping <= 750:
            score += 14

        elif ping <= 1500:
            score += 7

        elif ping <= 2500:
            score += 3

    # Connectivity latency: maximum 10 points.
    if latency > 0:
        if latency <= 200:
            score += 10

        elif latency <= 500:
            score += 8

        elif latency <= 800:
            score += 5

        elif latency <= 1000:
            score += 2

    # Upload: maximum 10 points.
    # Missing Upload receives no points but does not cause rejection.
    if upload >= 2:
        score += 10

    elif upload >= 1:
        score += 8

    elif upload >= 0.5:
        score += 5

    elif upload >= 0.3:
        score += 3

    elif upload > 0:
        score += 1

    # Historical stability.
    score += min(
        max(success_count, 0),
        3,
    ) * 1.5

    score -= min(
        max(fail_count, 0),
        5,
    ) * 2

    return max(
        min(
            round(score, 2),
            100.0,
        ),
        0.0,
    )


def build_quality_error(
    result,
    is_qualified,
):
    """
    Build the persisted rejection reason or non-fatal warning text.

    For a qualified config, Upload endpoint errors may remain as a
    warning without changing the qualified state.
    """
    result = dict(
        result or {}
    )
    error_parts = []

    def add_error(value):
        value = str(
            value or ""
        ).strip()

        if (
            value
            and value not in error_parts
        ):
            error_parts.append(
                value
            )

    if not is_qualified:
        failed_stage = str(
            result.get(
                "failed_stage"
            )
            or ""
        ).strip()

        if failed_stage:
            add_error(
                f"stage:{failed_stage}"
            )

        add_error(
            result.get(
                "quality_reject_reason"
            )
            or result.get("error")
            or "quality_rejected"
        )

    if result.get("ping_error"):
        add_error(
            "ping: "
            f"{result['ping_error']}"
        )

    if result.get("download_error"):
        add_error(
            "download: "
            f"{result['download_error']}"
        )

    if result.get("upload_error"):
        add_error(
            "upload: "
            f"{result['upload_error']}"
        )

    return (
        " | ".join(error_parts)
        if error_parts
        else None
    )


def update_quality_result(
    config_id,
    result,
):
    """
    Persist one final Quality result.

    Qualification requirements:
        - result["alive"] is exactly True;
        - Config.status is still "alive".

    A missing Config or database failure raises, allowing the caller to
    surface the persistence error instead of silently continuing.
    """
    config_id = _normalize_config_id(
        config_id
    )
    result = dict(
        result or {}
    )

    session = SessionLocal()

    try:
        row = (
            session.query(Config)
            .filter(Config.id == config_id)
            .first()
        )

        if row is None:
            raise LookupError(
                f"Config not found: {config_id}"
            )

        ping_min = optional_float(
            result.get("ping_min_ms")
        )
        ping_avg = optional_float(
            result.get("ping_avg_ms")
        )
        download = optional_float(
            result.get("download_mbps")
        )
        upload = optional_float(
            result.get("upload_mbps")
        )

        worker_qualified = (
            result.get("alive") is True
        )
        connectivity_alive = (
            row.status == "alive"
        )

        is_qualified = (
            worker_qualified
            and connectivity_alive
        )

        if (
            worker_qualified
            and not connectivity_alive
        ):
            mismatch_error = (
                "connectivity_not_alive:"
                f"{row.status}"
            )

            result["alive"] = False
            result["failed_stage"] = (
                "connectivity_state"
            )
            result[
                "quality_reject_reason"
            ] = mismatch_error
            result["error"] = (
                mismatch_error
            )

        score = calculate_quality_score(
            ping_avg_ms=ping_avg,
            download_mbps=download,
            upload_mbps=upload,
            success_count=(
                row.success_count
            ),
            fail_count=row.fail_count,
            latency_ms=row.latency,
        )

        row.last_quality_checked = (
            utcnow_naive()
        )

        # Persist measured values even when rejected for diagnostics.
        row.ping_min_ms = ping_min
        row.ping_avg_ms = ping_avg
        row.download_mbps = download
        row.upload_mbps = upload

        # quality_score is diagnostic/ranking data.
        row.quality_score = score

        # Public score is enabled only for qualified rows.
        row.score = (
            score
            if is_qualified
            else 0.0
        )

        row.quality_status = (
            "qualified"
            if is_qualified
            else "rejected"
        )

        row.quality_error = (
            build_quality_error(
                result=result,
                is_qualified=(
                    is_qualified
                ),
            )
        )

        session.commit()

        return {
            "config_id": row.id,
            "connectivity_status": (
                row.status
            ),
            "quality_status": (
                row.quality_status
            ),
            "quality_score": (
                row.quality_score
            ),
            "public_score": row.score,
            "ping_min_ms": (
                row.ping_min_ms
            ),
            "ping_avg_ms": (
                row.ping_avg_ms
            ),
            "download_mbps": (
                row.download_mbps
            ),
            "upload_mbps": (
                row.upload_mbps
            ),
            "quality_error": (
                row.quality_error
            ),
            "updated": True,
        }

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


__all__ = [
    "utcnow_naive",
    "safe_float",
    "optional_float",
    "claim_quality_batch",
    "calculate_quality_score",
    "build_quality_error",
    "update_quality_result",
]