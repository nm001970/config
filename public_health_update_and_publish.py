import sys
import subprocess
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_step(title, command):
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

    if result.returncode != 0:
        raise RuntimeError(f"Step failed: {title}")

    print("\n" + "-" * 70)
    print(f"{now_text()} | END: {title}")
    print("-" * 70)


def main():
    python = sys.executable

    run_step(
        "Public health cycle",
        [python, "-u", "public_health_cycle.py"],
    )

    run_step(
        "Auto publish",
        [python, "-u", "auto_publish.py"],
    )

    print("\n" + "=" * 70)
    print("PUBLIC HEALTH UPDATE AND PUBLISH FINISHED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()