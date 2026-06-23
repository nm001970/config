import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
INTERVAL_HOURS = float(os.getenv("AUTO_LOOP_INTERVAL_HOURS", "8"))
SLEEP_SECONDS = int(INTERVAL_HOURS * 60 * 60)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_once():
    print("\n" + "=" * 70)
    print(f"{now_text()} | AUTO LOOP CYCLE START")
    print("=" * 70)

    result = subprocess.run(
        [sys.executable, "-u", "auto_update_and_publish.py"],
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.returncode == 0:
        print(f"{now_text()} | AUTO LOOP CYCLE SUCCESS")
    else:
        print(f"{now_text()} | AUTO LOOP CYCLE FAILED | code={result.returncode}")


def main():
    print("=" * 70)
    print("AUTO LOOP STARTED")
    print("INTERVAL HOURS:", INTERVAL_HOURS)
    print("=" * 70)

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"{now_text()} | AUTO LOOP ERROR: {repr(e)}")

        print(f"{now_text()} | Sleeping {INTERVAL_HOURS} hours...")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()