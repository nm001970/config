import secrets

from sqlalchemy import text

from database.db import engine, SessionLocal
from database.models import BotUser


def column_exists(table_name, column_name):
    with engine.connect() as conn:
        rows = conn.execute(
            text(f"PRAGMA table_info({table_name})")
        ).fetchall()

    columns = [row[1] for row in rows]

    return column_name in columns


if not column_exists("bot_users", "subscription_token"):
    with engine.connect() as conn:
        conn.execute(
            text(
                "ALTER TABLE bot_users "
                "ADD COLUMN subscription_token TEXT"
            )
        )
        conn.commit()

    print("ADDED COLUMN: subscription_token")
else:
    print("COLUMN EXISTS: subscription_token")


session = SessionLocal()

users = (
    session.query(BotUser)
    .filter(
        (BotUser.subscription_token == None)
        | (BotUser.subscription_token == "")
    )
    .all()
)

for user in users:
    user.subscription_token = secrets.token_urlsafe(32)

session.commit()

print("TOKENS GENERATED:", len(users))

session.close()