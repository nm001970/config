import argparse
from datetime import datetime, timedelta

from database.user_db import UserSessionLocal
from database.models import BotUser


def parse_args():
    parser = argparse.ArgumentParser(
        description="Manually extend or set a bot user's subscription."
    )

    lookup = parser.add_mutually_exclusive_group(required=True)

    lookup.add_argument(
        "--telegram-id",
        type=int,
        help="Telegram numeric user ID"
    )

    lookup.add_argument(
        "--token",
        type=str,
        help="Subscription token"
    )

    lookup.add_argument(
        "--username",
        type=str,
        help="Telegram username without @"
    )

    action = parser.add_mutually_exclusive_group(required=True)

    action.add_argument(
        "--days",
        type=int,
        help="Add N days to the active subscription, or from now if expired"
    )

    action.add_argument(
        "--set-days",
        type=int,
        help="Set subscription_end to now + N days"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the result without saving changes"
    )

    return parser.parse_args()


def find_user(session, args):
    query = session.query(BotUser)

    if args.telegram_id is not None:
        return (
            query
            .filter(BotUser.telegram_id == args.telegram_id)
            .one_or_none()
        )

    if args.token:
        return (
            query
            .filter(BotUser.subscription_token == args.token)
            .one_or_none()
        )

    if args.username:
        username = args.username.strip().lstrip("@")

        matches = (
            query
            .filter(BotUser.username == username)
            .all()
        )

        if len(matches) > 1:
            raise RuntimeError(
                f"More than one user found for username: {username}"
            )

        return matches[0] if matches else None

    return None


def format_dt(value):
    if not value:
        return "None"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def main():
    args = parse_args()

    if args.days is not None and args.days <= 0:
        raise ValueError("--days must be greater than 0")

    if args.set_days is not None and args.set_days <= 0:
        raise ValueError("--set-days must be greater than 0")

    session = UserSessionLocal()

    try:
        user = find_user(session, args)

        if not user:
            raise RuntimeError("User not found")

        now = datetime.utcnow()

        old_end = user.subscription_end
        old_start = user.subscription_start

        if not user.subscription_start:
            user.subscription_start = now

        if args.set_days is not None:
            new_end = now + timedelta(days=args.set_days)
            mode = f"SET to now + {args.set_days} days"
        else:
            base_time = user.subscription_end

            if not base_time or base_time < now:
                base_time = now

            new_end = base_time + timedelta(days=args.days)
            mode = f"EXTEND by {args.days} days"

        user.subscription_end = new_end

        print("=" * 60)
        print("MANUAL SUBSCRIPTION UPDATE")
        print("=" * 60)
        print(f"Mode:              {mode}")
        print(f"Telegram ID:       {user.telegram_id}")
        print(f"Username:          @{user.username}" if user.username else "Username:          None")
        print(f"Subscription token:{user.subscription_token}")
        print(f"Old start:         {format_dt(old_start)}")
        print(f"Old end:           {format_dt(old_end)}")
        print(f"New start:         {format_dt(user.subscription_start)}")
        print(f"New end:           {format_dt(user.subscription_end)}")
        print(f"Dry run:           {args.dry_run}")

        if args.dry_run:
            session.rollback()
            print("RESULT: DRY RUN ONLY")
        else:
            session.commit()
            print("RESULT: SAVED")

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


if __name__ == "__main__":
    main()