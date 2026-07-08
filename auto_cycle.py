import json
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"

ENABLE_FILE_LOG = os.getenv("AUTO_CYCLE_FILE_LOG", "true").lower() == "true"

AUTO_CYCLE_MODE = os.getenv("AUTO_CYCLE_MODE", "full").lower()

SMART_BATCH_LIMIT = int(os.getenv("SMART_BATCH_LIMIT", "3500"))
SMART_CONNECTIVITY_WORKERS = int(os.getenv("SMART_CONNECTIVITY_WORKERS", "5"))
SMART_QUALITY_WORKERS = int(os.getenv("SMART_QUALITY_WORKERS", "3"))

SMART_BATCH_PLAN_FILE = os.getenv("SMART_BATCH_PLAN_FILE", "smart_batch_plan.json")
SMART_BATCH_IDS_FILE = os.getenv("SMART_BATCH_IDS_FILE", "smart_batch_ids.json")
SMART_ALIVE_IDS_FILE = os.getenv("SMART_ALIVE_IDS_FILE", "smart_alive_ids.json")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_log_file():
    if not ENABLE_FILE_LOG:
        return None

    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"auto_cycle_{stamp}.log"


def write_log(log_file, text):
    if log_file is None:
        return

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(text)


def emit(log_file, text=""):
    print(text)
    write_log(log_file, text + "\n")


