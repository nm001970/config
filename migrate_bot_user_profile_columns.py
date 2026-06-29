import argparse
import os

from sqlalchemy import create_engine, inspect, text


PROFILE_COLUMNS = {
    "chat_id": {
        "sqlite": "TEXT",
        "postgresql": "TEXT",
    },
    "last_name": {
        "sqlite": "TEXT",
        "postgresql": "TEXT",
    },
    "language_code": {
        "sqlite": "TEXT",
        "postgresql": "TEXT",
    },
    "last_seen_at": {
        "sqlite": "DATETIME",
        "postgresql": "TIMESTAMP",
    },
}


def normalize_database_url(url: str):
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def get_database_url(db_name: str):
    if db_name == "production":
        url = os.getenv("PRODUCTION_USER_DATABASE_URL") or os.getenv("USER_DATABASE_URL")

        if not url:
            raise RuntimeError(
                "Production DB URL not found. Set PRODUCTION_USER_DATABASE_URL "
                "or USER_DATABASE_URL."
            )

        return normalize_database_url(url)

    if db_name == "local":
        return os.getenv("LOCAL_USER_DATABASE_URL", "sqlite:///users.db")

    raise ValueError(f"Unknown DB target: {db_name}")


def mask_url(url: str):
    if not url:
        return "None"

    if "@" in url:
        return "***@" + url.rsplit("@", 1)[1]

    return url


def parse_args():
    parser = argparse.ArgumentParser(
        description="Add BotUser profile columns to local or production DB."
    )

    parser.add_argument(
        "--db",
        choices=["local", "production"],
        required=True,
        help="Target user database"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show missing columns; do not alter DB"
    )

    return parser.parse_args()


def main():
    args = parse_args()

    database_url = get_database_url(args.db)

    engine = create_engine(
        database_url,
        echo=False,
    )

    inspector = inspect(engine)

    if "bot_users" not in inspector.get_table_names():
        raise RuntimeError("Table not found: bot_users")

    existing_columns = {
        column["name"]
        for column in inspector.get_columns("bot_users")
    }

    dialect = engine.dialect.name

    print("=" * 70)
    print("MIGRATE BOT USER PROFILE COLUMNS")
    print("=" * 70)
    print(f"DB target: {args.db}")
    print(f"DB URL:    {mask_url(database_url)}")
    print(f"Dialect:   {dialect}")
    print(f"Dry run:   {args.dry_run}")
    print("-" * 70)

    missing = []

    for column_name, type_map in PROFILE_COLUMNS.items():
        if column_name in existing_columns:
            print(f"EXISTS:  {column_name}")
            continue

        sql_type = type_map.get(dialect)

        if not sql_type:
            raise RuntimeError(
                f"Unsupported DB dialect for migration: {dialect}"
            )

        missing.append((column_name, sql_type))
        print(f"MISSING: {column_name} {sql_type}")

    if not missing:
        print("RESULT: NO CHANGES NEEDED")
        return

    if args.dry_run:
        print("RESULT: DRY RUN ONLY")
        return

    with engine.begin() as connection:
        for column_name, sql_type in missing:
            sql = f"ALTER TABLE bot_users ADD COLUMN {column_name} {sql_type}"
            print(f"APPLY: {sql}")
            connection.execute(text(sql))

    print("RESULT: MIGRATION APPLIED")


if __name__ == "__main__":
    main()