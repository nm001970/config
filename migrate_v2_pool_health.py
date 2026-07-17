from sqlalchemy import inspect, text

from database.db import engine


HEALTH_COLUMNS = [
    (
        "health_status",
        "VARCHAR NOT NULL DEFAULT 'unknown'",
    ),
    (
        "health_checked_at",
        "DATETIME NULL",
    ),
    (
        "health_success_streak",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "health_fail_streak",
        "INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "health_avg_download_mbps",
        "FLOAT NULL",
    ),
    (
        "health_median_download_mbps",
        "FLOAT NULL",
    ),
    (
        "health_error",
        "VARCHAR NULL",
    ),
]


def main():
    inspector = inspect(engine)

    if "config_pool" not in inspector.get_table_names():
        raise RuntimeError(
            "config_pool table does not exist"
        )

    existing_columns = {
        column["name"]
        for column in inspector.get_columns(
            "config_pool"
        )
    }

    added_columns = []
    skipped_columns = []

    with engine.begin() as connection:
        for column_name, column_definition in HEALTH_COLUMNS:
            if column_name in existing_columns:
                skipped_columns.append(column_name)
                continue

            connection.execute(
                text(
                    f"ALTER TABLE config_pool "
                    f"ADD COLUMN {column_name} "
                    f"{column_definition}"
                )
            )

            added_columns.append(column_name)

        # برای دیتابیس موجود، index=True در مدل به‌تنهایی
        # Index را ایجاد نمی‌کند؛ بنابراین اینجا می‌سازیم.
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_config_pool_health_status "
                "ON config_pool (health_status)"
            )
        )

        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_config_pool_operator_pool_health "
                "ON config_pool "
                "(operator, pool_status, health_status)"
            )
        )

        # داده‌های قبلی بدون ادعای سلامت قطعی،
        # در وضعیت unknown قرار می‌گیرند.
        connection.execute(
            text(
                """
                UPDATE config_pool
                SET health_status = 'unknown'
                WHERE health_status IS NULL
                   OR TRIM(health_status) = ''
                """
            )
        )

        connection.execute(
            text(
                """
                UPDATE config_pool
                SET health_success_streak = 0
                WHERE health_success_streak IS NULL
                """
            )
        )

        connection.execute(
            text(
                """
                UPDATE config_pool
                SET health_fail_streak = 0
                WHERE health_fail_streak IS NULL
                """
            )
        )

    print("=" * 70)
    print("V2 POOL HEALTH MIGRATION FINISHED")
    print("ADDED:", added_columns)
    print("SKIPPED:", skipped_columns)
    print("INDEXES: READY")
    print("=" * 70)


if __name__ == "__main__":
    main()