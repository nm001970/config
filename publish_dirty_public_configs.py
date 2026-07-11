import os
import subprocess
import sys
from pathlib import Path
import time 

PROJECT_ROOT = Path(__file__).resolve().parent

PUBLISH_LOCK_PATH = PROJECT_ROOT / "git_publish.lock"

PUBLISH_LOCK_STALE_SECONDS = int(
    os.getenv("GIT_PUBLISH_LOCK_STALE_SECONDS", "1800")
)

BRANCH = os.getenv("GIT_BRANCH", "main")

ALLOWED_FILES = {
    "public_configs.json",
    "public_configs_mci.json",
}

PUBLISH_ORDER = [
    "public_configs_mci.json",
    "public_configs.json",
]


def run(command, env=None, check=True):
    print()
    print("$ " + " ".join(str(x) for x in command))

    result = subprocess.run(
        [str(x) for x in command],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code {result.returncode}: {command}"
        )

    return result.returncode


def get_tracked_changes():
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=PROJECT_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )

    changes = []

    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()

        if not line:
            continue

        # Format examples:
        #  M public_configs.json
        # M  public_configs.json
        # R  old -> new
        path = line[3:].strip()

        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()

        path = path.replace("\\", "/")
        changes.append(path)

    return changes


def validate_public_file(filename):
    env = os.environ.copy()
    env["PUBLIC_CONFIGS_FILE"] = filename

    print()
    print("=" * 70)
    print(f"VALIDATE: {filename}")
    print("=" * 70)

    run(
        [sys.executable, "-u", "check_public_export.py"],
        env=env,
    )


def commit_public_file(filename):
    print()
    print("=" * 70)
    print(f"COMMIT: {filename}")
    print("=" * 70)

    run(["git", "add", filename])
    run(["git", "commit", "-m", f"Update {filename}"])

def acquire_publish_lock():
    if PUBLISH_LOCK_PATH.exists():
        lock_age = time.time() - PUBLISH_LOCK_PATH.stat().st_mtime

        if lock_age > PUBLISH_LOCK_STALE_SECONDS:
            print(
                f"Removing stale publish lock: "
                f"age={round(lock_age, 1)} seconds"
            )
            PUBLISH_LOCK_PATH.unlink()
        else:
            raise RuntimeError(
                f"Git publisher is already running: {PUBLISH_LOCK_PATH}"
            )

    try:
        file_descriptor = os.open(
            str(PUBLISH_LOCK_PATH),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )

        with os.fdopen(file_descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")
            lock_file.write(f"created_at={time.time()}\n")

    except FileExistsError:
        raise RuntimeError(
            f"Git publisher lock already exists: {PUBLISH_LOCK_PATH}"
        )


def release_publish_lock():
    try:
        PUBLISH_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def main():
    print("=" * 70)
    print("PUBLISH DIRTY PUBLIC CONFIGS")
    print("PROJECT:", PROJECT_ROOT)
    print("BRANCH:", BRANCH)
    print("=" * 70)

    changes = get_tracked_changes()

    if not changes:
        print("No tracked changes to publish.")
        return

    print()
    print("TRACKED CHANGES:")
    for item in changes:
        print(" ", item)

    unexpected = [
        item
        for item in changes
        if item not in ALLOWED_FILES
    ]

    if unexpected:
        print()
        print("UNEXPECTED TRACKED CHANGES FOUND.")
        print("Refusing to publish because non-public-config files are modified.")
        print()
        for item in unexpected:
            print(" ", item)

        raise RuntimeError("Unexpected tracked changes found")

    targets = [
        item
        for item in PUBLISH_ORDER
        if item in changes
    ]

    if not targets:
        print("No allowed public config changes to publish.")
        return

    print()
    print("TARGETS TO PUBLISH:")
    for target in targets:
        print(" ", target)

    # Validate all changed public files before committing anything.
    for target in targets:
        validate_public_file(target)

    # Commit each file separately so MCI and default history stay clean.
    for target in targets:
        commit_public_file(target)

    print()
    print("=" * 70)
    print("SYNC WITH REMOTE")
    print("=" * 70)

    run(["git", "pull", "--rebase", "origin", BRANCH])
    run(["git", "push", "origin", BRANCH])

    print()
    print("=" * 70)
    print("PUBLISH DIRTY PUBLIC CONFIGS FINISHED SUCCESSFULLY")
    print("=" * 70)


if __name__ == "__main__":
    lock_acquired = False

    try:
        acquire_publish_lock()
        lock_acquired = True

        main()

    except Exception as exc:
        print()
        print("=" * 70)
        print("PUBLISH DIRTY PUBLIC CONFIGS FAILED")
        print("ERROR:", repr(exc))
        print("=" * 70)
        raise SystemExit(1)

    finally:
        if lock_acquired:
            release_publish_lock()