import secrets
from datetime import datetime, timedelta

from database.user_db import UserSessionLocal
from database.models import BotUser


FREE_DAYS = 10


def generate_token():
    return secrets.token_urlsafe(32)


def is_subscription_active(user):
    if not user:
        return False

    if not user.subscription_end:
        return False

    return user.subscription_end > datetime.utcnow()


def user_to_dict(user):
    if not user:
        return None

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "subscription_token": user.subscription_token,
        "subscription_start": user.subscription_start,
        "subscription_end": user.subscription_end,
        "creat_at": user.creat_at,
        "is_active": is_subscription_active(user),
    }


def get_user_by_telegram_id(telegram_id):
    session = UserSessionLocal()

    try:
        user = (
            session.query(BotUser)
            .filter(BotUser.telegram_id == str(telegram_id))
            .first()
        )

        return user_to_dict(user)

    finally:
        session.close()


def get_user_by_subscription_token(token):
    session = UserSessionLocal()

    try:
        user = (
            session.query(BotUser)
            .filter(BotUser.subscription_token == token)
            .first()
        )

        return user_to_dict(user)

    finally:
        session.close()


def create_unique_subscription_token(session):
    for _ in range(10):
        token = generate_token()

        exists = (
            session.query(BotUser)
            .filter(BotUser.subscription_token == token)
            .first()
        )

        if not exists:
            return token

    raise RuntimeError("Could not generate unique subscription token")


def get_or_create_user(telegram_id, username=None, first_name=None):
    session = UserSessionLocal()

    try:
        telegram_id = str(telegram_id)

        user = (
            session.query(BotUser)
            .filter(BotUser.telegram_id == telegram_id)
            .first()
        )

        now = datetime.utcnow()

        if user:
            user.username = username
            user.first_name = first_name

            if not user.subscription_token:
                user.subscription_token = create_unique_subscription_token(session)

            if not user.subscription_start:
                user.subscription_start = now

            if not user.subscription_end:
                user.subscription_end = now + timedelta(days=FREE_DAYS)

            session.commit()
            session.refresh(user)

            return user_to_dict(user)

        user = BotUser(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            subscription_token=create_unique_subscription_token(session),
            subscription_start=now,
            subscription_end=now + timedelta(days=FREE_DAYS),
            creat_at=now,
        )

        session.add(user)
        session.commit()
        session.refresh(user)

        return user_to_dict(user)

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()