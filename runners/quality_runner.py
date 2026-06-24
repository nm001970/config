import time
from multiprocessing import Process

from database.repositories.quality_repository import claim_quality_batch
from quality_worker import test_quality


def run_one_worker(config_id):
    test_quality(config_id)


def chunk_ids(ids, size):
    for index in range(0, len(ids), size):
        yield ids[index:index + size]


def run_quality_ids(ids, workers=3, title="QUALITY IDS"):
    """
    اجرای quality test برای IDهای مشخص.

    این تابع برای Hot/Reserve health استفاده خواهد شد.
    رفتار آن مستقل از claim_quality_batch است.
    """
    ids = list(ids or [])

    if not ids:
        print(f"{title}: NO IDS")
        return 0

    total_tested = 0

    print("\n" + "=" * 40)
    print(title)
    print("=" * 40)
    print("IDS:", ids)
    print("WORKERS:", workers)

    for batch_number, batch_ids in enumerate(chunk_ids(ids, workers), start=1):
        print("\n" + "-" * 40)
        print(f"{title} BATCH {batch_number}")
        print("BATCH IDS:", batch_ids)
        print("-" * 40)

        processes = []

        for config_id in batch_ids:
            p = Process(
                target=run_one_worker,
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


def run_quality_scan(workers=3, rounds=1):
    """
    اجرای quality test برای کانفیگ‌های جدید / not_tested.

    این رفتار قبلی را حفظ می‌کند:
    claim_quality_batch فقط کانفیگ‌های alive و not_tested را claim می‌کند.
    """

    total_claimed = 0

    for round_number in range(1, rounds + 1):

        print("\n" + "=" * 40)
        print(f"QUALITY ROUND {round_number}")
        print("=" * 40)

        ids = claim_quality_batch(workers)

        print("CLAIMED", ids)

        if not ids:
            print("NO QUALITY CANDIDATES")
            break

        total_claimed += len(ids)

        run_quality_ids(
            ids,
            workers=workers,
            title=f"QUALITY ROUND {round_number}"
        )

        print(f"QUALITY ROUND {round_number} DONE")

        time.sleep(1)

    print("\n" + "=" * 40)
    print(f"TOTAL QUALITY CLAIMED: {total_claimed}")
    print("QUALITY DONE")

    return total_claimed