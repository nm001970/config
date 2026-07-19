import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

INCOMING_CANDIDATE = Path(
    os.getenv(
        "MCI_INCOMING_CANDIDATE",
        r"C:\MNProxyIncoming\mci\public_configs_mci.candidate.json",
    )
)

INCOMING_STATUS = Path(
    os.getenv(
        "MCI_INCOMING_STATUS",
        r"C:\MNProxyIncoming\status\worker_status_mci.json",
    )
)

TARGET_FILE = Path(
    os.getenv("MCI_TARGET_FILE", "public_configs_mci.json")
)

EXPECTED_FILE = "public_configs_mci.json"
STATUS_PATH = PROJECT_ROOT / "publisher_status_mci.json"
COMMAND_TIMEOUT_SECONDS = int(
    os.getenv("MCI_PUBLISH_COMMAND_TIMEOUT_SECONDS", "900")
)
MAX_STATUS_OUTPUT_CHARS = int(
    os.getenv("MCI_PUBLISH_STATUS_OUTPUT_CHARS", "8000")
)

LAST_COMMAND = ""
LAST_COMMAND_OUTPUT = ""
LAST_FAILED_STAGE = ""


class CommandExecutionError(RuntimeError):
    def __init__(self, message, command, return_code=None, output=""):
        super().__init__(message)
        self.command = command
        self.return_code = return_code
        self.output = output


def utc_now_text():
    return datetime.now(timezone.utc).isoformat()


def fail(message):
    raise RuntimeError(message)


def run_command(command, check=True, env_extra=None, timeout=None):
    global LAST_COMMAND, LAST_COMMAND_OUTPUT

    timeout = timeout or COMMAND_TIMEOUT_SECONDS
    command = [str(item) for item in command]
    LAST_COMMAND = " ".join(command)
    LAST_COMMAND_OUTPUT = ""

    print()
    print("$", LAST_COMMAND)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    if env_extra:
        env.update(env_extra)

    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        LAST_COMMAND_OUTPUT = output
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        raise CommandExecutionError(
            f"Command timed out after {timeout}s: {LAST_COMMAND}",
            command=command,
            output=output,
        ) from exc

    LAST_COMMAND_OUTPUT = result.stdout or ""

    if LAST_COMMAND_OUTPUT:
        print(
            LAST_COMMAND_OUTPUT,
            end="" if LAST_COMMAND_OUTPUT.endswith("\n") else "\n",
        )

    if check and result.returncode != 0:
        raise CommandExecutionError(
            f"Command failed with exit code {result.returncode}: {LAST_COMMAND}",
            command=command,
            return_code=result.returncode,
            output=LAST_COMMAND_OUTPUT,
        )

    return result


def load_json_file(path):
    if not path.exists():
        fail(f"Required file not found: {path}")

    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            return json.load(file)
    except Exception as exc:
        fail(f"Invalid JSON file: {path} | {repr(exc)}")


def validate_payload_basic(path):
    payload = load_json_file(path)

    hot = payload.get("hot") or []
    reserve = payload.get("reserve") or []

    if not isinstance(hot, list):
        fail("candidate hot is not a list")

    if not isinstance(reserve, list):
        fail("candidate reserve is not a list")

    if len(hot) < 5:
        fail(f"candidate hot has too few items: {len(hot)}")

    if len(reserve) < 1:
        fail(f"candidate reserve has too few items: {len(reserve)}")

    print("Candidate basic check OK")
    print("generated_at:", payload.get("generated_at"))
    print("strategy:", payload.get("strategy"))
    print("hot:", len(hot))
    print("reserve:", len(reserve))

    return payload


def sha256_file(path):
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_path, path)

    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def write_publisher_status(
    status,
    detail="",
    commit="",
    candidate_hash="",
    candidate_size=0,
    worker_status=None,
    failed_stage="",
    return_code=None,
):
    worker_status = worker_status or {}

    payload = {
        "publisher_id": "computer_central_publisher",
        "operator": "mci",
        "status": status,
        "detail": detail,
        "commit": commit,
        "created_at": utc_now_text(),
        "incoming_candidate": str(INCOMING_CANDIDATE),
        "incoming_status": str(INCOMING_STATUS),
        "target_file": str(TARGET_FILE),
        "candidate_sha256": candidate_hash,
        "candidate_size": candidate_size,
        "worker_id": worker_status.get("worker_id") or "",
        "worker_run_id": worker_status.get("run_id") or "",
        "worker_created_at": worker_status.get("created_at") or "",
        "failed_stage": failed_stage,
        "return_code": return_code,
        "last_command": LAST_COMMAND,
        "last_command_output": LAST_COMMAND_OUTPUT[-MAX_STATUS_OUTPUT_CHARS:],
    }

    atomic_write_json(STATUS_PATH, payload)


