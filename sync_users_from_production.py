import os
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BotUser
from database.user_db import create_user_tables


def normalize_database_url(url: str):
    if url and url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


def make_session(database_url: str):
    engine = create_engine(
        normalize_database_url(database_url),
        echo=False,
    )

    Session = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    return Session()


def get_production_url():
    url = os.getenv("PRODUCTION_USER_DATABASE_URL") or os.getenv("USER_DATABASE_URL")

    if not url:
        raise RuntimeError(
            "Production DB URL not found. Set PRODUCTION_USER_DATABASE_URL "
            "or USER_DATABASE_URL."
        )

    return url


def get_local_url():
    return os.getenv("LOCAL_USER_DATABASE_URL", "sqlite:///users.db")


def copy_field(source, target, field_name):
    if hasattr(source, field_name) and hasattr(target, field_name):
        setattr(
            target,
            field_name,
            getattr(source, field_name)
        )


def upsert_user(local_session, source_user):
    target_user = (
        local_session
        .query(BotUser)
        .filter(BotUser.telegram_id == source_user.telegram_id)
        .one_or_none()
    )

    created = False

    if not target_user:
        target_user = BotUser(
            telegram_id=source_user.telegram_id,
        )
        local_session.add(target_user)
        created = True

    fields = [
        "username",
        "first_name",
        "last_name",
        "language_code",
        "subscription_token",
        "subscription_start",
        "subscription_end",
        "creat_at",
        "created_at",
        "last_seen_at",
    ]

    for field_name in fields:
        copy_field(source_user, target_user, field_name)

    return created


def main():
    production_url = get_production_url()
    local_url = get_local_url()

    create_user_tables()

    production_session = make_session(production_url)
    local_session = make_session(local_url)

    total = 0
    created = 0
    updated = 0

    try:
        users = (
            production_session
            .query(BotUser)
            .order_by(BotUser.telegram_id.asc())
            .all()
        )

        for source_user in users:
            was_created = upsert_user(
                local_session,
                source_user,
            )

            total += 1

            if was_created:
                created += 1
            else:
                updated += 1

        local_session.commit()

        print("=" * 60)
        print("SYNC USERS FROM PRODUCTION")
        print("=" * 60)
        print(f"Time UTC:  {datetime.utcnow().isoformat()}Z")
        print(f"Total:     {total}")
        print(f"Created:   {created}")
        print(f"Updated:   {updated}")
        print("RESULT: SAVED TO LOCAL")

    except Exception:
        local_session.rollback()
        raise

    finally:
        production_session.close()
        local_session.close()


if __name__ == "__main__":
    main()