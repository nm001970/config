import argparse

from sqlalchemy import and_, or_
from database.repositories.config_repository import get_config_by_id
from quality_worker import test_quality


from database.db import SessionLocal
from database.models import Config, ConfigPool
from database.repositories.pool_repository import (
    get_reserve_count,
    mark_gate_testing,
    upsert_gate_result,
)
from delivery_validate_public_configs import (
    get_direct_ip,
    validate_config_text,
)


def load_candidates(operator, limit):
    session = SessionLocal()

    try:
        rows = (
            session.query(
                Config.id,
                Config.config_text,
                Config.protocol,
                Config.quality_status,
                Config.quality_score,
                Config.latency,
            )
            .outerjoin(
                ConfigPool,
                and_(
                    ConfigPool.config_id == Config.id,
                    ConfigPool.operator == operator,
                ),
            )
            .filter(
                Config.status == "alive",
                or_(
                    Config.quality_status.is_(None),
                    Config.quality_status == "not_tested",
                    Config.quality_status == "qualified",
                ),
                or_(
                    ConfigPool.id.is_(None),
                    ConfigPool.pool_status.in_(
                        ["none", "quarantined"]
                    ),
                ),
            )
            .order_by(
                (Config.quality_status == "qualified").desc(),
                Config.quality_score.desc(),
                Config.last_quality_checked.is_(None).desc(),
                Config.latency.asc(),
                Config.id.desc(),
            )
            .limit(int(limit))
            .all()
        )

        return [
            {
                "id": row.id,
                "config_text": row.config_text,
                "protocol": row.protocol,
                "quality_status": row.quality_status,
                "quality_score": row.quality_score,
                "latency": row.latency,
            }
            for row in rows
        ]

    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(
        description="Seed V2 reserve from existing qualified configs."
    )

    parser.add_argument(
        "--operator",
        required=True,
    )

    parser.add_argument(
        "--target",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--max-attempts",
        type=int,
        default=200,
    )

    args = parser.parse_args()

    operator = args.operator.strip().lower()
    target = max(int(args.target), 1)
    max_attempts = max(int(args.max_attempts), 1)

    reserve_count = get_reserve_count(operator)

    print("=" * 70)
    print("V2 RESERVE SEED")
    print("OPERATOR:", operator)
    print("CURRENT RESERVE:", reserve_count)
    print("TARGET:", target)
    print("MAX ATTEMPTS:", max_attempts)
    print("=" * 70)

    if reserve_count >= target:
        print("RESERVE TARGET ALREADY REACHED")
        return

    candidates = load_candidates(
        operator=operator,
        limit=max_attempts,
    )

    print("QUALIFIED CANDIDATES:", len(candidates))

    if not candidates:
        print("NO QUALIFIED CANDIDATES AVAILABLE")
        return

    direct_ip = get_direct_ip()

    print("DIRECT IP:", direct_ip or "UNKNOWN")

    attempted = 0
    accepted = 0
    rejected = 0

    for candidate in candidates:
        reserve_count = get_reserve_count(operator)

        if reserve_count >= target:
            break

        config_id = candidate["id"]
        attempted += 1

        print()
        print("-" * 70)
        print(
            f"SEED CANDIDATE ID={config_id} "
            f"PROTOCOL={candidate['protocol']} "
            f"SCORE={candidate['quality_score']} "
            f"LATENCY={candidate['latency']}"
        )

        try:
            if candidate["quality_status"] != "qualified":
                print(
                    f"[QUALITY REQUIRED] ID={config_id} "
                    f"STATUS={candidate['quality_status']}"
                )

                test_quality(config_id)

                refreshed = get_config_by_id(config_id)

                if (
                    refreshed is None
                    or refreshed.quality_status != "qualified"
                ):
                    rejected += 1

                    print(
                        f"[QUALITY REJECTED] ID={config_id} "
                        f"STATUS={getattr(refreshed, 'quality_status', None)} "
                        f"ERROR={getattr(refreshed, 'quality_error', None)}"
                    )
                    continue

                candidate["config_text"] = refreshed.config_text

                print(
                    f"[QUALITY PASSED] ID={config_id} "
                    f"SCORE={refreshed.quality_score}"
                )

            mark_gate_testing(
                config_id=config_id,
                operator=operator,
            )

            result = validate_config_text(
                candidate["config_text"],
                direct_ip=direct_ip,
            )

        except Exception as exc:
            result = {
                "ok": False,
                "error": repr(exc),
                "exit_ip": None,
                "download_mbps": None,
            }

        pool_item = upsert_gate_result(
            config_id=config_id,
            operator=operator,
            result=result,
        )

        if result.get("ok"):
            accepted += 1

            print(
                f"[RESERVE ADDED] ID={config_id} "
                f"EXIT_IP={pool_item.get('gate_exit_ip')} "
                f"DOWNLOAD={pool_item.get('gate_download_mbps')}"
            )

        else:
            rejected += 1

            print(
                f"[GATE REJECTED] ID={config_id} "
                f"ERROR={result.get('error')}"
            )

    final_reserve = get_reserve_count(operator)

    print()
    print("=" * 70)
    print("V2 RESERVE SEED FINISHED")
    print("ATTEMPTED:", attempted)
    print("ACCEPTED:", accepted)
    print("REJECTED:", rejected)
    print("RESERVE READY:", final_reserve)
    print("TARGET:", target)
    print("=" * 70)


if __name__ == "__main__":
    main()