def run_public_export_check(path):
    return run_command(
        [sys.executable, "-u", "check_public_export.py"],
        check=True,
        env_extra={"PUBLIC_CONFIGS_FILE": str(path)},
    )


def is_target_dirty(target_path):
    result = run_command(
        ["git", "status", "--porcelain", "--", str(target_path)],
        check=True,
    )
    return bool((result.stdout or "").strip())


def publish_target():
    return run_command(
        [
            sys.executable,
            "-u",
            "publish_dirty_public_configs.py",
            "--only",
            EXPECTED_FILE,
        ],
        check=True,
    )


def create_staging_copy(source_path):
    runtime_dir = PROJECT_ROOT / ".runtime" / "mci_publish"
    runtime_dir.mkdir(parents=True, exist_ok=True)

    file_descriptor, temp_name = tempfile.mkstemp(
        prefix="public_configs_mci.",
        suffix=".staging.json",
        dir=runtime_dir,
    )
    os.close(file_descriptor)

    staging_path = Path(temp_name)
    shutil.copy2(source_path, staging_path)
    return staging_path


def replace_target_atomically(staging_path, target_path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staging_path, target_path)


def main():
    global LAST_FAILED_STAGE

    remote_publish_completed = False
    worker_status = {}
    candidate_hash = ""
    candidate_size = 0
    staging_path = None
    backup_path = None
    target_path = PROJECT_ROOT / TARGET_FILE
    target_existed = target_path.exists()

    print("=" * 70)
    print("MN Proxy - Central Publisher For MCI Artifact")
    print("TIME UTC:", utc_now_text())
    print("PROJECT:", PROJECT_ROOT)
    print("INCOMING_CANDIDATE:", INCOMING_CANDIDATE)
    print("INCOMING_STATUS:", INCOMING_STATUS)
    print("TARGET_FILE:", TARGET_FILE)
    print("COMMAND_TIMEOUT_SECONDS:", COMMAND_TIMEOUT_SECONDS)
    print("=" * 70)

    try:
        LAST_FAILED_STAGE = "validate_target_path"
        normalized_target = str(TARGET_FILE).replace("\\", "/")
        if TARGET_FILE.is_absolute() or normalized_target != EXPECTED_FILE:
            fail(f"Invalid TARGET_FILE for MCI publisher: {TARGET_FILE}")

        LAST_FAILED_STAGE = "load_worker_status"
        print("\n[1/8] Check incoming worker status...")
        worker_status = load_json_file(INCOMING_STATUS)

        print("worker_id:", worker_status.get("worker_id"))
        print("operator:", worker_status.get("operator"))
        print("status:", worker_status.get("status"))
        print("created_at:", worker_status.get("created_at"))
        print("run_id:", worker_status.get("run_id"))

        if worker_status.get("operator") != "mci":
            fail("incoming worker status operator is not mci")

        if worker_status.get("status") != "candidate_ready":
            fail(
                "incoming worker status is not candidate_ready: "
                f"{worker_status.get('status')}"
            )

        if worker_status.get("worker_id") != "laptop_mci":
            fail("incoming worker status has invalid worker_id")

        if worker_status.get("transport") != "bluetooth_pan_smb":
            fail("incoming worker status has invalid transport")

        if worker_status.get("artifact_sent") is not True:
            fail("incoming worker status does not confirm artifact_sent")

        if worker_status.get("artifact_file") != INCOMING_CANDIDATE.name:
            fail(
                "incoming worker artifact_file does not match "
                "the candidate filename"
            )

        LAST_FAILED_STAGE = "inspect_candidate"
        print("\n[2/8] Check incoming candidate...")

        if not INCOMING_CANDIDATE.exists():
            fail(f"incoming candidate not found: {INCOMING_CANDIDATE}")

        if INCOMING_CANDIDATE.name.endswith((".tmp", ".partial")):
            fail("incoming candidate still has a temporary suffix")

        candidate_size = INCOMING_CANDIDATE.stat().st_size
        candidate_hash = sha256_file(INCOMING_CANDIDATE)

        print("candidate_size_bytes:", candidate_size)
        print("candidate_sha256:", candidate_hash)

        if candidate_size < 1000:
            fail(f"incoming candidate file is too small: {candidate_size} bytes")

        declared_size = worker_status.get("candidate_size")

        if declared_size not in (None, ""):
            try:
                declared_size_value = int(declared_size)
            except (TypeError, ValueError):
                fail(
                    "incoming worker status has invalid candidate_size: "
                    f"{declared_size!r}"
                )

            if declared_size_value != candidate_size:
                fail(
                    "candidate size mismatch: "
                    f"declared={declared_size_value}, "
                    f"actual={candidate_size}"
                )

        declared_hash = str(
            worker_status.get("candidate_sha256")
            or worker_status.get("sha256")
            or ""
        ).strip().lower()

        if not declared_hash:
            fail("incoming worker status has no candidate sha256")

        if declared_hash != candidate_hash:
            fail(
                "candidate sha256 mismatch: "
                f"declared={declared_hash}, "
                f"actual={candidate_hash}"
            )

        LAST_FAILED_STAGE = "stage_candidate"
        print("\n[3/8] Copy incoming candidate to local staging...")
        staging_path = create_staging_copy(INCOMING_CANDIDATE)
        print("staging_path:", staging_path)

        if sha256_file(staging_path) != candidate_hash:
            fail("staging copy hash does not match incoming candidate")

        LAST_FAILED_STAGE = "validate_staging"
        print("\n[4/8] Validate staged candidate...")
        validate_payload_basic(staging_path)
        run_public_export_check(staging_path)

        target_hash = sha256_file(target_path) if target_path.exists() else ""
        same_content = bool(target_hash and target_hash == candidate_hash)

        print("target_exists:", target_path.exists())
        print("target_sha256:", target_hash or "NONE")
        print("same_content:", same_content)

        if same_content:
            LAST_FAILED_STAGE = "publish_existing_dirty_target"
            staging_path.unlink(missing_ok=True)
            staging_path = None

            if is_target_dirty(TARGET_FILE):
                print("\n[5/8] Target content is unchanged but Git target is dirty; publish it...")
                publish_target()
                remote_publish_completed = True
                result_status = "published"
                detail = "Existing MCI target was already identical and its pending Git change was published."
            else:
                print("\n[5/8] Candidate is already published; no change required.")
                result_status = "no_change"
                detail = "Incoming MCI candidate is identical to the current clean target."

        else:
            LAST_FAILED_STAGE = "replace_target"
            print("\n[5/8] Atomically replace MCI target...")

            if target_path.exists():
                runtime_dir = PROJECT_ROOT / ".runtime" / "mci_publish"
                runtime_dir.mkdir(parents=True, exist_ok=True)
                backup_path = runtime_dir / "public_configs_mci.previous.json"
                shutil.copy2(target_path, backup_path)

            replace_target_atomically(staging_path, target_path)
            staging_path = None

            LAST_FAILED_STAGE = "validate_replaced_target"
            print("\n[6/8] Validate replaced MCI target...")
            run_public_export_check(target_path)

            LAST_FAILED_STAGE = "publish_target"
            print("\n[7/8] Publish only the MCI public artifact...")
            publish_target()
            remote_publish_completed = True

            result_status = "published"
            detail = "New MCI artifact validated, installed, and published successfully."

        LAST_FAILED_STAGE = "read_commit"

        git_remote = os.getenv("GIT_REMOTE", "origin")
        git_branch = os.getenv("GIT_BRANCH", "main")
        remote_ref = f"{git_remote}/{git_branch}"

        run_command(
            [
                "git",
                "fetch",
                git_remote,
                git_branch,
            ],
            check=True,
        )

        log_result = run_command(
            [
                "git",
                "log",
                "--oneline",
                "--decorate",
                "-1",
                remote_ref,
                "--",
                EXPECTED_FILE,
            ],
            check=True,
        )

        commit_line = (log_result.stdout or "").strip()

        print("\n[8/8] Write publisher status...")
        write_publisher_status(
            result_status,
            detail=detail,
            commit=commit_line,
            candidate_hash=candidate_hash,
            candidate_size=candidate_size,
            worker_status=worker_status,
        )

        if backup_path:
            backup_path.unlink(missing_ok=True)

        print()
        print("=" * 70)
        print("MCI CENTRAL PUBLISH FINISHED SUCCESSFULLY")
        print("STATUS:", result_status)
        print("COMMIT:", commit_line)
        print("=" * 70)

    except Exception as exc:
        if staging_path:
            staging_path.unlink(missing_ok=True)

        if not remote_publish_completed:
            if backup_path and backup_path.exists():
                print("Restoring previous MCI target after failure...")
                os.replace(backup_path, target_path)

            elif not target_existed and target_path.exists():
                target_path.unlink(missing_ok=True)

        elif backup_path and backup_path.exists():
            print(
                "Remote publish already completed; "
                "keeping the published local target."
            )
            backup_path.unlink(missing_ok=True)

        return_code = getattr(exc, "return_code", None)
        write_publisher_status(
            "failed",
            detail=repr(exc),
            candidate_hash=candidate_hash,
            candidate_size=candidate_size,
            worker_status=worker_status,
            failed_stage=LAST_FAILED_STAGE,
            return_code=return_code,
        )

        print()
        print("=" * 70)
        print("MCI CENTRAL PUBLISH FAILED")
        print("FAILED_STAGE:", LAST_FAILED_STAGE)
        print("ERROR:", repr(exc))
        print("=" * 70)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
