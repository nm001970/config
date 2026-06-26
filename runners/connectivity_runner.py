import time
from multiprocessing import Process
from worker.db_queries import claim_supported_connectivity_batch
from worker.test_worker import test_config

def run_one_work(config_id):
    test_config(config_id)

def run_connectivity_scan(workers=5, rounds=1):

    total_claimed =0

    for round_number in range(1, rounds +1):
        
        print("\n" + "="*40)
        print(f"CONNECTIVITY ROUND {round_number}")
        print("="*40)

        ids = claim_supported_connectivity_batch(workers)

        print("CLAIMED", ids)

        if not ids:
            print("NO CONNECTIVITY CANDIDATES")
            break

        total_claimed += len(ids)

        processes= []

        for config_id in ids:
            p= Process(
                target= run_one_work,
                args=(config_id,)
            )
            
            p.start()
            processes.append(p)

        for p in processes:
            p.join()

        print(f"CONNECTIVITY ROUND {round_number} DONE")

        time.sleep(1)

    print("\n" + "=" * 40)
    print(f"TOTAL CONNECTIVITY CLAIMED: {total_claimed}")
    print("CONNECTIVITY DONE")

    return total_claimed