def run_step(title, command, log_file):
    emit(log_file, "\n" + "=" * 70)
    emit(log_file, f"{now_text()} | START: {title}")
    emit(log_file, "COMMAND: " + " ".join(str(x) for x in command))
    emit(log_file, "=" * 70)

    process = subprocess.Popen(
        [str(x) for x in command],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    for line in process.stdout:
        print(line, end="")
        write_log(log_file, line)

    return_code = process.wait()

    emit(log_file, "\n" + "-" * 70)
    emit(log_file, f"{now_text()} | END: {title} | EXIT CODE: {return_code}")
    emit(log_file, "-" * 70)

    if return_code != 0:
        raise RuntimeError(f"Step failed: {title} | exit code={return_code}")


def run_function_step(title, func, log_file):
    emit(log_file, "\n" + "=" * 70)
    emit(log_file, f"{now_text()} | START: {title}")
    emit(log_file, "=" * 70)

    try:
        result = func()

        emit(log_file, "\n" + "-" * 70)
        emit(log_file, f"{now_text()} | END: {title} | EXIT CODE: 0")
        emit(log_file, "-" * 70)

        return result

    except Exception as exc:
        emit(log_file, "\n" + "-" * 70)
        emit(log_file, f"{now_text()} | END: {title} | EXIT CODE: 1")
        emit(log_file, f"ERROR: {repr(exc)}")
        emit(log_file, "-" * 70)
        raise


def read_smart_batch_ids():
    path = PROJECT_ROOT / SMART_BATCH_IDS_FILE

    if not path.exists():
        raise FileNotFoundError(f"Smart batch ids file not found: {path}")

    with open(path, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    ids = payload.get("ids") or []

    if not isinstance(ids, list):
        raise RuntimeError("Invalid smart batch ids file: ids is not a list")

    ids = [int(x) for x in ids]

    print("SMART IDS LOADED:", len(ids))

    return ids


def write_json_runtime(path_text, payload):
    path = PROJECT_ROOT / path_text

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path


def run_smart_connectivity():
    from runners.connectivity_runner import run_connectivity_ids

    ids = read_smart_batch_ids()

    if not ids:
        print("NO SMART CONNECTIVITY IDS")
        return 0

    return run_connectivity_ids(
        ids,
        workers=SMART_CONNECTIVITY_WORKERS,
        title="SMART CONNECTIVITY"
    )


def run_smart_quality():
    from database.repositories.config_repository import get_alive_config_ids_from_ids
    from runners.quality_runner import run_quality_ids

    ids = read_smart_batch_ids()

    alive_ids = get_alive_config_ids_from_ids(ids)

    payload = {
        "created_at": datetime.now().isoformat(),
        "source_ids_file": SMART_BATCH_IDS_FILE,
        "source_total": len(ids),
        "alive_total": len(alive_ids),
        "ids": alive_ids,
    }

    output_path = write_json_runtime(SMART_ALIVE_IDS_FILE, payload)

    print("SMART ALIVE IDS:", len(alive_ids))
    print("SMART ALIVE IDS FILE:", output_path)

    if not alive_ids:
        print("NO ALIVE IDS FOR SMART QUALITY")
        return 0

    return run_quality_ids(
        alive_ids,
        workers=SMART_QUALITY_WORKERS,
        title="SMART QUALITY"
    )


def run_optional_audit(python, log_file):
    if os.getenv("AUTO_CYCLE_RUN_AUDIT", "false").lower() != "true":
        return

    run_step(
        "Audit scoring",
        [python, "-u", "audit_scoring.py"],
        log_file,
    )


def run_full_mode(python, log_file):
    steps = [
        (
            "Update configs",
            [python, "-u", "main.py", "update"],
        ),
        (
            "Full database test cycle",
            [python, "-u", "main.py", "fullcycle"],
        ),
        (
            "Export public configs",
            [python, "-u", "safe_export_public_configs.py"],
        ),
        (
            "Check public export",
            [python, "-u", "check_public_export.py"],
        ),
    ]

    for title, command in steps:
        run_step(title, command, log_file)

    run_optional_audit(python, log_file)


def run_smart_mode(python, log_file):
    run_step(
        "Update configs",
        [python, "-u", "main.py", "update"],
        log_file,
    )

    run_step(
        "Select and claim smart connectivity batch",
        [
            python,
            "-u",
            "smart_batch_selector.py",
            "--limit",
            str(SMART_BATCH_LIMIT),
            "--plan-output",
            SMART_BATCH_PLAN_FILE,
            "--ids-output",
            SMART_BATCH_IDS_FILE,
            "--claim",
        ],
        log_file,
    )

    run_function_step(
        "Run smart connectivity tests",
        run_smart_connectivity,
        log_file,
    )

    run_function_step(
        "Run smart quality tests",
        run_smart_quality,
        log_file,
    )

    run_step(
        "Export public configs",
        [python, "-u", "safe_export_public_configs.py"],
        log_file,
    )

    run_step(
        "Check public export",
        [python, "-u", "check_public_export.py"],
        log_file,
    )

    run_optional_audit(python, log_file)


def main():
    python = sys.executable
    log_file = make_log_file()

    emit(log_file, "=" * 70)
    emit(log_file, "AUTO CYCLE STARTED")
    emit(log_file, "TIME: " + now_text())
    emit(log_file, "PROJECT: " + str(PROJECT_ROOT))
    emit(log_file, "MODE: " + AUTO_CYCLE_MODE)
    emit(log_file, "LOG FILE: " + (str(log_file) if log_file else "DISABLED"))
    emit(log_file, "=" * 70)

    try:
        if AUTO_CYCLE_MODE == "full":
            run_full_mode(python, log_file)

        elif AUTO_CYCLE_MODE == "smart":
            run_smart_mode(python, log_file)

        else:
            raise RuntimeError(
                f"Unsupported AUTO_CYCLE_MODE={AUTO_CYCLE_MODE}. Use full or smart."
            )

        emit(log_file, "\n" + "=" * 70)
        emit(log_file, "AUTO CYCLE FINISHED SUCCESSFULLY")
        emit(log_file, "=" * 70)

    except Exception as exc:
        emit(log_file, "\nAUTO CYCLE FAILED")
        emit(log_file, "ERROR: " + repr(exc))
        emit(log_file, traceback.format_exc())
        raise SystemExit(1)


if __name__ == "__main__":
    main()