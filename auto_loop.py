import os
import sys
import time
import subprocess
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

HEAVY_INTERVAL_HOURS = float(os.getenv("AUTO_LOOP_HEAVY_INTERVAL_HOURS", "8"))
HEALTH_INTERVAL_MINUTES = float(os.getenv("AUTO_LOOP_HEALTH_INTERVAL_MINUTES", "60"))
FAILED_RETRY_MINUTES = float(os.getenv("AUTO_LOOP_FAILED_RETRY_MINUTES", "15"))

HEAVY_SLEEP_SECONDS = int(HEAVY_INTERVAL_HOURS * 60 * 60)
HEALTH_SLEEP_SECONDS = int(HEALTH_INTERVAL_MINUTES * 60)
FAILED_SLEEP_SECONDS = int(FAILED_RETRY_MINUTES * 60)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_command(title, command):
    print("\n" + "=" * 70)
    print(f"{now_text()} | START: {title}")
    print("COMMAND:", " ".join(command))
    print("=" * 70)

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode == 0:
        print(f"{now_text()} | SUCCESS: {title}")
    else:
        print(f"{now_text()} | FAILED: {title} | code={result.returncode}")

    return result.returncode


def run_pool_status():
    return run_command(
        "Public pool status",
        [sys.executable, "-u", "public_pool_status.py"],
    )


def run_public_health():
    return run_command(
        "Public health update and publish",
        [sys.executable, "-u", "public_health_update_and_publish.py"],
    )


def run_heavy_cycle():
    return run_command(
        "Heavy auto update and publish",
        [sys.executable, "-u", "auto_update_and_publish.py"],
    )


def main():
    print("=" * 70)
    print("SMART AUTO LOOP STARTED")
    print("HEAVY INTERVAL HOURS:", HEAVY_INTERVAL_HOURS)
    print("HEALTH INTERVAL MINUTES:", HEALTH_INTERVAL_MINUTES)
    print("FAILED RETRY MINUTES:", FAILED_RETRY_MINUTES)
    print("=" * 70)

    last_heavy_run = None

    while True:
        try:
            should_run_heavy = False

            if last_heavy_run is None:
                should_run_heavy = True
            else:
                next_heavy_time = last_heavy_run + timedelta(seconds=HEAVY_SLEEP_SECONDS)
                if datetime.now() >= next_heavy_time:
                    should_run_heavy = True

            if should_run_heavy:
                code = run_heavy_cycle()

                if code == 0:
                    last_heavy_run = datetime.now()
                    print(f"{now_text()} | Heavy cycle completed.")
                    print(f"{now_text()} | Sleeping {HEALTH_INTERVAL_MINUTES} minutes before health cycle...")
                    time.sleep(HEALTH_SLEEP_SECONDS)
                    continue

                print(f"{now_text()} | Heavy cycle failed. Sleeping {FAILED_RETRY_MINUTES} minutes...")
                time.sleep(FAILED_SLEEP_SECONDS)
                continue

            health_code = run_public_health()

            if health_code != 0:
                print(f"{now_text()} | Public health failed. Sleeping {FAILED_RETRY_MINUTES} minutes...")
                time.sleep(FAILED_SLEEP_SECONDS)
                continue

            status_code = run_pool_status()

            if status_code == 2:
                print(f"{now_text()} | Pool unhealthy. Running heavy cycle early...")
                heavy_code = run_heavy_cycle()

                if heavy_code == 0:
                    last_heavy_run = datetime.now()
                    print(f"{now_text()} | Early heavy cycle completed.")
                    time.sleep(HEALTH_SLEEP_SECONDS)
                    continue

                print(f"{now_text()} | Early heavy cycle failed. Sleeping {FAILED_RETRY_MINUTES} minutes...")
                time.sleep(FAILED_SLEEP_SECONDS)
                continue

            if status_code != 0:
                print(f"{now_text()} | Pool status check failed. Sleeping {FAILED_RETRY_MINUTES} minutes...")
                time.sleep(FAILED_SLEEP_SECONDS)
                continue

            print(f"{now_text()} | Pool healthy.")
            print(f"{now_text()} |Sleeping {HEALTH_INTERVAL_MINUTES} minutes...")
            time.sleep(HEALTH_SLEEP_SECONDS)

        except KeyboardInterrupt:
            print(f"{now_text()} | SMART AUTO LOOP STOPPED BY USER")
            break

        except Exception as e:
            print(f"{now_text()} | SMART AUTO LOOP ERROR: {repr(e)}")
            print(f"{now_text()} | Sleeping {FAILED_RETRY_MINUTES} minutes...")
            time.sleep(FAILED_SLEEP_SECONDS)


if __name__ == "__main__":
    main()