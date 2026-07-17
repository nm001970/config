import os
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"
CYCLE_LOCK_PATH = RUNTIME_DIR / "export_publish_irancell.lock"

LOCK_STALE_SECONDS = int(
    os.getenv(
        "IRANCELL_EXPORT_PUBLISH_LOCK_STALE_SECONDS",
        "1800",
    )
)

COMMAND_TIMEOUT_SECONDS = int(
    os.getenv(
        "IRANCELL_EXPORT_PUBLISH_COMMAND_TIMEOUT_SECONDS",
        "1200",
    )
)


def run(command):
    command = [str(item) for item in command]

    print()
    print("$ " + " ".join(command))

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=env,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )

    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "Command timed out after "
            f"{COMMAND_TIMEOUT_SECONDS} seconds: "
            + " ".join(command)
        ) from exc

    if result.returncode != 0:
        raise RuntimeError(
            "Command failed with exit code "
            f"{result.returncode}: "
            + " ".join(command)
        )

    return result.returncode


def acquire_cycle_lock():
    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if CYCLE_LOCK_PATH.exists():
        lock_age = (
            time.time()
            - CYCLE_LOCK_PATH.stat().st_mtime
        )

        if lock_age > LOCK_STALE_SECONDS:
            print(
                "Removing stale export/publish lock: "
                f"age={round(lock_age, 1)} seconds"
            )
            CYCLE_LOCK_PATH.unlink()

        else:
            raise RuntimeError(
                "Another Irancell export/publish "
                "cycle is already running: "
                f"{CYCLE_LOCK_PATH}"
            )

    try:
        file_descriptor = os.open(
            str(CYCLE_LOCK_PATH),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as lock_file:
            lock_file.write(
                f"pid={os.getpid()}\n"
            )
            lock_file.write(
                f"created_at={time.time()}\n"
            )

    except FileExistsError as exc:
        raise RuntimeError(
            "Export/publish cycle lock "
            "already exists: "
            f"{CYCLE_LOCK_PATH}"
        ) from exc


def release_cycle_lock():
    try:
        CYCLE_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def validate_runtime_profile():
    expected = {
        "CONFIG_DATABASE_URL": "sqlite:///configs.db",
        "PUBLIC_CONFIGS_FILE": "public_configs.json",
        "SMART_BATCH_OPERATOR": "irancell",
        "PUBLIC_POOL_OPERATOR": "irancell",
        "AUTO_CYCLE_POOL_SUPERVISOR_ENABLED": "false",
        "PUBLIC_EXPORT_SCRIPT":
            "export_public_configs_pool_metrics.py",
    }

    invalid = []

    for key, expected_value in expected.items():
        actual_value = os.getenv(key)

        if key == "AUTO_CYCLE_POOL_SUPERVISOR_ENABLED":
            actual_value = (
                actual_value or ""
            ).lower()

        if actual_value != expected_value:
            invalid.append(
                f"{key}={actual_value!r}; "
                f"expected={expected_value!r}"
            )

    if invalid:
        raise RuntimeError(
            "Invalid Irancell runtime profile: "
            + "; ".join(invalid)
        )

    required_files = [
        PROJECT_ROOT
        / "safe_export_public_configs.py",
        PROJECT_ROOT
        / "publish_dirty_public_configs.py",
        PROJECT_ROOT
        / "export_public_configs_pool_metrics.py",
        PROJECT_ROOT
        / "check_public_export.py",
    ]

    missing_files = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing_files:
        raise FileNotFoundError(
            "Required files are missing: "
            + ", ".join(missing_files)
        )

    print("RUNTIME PROFILE GUARD: OK")


def main():
    print("=" * 70)
    print("IRANCELL EXPORT + PUBLISH CYCLE")
    print("PROJECT:", PROJECT_ROOT)
    print(
        "DATABASE:",
        os.getenv("CONFIG_DATABASE_URL"),
    )
    print(
        "PUBLIC FILE:",
        os.getenv("PUBLIC_CONFIGS_FILE"),
    )
    print(
        "EXPORTER:",
        os.getenv("PUBLIC_EXPORT_SCRIPT"),
    )
    print(
        "OPERATOR:",
        os.getenv("PUBLIC_POOL_OPERATOR"),
    )
    print("CYCLE LOCK:", CYCLE_LOCK_PATH)
    print("=" * 70)

    validate_runtime_profile()

    print()
    print("=" * 70)
    print("STAGE 1/2: SAFE EXPORT")
    print("=" * 70)

    run(
        [
            sys.executable,
            "-u",
            PROJECT_ROOT
            / "safe_export_public_configs.py",
        ]
    )

    print()
    print("=" * 70)
    print("STAGE 2/2: SEMANTIC PUBLISH")
    print("=" * 70)

    run(
        [
            sys.executable,
            "-u",
            PROJECT_ROOT
            / "publish_dirty_public_configs.py",
            "--only",
            "public_configs.json",
        ]
    )

    print()
    print("=" * 70)
    print("CYCLE_RESULT=success")
    print(
        "IRANCELL EXPORT + PUBLISH "
        "CYCLE FINISHED"
    )
    print("=" * 70)


if __name__ == "__main__":
    lock_acquired = False

    try:
        acquire_cycle_lock()
        lock_acquired = True
        main()

    except Exception as exc:
        print()
        print("=" * 70)
        print("CYCLE_RESULT=failed")
        print("ERROR:", repr(exc))
        print("=" * 70)
        raise SystemExit(1)

    finally:
        if lock_acquired:
            release_cycle_lock()
