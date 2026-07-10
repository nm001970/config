import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

PUBLIC_CONFIGS_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
GIT_BRANCH = os.getenv("GIT_BRANCH", "main")
DRY_RUN = os.getenv("AUTO_PUBLISH_DRY_RUN", "false").lower() == "true"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run(command, check=True, capture=False, env=None):
    print()
    print("$", " ".join(command))

    if capture:
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

        output = result.stdout or ""
        print(output, end="")

    else:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )

        output = ""

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {' '.join(command)}"
        )

    return result.returncode, output


def normalize_path(path_text):
    return path_text.replace("\\", "/")


def get_git_status_tracked():
    _, output = run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        capture=True,
    )

    return [line for line in output.splitlines() if line.strip()]


def ensure_only_target_changed(target_file):
    target_norm = normalize_path(target_file)
    lines = get_git_status_tracked()

    if not lines:
        print("NO GIT CHANGES FOUND")
        return False

    unexpected = []

    for line in lines:
        path = line[3:].strip()
        path_norm = normalize_path(path)

        if path_norm != target_norm:
            unexpected.append(line)

    if unexpected:
        print()
        print("UNEXPECTED GIT CHANGES FOUND.")
        print("Auto publish refuses to continue because unrelated files are modified.")
        print()
        for item in unexpected:
            print(item)

        raise RuntimeError("Unrelated git changes found")

    return True


def validate_public_export(target_file):
    env = os.environ.copy()
    env["PUBLIC_CONFIGS_FILE"] = target_file

    print()
    print("Running public export check...")

    run(
        [sys.executable, "check_public_export.py"],
        env=env,
    )


def commit_and_push(target_file):
    message = f"Update {target_file}"

    run(["git", "add", target_file])

    run(["git", "commit", "-m", message])

    run(["git", "pull", "--rebase", "origin", GIT_BRANCH])

    run(["git", "push", "origin", GIT_BRANCH])


def main():
    print("=" * 70)
    print("AUTO PUBLISH STARTED")
    print(f"TIME: {now_text()}")
    print(f"FILE: {PUBLIC_CONFIGS_FILE}")
    print(f"BRANCH: {GIT_BRANCH}")
    print(f"DRY RUN: {DRY_RUN}")
    print("=" * 70)

    target_path = PROJECT_ROOT / PUBLIC_CONFIGS_FILE

    if not target_path.exists():
        raise FileNotFoundError(f"Public configs file not found: {target_path}")

    validate_public_export(PUBLIC_CONFIGS_FILE)

    has_target_change = ensure_only_target_changed(PUBLIC_CONFIGS_FILE)

    if not has_target_change:
        print("No changes to publish.")
        return

    if DRY_RUN:
        print()
        print("DRY RUN ENABLED. No commit or push performed.")
        return

    commit_and_push(PUBLIC_CONFIGS_FILE)

    print()
    print("=" * 70)
    print("AUTO PUBLISH FINISHED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    main()