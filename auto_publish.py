import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PUBLIC_CONFIGS_FILE = "public_configs.json"
BRANCH_NAME = os.getenv("AUTO_PUBLISH_BRANCH", "main")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_command(command, check=True):
    print(f"\n$ {' '.join(command)}")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )

    if result.stdout:
        print(result.stdout, end="")

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )

    return result


def get_git_status_lines():
    result = run_command(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        check=True,
    )

    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def extract_status_path(status_line):
    if status_line.startswith("?? "):
        return status_line[3:].strip()

    return status_line[2:].strip()


def ensure_only_public_configs_changed():
    status_lines = get_git_status_lines()

    if not status_lines:
        print("NO GIT CHANGES FOUND")
        return False

    blocked = []

    for line in status_lines:
        path = extract_status_path(line)

        if path.replace("\\", "/") != PUBLIC_CONFIGS_FILE:
            blocked.append(line)

    if blocked:
        print("\nBLOCKED: non-public config changes detected.")
        print("auto_publish.py is allowed to publish only public_configs.json.")
        print("\nUnexpected changes:")
        for item in blocked:
            print(f"  {item}")

        raise RuntimeError(
            "Working tree contains changes other than public_configs.json"
        )

    return True


def ensure_public_configs_exists():
    path = PROJECT_ROOT / PUBLIC_CONFIGS_FILE

    if not path.exists():
        raise FileNotFoundError(f"{PUBLIC_CONFIGS_FILE} not found")


def run_public_export_check():
    print("\nRunning public export check...")
    run_command([sys.executable, "check_public_export.py"], check=True)


def has_public_configs_diff():
    result = run_command(
        ["git", "diff", "--quiet", "--", PUBLIC_CONFIGS_FILE],
        check=False,
    )

    # exit code 0 یعنی تغییری نیست
    # exit code 1 یعنی تغییر وجود دارد
    if result.returncode == 0:
        return False

    if result.returncode == 1:
        return True

    raise RuntimeError("git diff failed")


def publish_public_configs(dry_run=False):
    print("=" * 70)
    print("AUTO PUBLISH STARTED")
    print("TIME:", now_text())
    print("FILE:", PUBLIC_CONFIGS_FILE)
    print("BRANCH:", BRANCH_NAME)
    print("DRY RUN:", dry_run)
    print("=" * 70)

    ensure_public_configs_exists()
    run_public_export_check()

    if not ensure_only_public_configs_changed():
        print("No changes to publish.")
        return

    if not has_public_configs_diff():
        print("No public config diff to publish.")
        return

    if dry_run:
        print("\nDRY RUN OK")
        print("Would run:")
        print(f"  git add {PUBLIC_CONFIGS_FILE}")
        print("  git commit --amend --no-edit")
        print(f"  git push --force-with-lease origin {BRANCH_NAME}")
        return

    run_command(["git", "add", PUBLIC_CONFIGS_FILE], check=True)

    run_command(
        ["git", "commit", "--amend", "--no-edit"],
        check=True,
    )

    run_command(
        ["git", "push", "--force-with-lease", "origin", BRANCH_NAME],
        check=True,
    )

    print("\n" + "="* 70)
    print("AUTO PUBLISH FINISHED SUCCESSFULLY")
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Check what would be published without changing git history.",
    )

    args = parser.parse_args()

    publish_public_configs(dry_run=args.dry_run)


if __name__ == "__main__":
    main()