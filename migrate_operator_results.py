import argparse

from sqlalchemy import inspect

from database.db import engine, Base
from database.models import ConfigOperatorResult


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create operator-specific config test result table."
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show current status without creating table"
    )

    return parser.parse_args()


def main():
    args = parse_args()

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    print("=" * 70)
    print("MIGRATE OPERATOR RESULTS")
    print("=" * 70)
    print(f"Dry run: {args.dry_run}")

    if "config_operator_results" in tables:
        print("Table exists: config_operator_results")
        print("RESULT: NO CHANGES NEEDED")
        return

    print("Missing table: config_operator_results")

    if args.dry_run:
        print("RESULT: DRY RUN ONLY")
        return

    Base.metadata.create_all(
        bind=engine,
        tables=[
            ConfigOperatorResult.__table__
        ]
    )

    print("RESULT: TABLE CREATED")


if __name__ == "__main__":
    main()