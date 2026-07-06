import sys
from multiprocessing import freeze_support

from worker.collector import download_configs
from worker.parser import parse_configs
from worker.deduplicator import add_hashes
from worker.storage import save_configs
from worker.config_sanitizer import sanitize_configs_for_storage

from runners.connectivity_runner import run_connectivity_scan
from runners.quality_runner import run_quality_scan
from runners.public_health_runner import (
    run_hot_health_scan,
    run_reserve_health_scan,
    run_public_health_scan
)

from database.repositories.maintenance_repository import (
    # reset_stuck_connectivity,
    schedule_quality_retest,
    reset_stuck_quality,
    archive_dead_configs
)

from database.repositories.summary_repository import (
    get_total_count,
    get_status_summary,
    get_quality_summary,
    get_top_alive,
    get_top_qualified
)


def update_configs():
    print("FETCHING CONFIGS...")

    raw_text = download_configs()
    parsed_configs = parse_configs(raw_text)

    print(f"FOUND: {len(parsed_configs)}")

    sanitized_configs, skipped = sanitize_configs_for_storage(parsed_configs)
    print(f"SANITIZED: {len(sanitized_configs)}")
    print(f"SKIPPED BY SANITIZER: {skipped}")

    configs_with_hash = add_hashes(sanitized_configs)
    inserted = save_configs(configs_with_hash)

    print(f"INSERTED: {inserted}")

    return inserted


def maintenance():
    print("MAINTENANCE...")

    # stuck_connectivity = reset_stuck_connectivity()
    quality_reaet = schedule_quality_retest()
    stuck_quality = reset_stuck_quality()
    archived_dead = archive_dead_configs(days=3)

    print(f"RESET STUCK CONNECTIVITY: {quality_reaet}")
    print(f"RESET STUCK QUALITY: {stuck_quality}")
    print(f"ARCHIVED DEAD: {archived_dead}")


def summary():
    print("\n" + "=" * 40)
    print("DATABASE SUMMARY")
    print("=" * 40)

    print("TOTAL:", get_total_count())

    print("\nSTATUS SUMMARY:")
    print(get_status_summary())

    print("\nQUALITY SUMMARY:")
    print(get_quality_summary())

    print("\nTOP ALIVE:")
    for item in get_top_alive(limit=10):
        print(item)

    print("\nTOP QUALIFIED:")
    for item in get_top_qualified(limit=10):
        print(item)

def hot_health():
    tested = run_hot_health_scan()
    print(f"HOTHEALTH TESTED: {tested}")
    return tested

def reserve_health():
    tested= run_reserve_health_scan()
    print(f"RESERVE HEALTH TESTED: {tested}")
    return tested

def public_health():
    tested = run_public_health_scan()
    print(f"PUBLIC HEALTH TESTED: {tested}")
    return tested

def run_all():
    maintenance()

    update_configs()

    run_connectivity_scan(
        workers=5,
        rounds=10
    )

    run_quality_scan(
        workers=3,
        rounds=5
    )

    summary()

def run_connectivity_until_done(
    workers=5,
    rounds_per_pass=20,
    max_passes=500
):
    print("\n" + "=" * 40)
    print("FULL CONNECTIVITY CYCLE")
    print("=" * 40)

    total_claimed = 0

    for pass_number in range(1, max_passes + 1):
        print("\n" + "-" * 40)
        print(f"CONNECTIVITY PASS {pass_number}")
        print("-" * 40)

        claimed = run_connectivity_scan(
            workers=workers,
            rounds=rounds_per_pass
        )

        total_claimed += claimed

        print(f"CONNECTIVITY PASS {pass_number} CLAIMED: {claimed}")
        print(f"CONNECTIVITY TOTAL CLAIMED: {total_claimed}")

        if claimed == 0:
            print("NO MORE CONNECTIVITY CANDIDATES")
            break

    print("\n" + "=" * 40)
    print(f"FULL CONNECTIVITY DONE: {total_claimed}")
    print("=" * 40)

    return total_claimed


def run_quality_until_done(
    workers=3,
    rounds_per_pass=20,
    max_passes=500
):
    print("\n" + "=" * 40)
    print("FULL QUALITY CYCLE")
    print("=" * 40)

    total_claimed = 0

    for pass_number in range(1, max_passes + 1):
        print("\n" + "-" * 40)
        print(f"QUALITY PASS {pass_number}")
        print("-" * 40)

        claimed = run_quality_scan(
            workers=workers,
            rounds=rounds_per_pass
        )

        total_claimed += claimed

        print(f"QUALITY PASS {pass_number} CLAIMED: {claimed}")
        print(f"QUALITY TOTAL CLAIMED: {total_claimed}")

        if claimed == 0:
            print("NO MORE QUALITY CANDIDATES")
            break

    print("\n" + "=" * 40)
    print(f"FULL QUALITY DONE: {total_claimed}")
    print("=" * 40)

    return total_claimed


def fullcycle():
    print("\n" + "=" * 40)
    print("STARTING FULL DATABASE TEST CYCLE")
    print("=" * 40)

    maintenance()

    run_connectivity_until_done(
        workers=5,
        rounds_per_pass=20,
        max_passes=500
    )

    run_quality_until_done(
        workers=3,
        rounds_per_pass=20,
        max_passes=500
    )

    summary()

    print("\n" + "=" * 40)
    print("FULL DATABASE TEST CYCLE FINISHED")
    print("=" * 40)


def print_usage():
    print("Usage:")
    print("python main.py update")
    print("python main.py connectivity")
    print("python main.py quality")
    print("python main.py maintenance")
    print("python main.py summary")
    print("python main.py all")
    print("python main.py fullcycle")
    print("python main.py hot-health")
    print("python main.py reserve-health")
    print("python main.py public-health")


if __name__ == "__main__":
    freeze_support()

    if len(sys.argv) < 2:
        print_usage()
        sys.exit(1)

    command = sys.argv[1].strip().lower()

    if command == "update":
        update_configs()

    elif command == "connectivity":
        run_connectivity_scan(
            workers=5,
            rounds=10
        )

    elif command == "quality":
        run_quality_scan(
            workers=5,
            rounds=5
        )

    elif command == "maintenance":
        maintenance()

    elif command == "summary":
        summary()

    elif command == "all":
        run_all()

    elif command == "fullcycle":
        fullcycle()

    elif command == "hot-health":
        hot_health()

    elif command == "reserve-health":
        reserve_health()

    elif command == "public-health":
        public_health()

    else:
        print(f"Unknown command: {command}")
        print_usage()
        sys.exit(1)