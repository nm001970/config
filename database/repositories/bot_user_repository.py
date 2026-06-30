import os
import secrets
from datetime import datetime, timedelta

from database.user_db import UserSessionLocal
from database.models import BotUser


FREE_TRIAL_DAYS =int(
    os.getenv("FREE_TRIAL_DAYS", "3")
)


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
        "chat_id": getattr(user, "chat_id", None),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": getattr(user, "last_name", None),
        "language_code": getattr(user, "language_code", None),
        "subscription_token": user.subscription_token,
        "subscription_start": user.subscription_start,
        "subscription_end": user.subscription_end,
        "last_seen_at": getattr(user, "last_seen_at", None),
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


def update_user_profile(
    user,
    telegram_id,
    chat_id=None,
    username=None,
    first_name=None,
    last_name=None,
    language_code=None,
    now=None,
):
    now = now or datetime.utcnow()

    user.telegram_id = str(telegram_id)

    if hasattr(user, "chat_id"):
        user.chat_id = str(chat_id) if chat_id is not None else str(telegram_id)

    user.username = username
    user.first_name = first_name

    if hasattr(user, "last_name"):
        user.last_name = last_name

    if hasattr(user, "language_code"):
        user.language_code = language_code

    if hasattr(user, "last_seen_at"):
        user.last_seen_at = now


def get_or_create_user(
    telegram_id,
    chat_id=None,
    username=None,
    first_name=None,
    last_name=None,
    language_code=None,
):
    session = UserSessionLocal()

    try:
        telegram_id = str(telegram_id)
        chat_id = str(chat_id) if chat_id is not None else telegram_id

        user = (
            session.query(BotUser)
            .filter(BotUser.telegram_id == telegram_id)
            .first()
        )

        now = datetime.utcnow()

        if user:
            update_user_profile(
                user=user,
                telegram_id=telegram_id,
                chat_id=chat_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
                now=now,
            )

            if not user.subscription_token:
                user.subscription_token = create_unique_subscription_token(session)

            if not user.subscription_start:
                user.subscription_start = now

            if not user.subscription_end:
                user.subscription_end = now + timedelta(days=FREE_TRIAL_DAYS)

            session.commit()
            session.refresh(user)

            return user_to_dict(user)

        user = BotUser(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            subscription_token=create_unique_subscription_token(session),
            subscription_start=now,
            subscription_end=now + timedelta(days=FREE_TRIAL_DAYS),
            creat_at=now,
        )

        update_user_profile(
            user=user,
            telegram_id=telegram_id,
            chat_id=chat_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            language_code=language_code,
            now=now,
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