import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

TARGET_FILE = os.getenv("PUBLIC_CONFIGS_FILE", "public_configs.json")
TARGET_PATH = Path(TARGET_FILE)

if not TARGET_PATH.is_absolute():
    TARGET_PATH = PROJECT_ROOT / TARGET_PATH

CANDIDATE_PATH = TARGET_PATH.with_name(
    f"{TARGET_PATH.stem}.candidate{TARGET_PATH.suffix}"
)

LAST_GOOD_PATH = TARGET_PATH.with_name(
    f"{TARGET_PATH.stem}.last_good{TARGET_PATH.suffix}"
)

STATUS_PATH = TARGET_PATH.with_name(
    f"{TARGET_PATH.stem}.safe_export_status.json"
)

ALLOW_LAST_GOOD_FALLBACK_SUCCESS = (
    os.getenv("SAFE_EXPORT_ALLOW_LAST_GOOD_SUCCESS", "false").lower() == "true"
)


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_status(status, detail="", extra=None):
    payload = {
        "status": status,
        "detail": detail,
        "target_file": str(TARGET_PATH),
        "candidate_file": str(CANDIDATE_PATH),
        "last_good_file": str(LAST_GOOD_PATH),
        "created_at": datetime.now().isoformat(),
    }

    if extra:
        payload.update(extra)

    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run_command(command, env_extra=None, check=True):
    print()
    print("$", " ".join(str(x) for x in command))

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    if env_extra:
        env.update(env_extra)

    result = subprocess.run(
        [str(x) for x in command],
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
            f"Command failed with exit code {result.returncode}: {' '.join(str(x) for x in command)}"
        )

    return result


def validate_public_export(path):
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    with open(path, "r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    hot = payload.get("hot") or []
    reserve = payload.get("reserve") or []

    if not isinstance(hot, list):
        raise RuntimeError("hot is not a list")

    if len(hot) < int(os.getenv("SUBSCRIPTION_CONFIG_LIMIT", "5")):
        raise RuntimeError(f"hot has too few items: {len(hot)}")

    if reserve is not None and not isinstance(reserve, list):
        raise RuntimeError("reserve is not a list")

    generated_at = payload.get("generated_at")

    run_command(
        [sys.executable, "check_public_export.py"],
        env_extra={"PUBLIC_CONFIGS_FILE": str(path)},
        check=True,
    )

    return {
        "generated_at": generated_at,
        "hot_count": len(hot),
        "reserve_count": len(reserve),
        "file_size": path.stat().st_size,
    }


def backup_current_target_if_valid():
    if not TARGET_PATH.exists():
        print("No existing target file found. Skipping last_good backup.")
        return

    try:
        info = validate_public_export(TARGET_PATH)
        shutil.copy2(TARGET_PATH, LAST_GOOD_PATH)
        print("Existing target is valid. Updated last_good backup.")
        print("last_good:", LAST_GOOD_PATH)
        write_status(
            "existing_target_backed_up",
            "Existing target was valid and copied to last_good.",
            info,
        )
    except Exception as exc:
        print("WARNING: Existing target is not valid.")
        print("Existing target validation error:", repr(exc))

        if LAST_GOOD_PATH.exists():
            print("Restoring target from last_good backup...")
            shutil.copy2(LAST_GOOD_PATH, TARGET_PATH)
            validate_public_export(TARGET_PATH)
            print("Target restored from last_good.")
        else:
            print("No last_good backup exists. Continuing, but export must produce a valid candidate.")


def main():
    print("=" * 70)
    print("SAFE PUBLIC EXPORT STARTED")
    print("TIME:", now_text())
    print("PROJECT:", PROJECT_ROOT)
    print("TARGET:", TARGET_PATH)
    print("CANDIDATE:", CANDIDATE_PATH)
    print("LAST_GOOD:", LAST_GOOD_PATH)
    print("=" * 70)

    write_status("started", "Safe export started.")

    backup_current_target_if_valid()

    if CANDIDATE_PATH.exists():
        CANDIDATE_PATH.unlink()

    try:
        print()
        print("[1/4] Run export_public_configs.py into candidate file...")

        run_command(
            [sys.executable, "-u", "export_public_configs.py"],
            env_extra={"PUBLIC_CONFIGS_FILE": str(CANDIDATE_PATH)},
            check=True,
        )

        print()
        print("[2/4] Validate candidate export...")

        candidate_info = validate_public_export(CANDIDATE_PATH)

        print()
        print("[3/4] Candidate is valid. Replace target atomically...")

        temp_replace = TARGET_PATH.with_name(
            f"{TARGET_PATH.stem}.replace_tmp{TARGET_PATH.suffix}"
        )

        shutil.copy2(CANDIDATE_PATH, temp_replace)
        os.replace(temp_replace, TARGET_PATH)

        print("Target replaced:", TARGET_PATH)

        print()
        print("[4/4] Validate final target and update last_good...")

        final_info = validate_public_export(TARGET_PATH)
        shutil.copy2(TARGET_PATH, LAST_GOOD_PATH)

        write_status(
            "success",
            "Safe export finished successfully.",
            {
                "candidate": candidate_info,
                "final": final_info,
            },
        )

        print()
        print("=" * 70)
        print("SAFE PUBLIC EXPORT FINISHED SUCCESSFULLY")
        print("TARGET:", TARGET_PATH)
        print("HOT:", final_info.get("hot_count"))
        print("RESERVE:", final_info.get("reserve_count"))
        print("=" * 70)

    except Exception as exc:
        print()
        print("=" * 70)
        print("SAFE PUBLIC EXPORT FAILED")
        print("ERROR:", repr(exc))
        print("=" * 70)

        write_status("failed", repr(exc))

        if CANDIDATE_PATH.exists():
            try:
                CANDIDATE_PATH.unlink()
                print("Invalid candidate removed.")
            except Exception:
                pass

        restored_from_last_good = False

        if LAST_GOOD_PATH.exists():
            try:
                print("Restoring target from last_good after failure...")
                shutil.copy2(LAST_GOOD_PATH, TARGET_PATH)
                restored_info = validate_public_export(TARGET_PATH)
                restored_from_last_good = True
                print("Target restored from last_good.")

                write_status(
                    "fallback_to_last_good",
                    repr(exc),
                    {
                        "restored_from_last_good": True,
                        "fallback_success_is_allowed": ALLOW_LAST_GOOD_FALLBACK_SUCCESS,
                        "restored": restored_info,
                    },
                )

            except Exception as restore_exc:
                print("WARNING: failed to restore last_good:", repr(restore_exc))
                write_status(
                    "failed",
                    repr(exc),
                    {
                        "restored_from_last_good": False,
                        "restore_error": repr(restore_exc),
                    },
                )

        if restored_from_last_good and ALLOW_LAST_GOOD_FALLBACK_SUCCESS:
            print()
            print("=" * 70)
            print("SAFE PUBLIC EXPORT FALLBACK SUCCESS")
            print("Candidate export failed, but valid last_good was restored.")
            print("Exiting with code 0 because SAFE_EXPORT_ALLOW_LAST_GOOD_SUCCESS=true")
            print("=" * 70)
            raise SystemExit(0)

        raise SystemExit(1)


if __name__ == "__main__":
    main()