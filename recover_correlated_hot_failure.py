from datetime import datetime, timezone

from database.db import SessionLocal
from database.models import ConfigPool


OPERATOR = "irancell"

# کانفیگ جدید سالم در Slot 1 باقی می‌ماند.
HOT_SLOTS = {
    22419: 1,
    15068: 2,
    10425: 3,
    14713: 4,
}

RESTORED_IDS = {
    15068,
    10425,
    14713,
}


def main():
    session = SessionLocal()
    now_utc_naive = datetime.now(
        timezone.utc
    ).replace(tzinfo=None)

    try:
        for config_id, hot_slot in HOT_SLOTS.items():
            row = (
                session.query(ConfigPool)
                .filter(
                    ConfigPool.config_id == config_id,
                    ConfigPool.operator == OPERATOR,
                )
                .one_or_none()
            )

            if row is None:
                raise RuntimeError(
                    f"Missing ConfigPool row: {config_id}"
                )

            row.pool_status = "hot"
            row.gate_status = "accepted"
            row.hot_slot = hot_slot

            if config_id in RESTORED_IDS:
                row.health_status = "suspect"
                row.health_success_streak = 0
                row.health_fail_streak = 1
                row.health_checked_at = now_utc_naive
                row.health_error = (
                    "restored_after_correlated_hot_failure"
                )

        session.commit()

        rows = (
            session.query(ConfigPool)
            .filter(
                ConfigPool.operator == OPERATOR,
                ConfigPool.pool_status == "hot",
            )
            .order_by(ConfigPool.hot_slot.asc())
            .all()
        )

        print("HOT RECOVERY COMPLETE")
        print(
            [
                (
                    row.hot_slot,
                    row.config_id,
                    row.health_status,
                    row.health_fail_streak,
                )
                for row in rows
            ]
        )

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    main()
