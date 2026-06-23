import time
from multiprocessing import Process

from database.repositories.quality_repository import claim_quality_batch
from quality_worker import test_quality

def run_one_worker(config_id):
    test_quality(config_id)


def run_quality_scan(workers=3, rounds=1):

    total_claimed = 0

    for round_number in range(1, rounds +1):

        print("\n" + "="*40)
        print(f"QUALITY ROUND {round_number}")
        print("="*40)

        ids = claim_quality_batch(workers)

        print("CLAIMED", ids)

        if not ids :
            print("NO QUALITY CANDIDATES")
            break

        total_claimed += len(ids)

        processes = []

        for config_id in ids:
            p= Process(
                target=run_one_worker,
                args=(config_id,)
            )

            p.start()
            processes.append(p)

        for p in processes:
            p.join()

        print(f"QUALITY ROUND {round_number} DONE")

        time.sleep(1)

    print("\n" + "=" *40)
    print(f"TOTAL QUALITY CLAIMED: {total_claimed}")
    print("QUALITY DONE")

    return total_claimed

        