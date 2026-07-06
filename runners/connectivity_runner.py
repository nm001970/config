import time
from multiprocessing import Process

from worker.db_queries import claim_supported_connectivity_batch
from worker.test_worker import test_config


def run_one_work(config_id):
    test_config(config_id)


def chunk_ids(ids, size):
    for index in range(0, len(ids), size):
        yield ids[index:index + size]


def run_connectivity_ids(ids, workers=5, title="CONNECTIVITY IDS"):
    """
    Run connectivity tests for explicit config IDs.

    This mirrors runners.quality_runner.run_quality_ids
    and is used by smart batch testing.
    """

    ids = list(ids or [])

    if not ids:
        print(f"{title}: NO IDS")
        return 0

    total_tested = 0

    print("\n" + "=" * 40)
    print(title)
    print("=" * 40)
    print("TOTAL IDS:", len(ids))
    print("WORKERS:", workers)

    for batch_number, batch_ids in enumerate(chunk_ids(ids, workers), start=1):
        print("\n" + "-" * 40)
        print(f"{title} BATCH {batch_number}")
        print("BATCH IDS:", batch_ids)
        print("-" * 40)

        processes = []

        for config_id in batch_ids:
            p = Process(
                target=run_one_work,
                args=(config_id,)
            )

            p.start()
            processes.append(p)

        for p in processes:
            p.join()

        total_tested += len(batch_ids)

        print(f"{title} BATCH {batch_number} DONE")

        time.sleep(1)

    print("\n" + "=" * 40)
    print(f"{title} DONE: {total_tested}")
    print("=" * 40)

    return total_tested


def run_connectivity_scan(workers=5, rounds=1):
    total_claimed = 0

    for round_number in range(1, rounds + 1):

        print("\n" + "=" * 40)
        print(f"CONNECTIVITY ROUND {round_number}")
        print("=" * 40)

        ids = claim_supported_connectivity_batch(workers)

        print("CLAIMED", ids)

        if not ids:
            print("NO CONNECTIVITY CANDIDATES")
            break

        total_claimed += len(ids)

        run_connectivity_ids(
            ids,
            workers=workers,
            title=f"CONNECTIVITY ROUND {round_number}"
        )

        print(f"CONNECTIVITY ROUND {round_number} DONE")

        time.sleep(1)

    print("\n" + "=" * 40)
    print(f"TOTAL CONNECTIVITY CLAIMED: {total_claimed}")
    print("CONNECTIVITY DONE")

    return total_claimed