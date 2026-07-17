import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
PUBLISH_LOCK_PATH = PROJECT_ROOT / "git_publish.lock"

PUBLISH_LOCK_STALE_SECONDS = int(
    os.getenv("GIT_PUBLISH_LOCK_STALE_SECONDS", "1800")
)

COMMAND_TIMEOUT_SECONDS = int(
    os.getenv("GIT_PUBLISH_COMMAND_TIMEOUT_SECONDS", "600")
)

REMOTE = os.getenv("GIT_REMOTE", "origin")
BRANCH = os.getenv("GIT_BRANCH", "main")

ALLOWED_FILES = {
    "public_configs.json",
    "public_configs_mci.json",
}

PUBLISH_ORDER = [
    "public_configs_mci.json",
    "public_configs.json",
]


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Validate and publish public config artifacts through "
            "an isolated temporary Git worktree."
        )
    )

    parser.add_argument(
        "--only",
        choices=sorted(ALLOWED_FILES),
        help=(
            "Publish only the selected artifact. "
            "Recommended for ownership-separated publishing flows."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate, fetch, create the temporary worktree and show "
            "the staged change without committing or pushing."
        ),
    )

    return parser.parse_args()


def run(
    command,
    *,
    cwd=PROJECT_ROOT,
    env=None,
    check=True,
    timeout=None,
):
    timeout = timeout or COMMAND_TIMEOUT_SECONDS

    command = [str(item) for item in command]

    print()
    print("$ " + " ".join(command))

    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""

        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")

        if output:
            print(
                output,
                end="" if output.endswith("\n") else "\n",
            )

        raise RuntimeError(
            f"Command timed out after {timeout}s: "
            + " ".join(command)
        ) from exc

    if result.stdout:
        print(
            result.stdout,
            end="" if result.stdout.endswith("\n") else "\n",
        )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed with exit code "
            f"{result.returncode}: {command}"
        )

    return result


def validate_public_path(path):
    path = Path(path).resolve()

    if not path.exists():
        raise FileNotFoundError(
            f"Public config artifact not found: {path}"
        )

    env = os.environ.copy()
    env["PUBLIC_CONFIGS_FILE"] = str(path)

    print()
    print("=" * 70)
    print("VALIDATE:", path)
    print("=" * 70)

    run(
        [
            sys.executable,
            "-u",
            "check_public_export.py",
        ],
        env=env,
    )


def resolve_targets(only):
    if only:
        target_path = PROJECT_ROOT / only

        if not target_path.exists():
            raise FileNotFoundError(
                f"Requested artifact does not exist: {target_path}"
            )

        return [only]

    targets = [
        filename
        for filename in PUBLISH_ORDER
        if (PROJECT_ROOT / filename).exists()
    ]

    if not targets:
        raise RuntimeError(
            "No allowed public config artifacts were found."
        )

    return targets


def acquire_publish_lock():
    if PUBLISH_LOCK_PATH.exists():
        lock_age = (
            time.time()
            - PUBLISH_LOCK_PATH.stat().st_mtime
        )

        if lock_age > PUBLISH_LOCK_STALE_SECONDS:
            print(
                "Removing stale publish lock: "
                f"age={round(lock_age, 1)} seconds"
            )
            PUBLISH_LOCK_PATH.unlink()

        else:
            raise RuntimeError(
                "Git publisher is already running: "
                f"{PUBLISH_LOCK_PATH}"
            )

    try:
        file_descriptor = os.open(
            str(PUBLISH_LOCK_PATH),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
        ) as lock_file:
            lock_file.write(f"pid={os.getpid()}\n")
            lock_file.write(
                f"created_at={time.time()}\n"
            )

    except FileExistsError as exc:
        raise RuntimeError(
            "Git publisher lock already exists: "
            f"{PUBLISH_LOCK_PATH}"
        ) from exc


def release_publish_lock():
    try:
        PUBLISH_LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def fetch_remote_branch():
    print()
    print("=" * 70)
    print("FETCH REMOTE BRANCH")
    print("REMOTE:", REMOTE)
    print("BRANCH:", BRANCH)
    print("=" * 70)

    run(
        [
            "git",
            "fetch",
            REMOTE,
            BRANCH,
        ]
    )


def create_temporary_worktree():
    temp_root = Path(
        tempfile.mkdtemp(
            prefix="mnproxy_git_publish_"
        )
    )

    worktree_path = temp_root / "worktree"

    print()
    print("=" * 70)
    print("CREATE TEMPORARY WORKTREE")
    print("PATH:", worktree_path)
    print("BASE:", f"{REMOTE}/{BRANCH}")
    print("=" * 70)

    try:
        run(
            [
                "git",
                "worktree",
                "add",
                "--detach",
                str(worktree_path),
                f"{REMOTE}/{BRANCH}",
            ]
        )

    except Exception:
        shutil.rmtree(
            temp_root,
            ignore_errors=True,
        )
        raise

    return temp_root, worktree_path


def cleanup_temporary_worktree(
    temp_root,
    worktree_path,
):
    if worktree_path is not None:
        print()
        print("=" * 70)
        print("REMOVE TEMPORARY WORKTREE")
        print("=" * 70)

        run(
            [
                "git",
                "worktree",
                "remove",
                "--force",
                str(worktree_path),
            ],
            check=False,
        )

    if temp_root is not None:
        shutil.rmtree(
            temp_root,
            ignore_errors=True,
        )

    run(
        [
            "git",
            "worktree",
            "prune",
        ],
        check=False,
    )



def get_config_text(item):
    if isinstance(item, str):
        return item.strip()

    if isinstance(item, dict):
        return str(
            item.get("config_text") or ""
        ).strip()

    return ""


def build_semantic_payload(path):
    path = Path(path)

    with open(
        path,
        "r",
        encoding="utf-8-sig",
    ) as file_handle:
        payload = json.load(file_handle)

    hot = payload.get("hot") or []
    reserve = payload.get("reserve") or []

    return {
        "version": payload.get("version"),
        "strategy": payload.get("strategy"),
        "operator": payload.get("operator"),
        "hot": [
            get_config_text(item)
            for item in hot
        ],
        "reserve": [
            get_config_text(item)
            for item in reserve
        ],
    }


def get_semantic_hash(path):
    semantic_payload = build_semantic_payload(
        path
    )

    canonical = json.dumps(
        semantic_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(
        canonical
    ).hexdigest()


def has_semantic_change(
    source_path,
    remote_path,
):
    source_hash = get_semantic_hash(
        source_path
    )

    if not Path(remote_path).exists():
        print(
            "REMOTE ARTIFACT DOES NOT EXIST."
        )
        print(
            "SOURCE SEMANTIC HASH:",
            source_hash,
        )
        return True

    try:
        remote_hash = get_semantic_hash(
            remote_path
        )

    except Exception as exc:
        print(
            "WARNING: unable to calculate "
            "remote semantic hash:",
            repr(exc),
        )
        return True

    print(
        "SOURCE SEMANTIC HASH:",
        source_hash,
    )
    print(
        "REMOTE SEMANTIC HASH:",
        remote_hash,
    )

    return source_hash != remote_hash

def copy_validate_and_stage(
    worktree_path,
    targets,
):
    changed_targets = []

    for filename in targets:
        source_path = PROJECT_ROOT / filename
        destination_path = worktree_path / filename

        print()
        print("=" * 70)
        print("COMPARE PUBLIC ARTIFACT")
        print("SOURCE:", source_path)
        print("REMOTE SNAPSHOT:", destination_path)
        print("=" * 70)

        semantic_changed = has_semantic_change(
            source_path,
            destination_path,
        )

        if not semantic_changed:
            print(
                "NO SEMANTIC CONTENT CHANGE:",
                filename,
            )
            print(
                "Timestamp and metric-only changes "
                "will not be published."
            )
            continue

        print(
            "SEMANTIC CONTENT CHANGE FOUND:",
            filename,
        )

        print()
        print("=" * 70)
        print("COPY PUBLIC ARTIFACT")
        print("SOURCE:", source_path)
        print("DESTINATION:", destination_path)
        print("=" * 70)

        destination_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copy2(
            source_path,
            destination_path,
        )

        # Validate the exact snapshot copied into
        # the isolated Git worktree.
        validate_public_path(
            destination_path
        )

        run(
            [
                "git",
                "add",
                "--",
                filename,
            ],
            cwd=worktree_path,
        )

        diff_result = run(
            [
                "git",
                "diff",
                "--cached",
                "--quiet",
                "--",
                filename,
            ],
            cwd=worktree_path,
            check=False,
        )

        if diff_result.returncode == 0:
            print(
                "NO GIT CONTENT CHANGE:",
                filename,
            )
            continue

        if diff_result.returncode == 1:
            print(
                "GIT CONTENT CHANGE FOUND:",
                filename,
            )
            changed_targets.append(filename)
            continue

        raise RuntimeError(
            "Unable to compare staged artifact: "
            f"{filename}; "
            f"git diff exit="
            f"{diff_result.returncode}"
        )

    return changed_targets

def show_staged_changes(
    worktree_path,
    targets,
):
    print()
    print("=" * 70)
    print("STAGED PUBLIC ARTIFACT CHANGES")
    print("=" * 70)

    run(
        [
            "git",
            "diff",
            "--cached",
            "--name-status",
            "--",
            *targets,
        ],
        cwd=worktree_path,
    )

    run(
        [
            "git",
            "diff",
            "--cached",
            "--stat",
            "--",
            *targets,
        ],
        cwd=worktree_path,
    )


def commit_and_push(
    worktree_path,
    changed_targets,
):
    if len(changed_targets) == 1:
        commit_message = (
            f"Update {changed_targets[0]}"
        )
    else:
        commit_message = (
            "Update public config artifacts"
        )

    print()
    print("=" * 70)
    print("COMMIT PUBLIC ARTIFACTS")
    print("MESSAGE:", commit_message)
    print("=" * 70)

    run(
        [
            "git",
            "commit",
            "-m",
            commit_message,
            "--",
            *changed_targets,
        ],
        cwd=worktree_path,
    )

    print()
    print("=" * 70)
    print("PUSH PUBLIC ARTIFACTS")
    print(
        "DESTINATION:",
        f"{REMOTE}/{BRANCH}",
    )
    print("=" * 70)

    run(
        [
            "git",
            "push",
            REMOTE,
            f"HEAD:refs/heads/{BRANCH}",
        ],
        cwd=worktree_path,
    )


def main(args):
    print("=" * 70)
    print("ISOLATED PUBLIC CONFIG PUBLISHER")
    print("PROJECT:", PROJECT_ROOT)
    print("REMOTE:", REMOTE)
    print("BRANCH:", BRANCH)
    print("ONLY:", args.only or "ALL AVAILABLE")
    print("DRY RUN:", args.dry_run)
    print(
        "COMMAND TIMEOUT:",
        COMMAND_TIMEOUT_SECONDS,
    )
    print("=" * 70)

    targets = resolve_targets(args.only)

    print()
    print("REQUESTED TARGETS:")

    for target in targets:
        print(" ", target)

    # Early validation of each live source artifact.
    for target in targets:
        validate_public_path(
            PROJECT_ROOT / target
        )

    fetch_remote_branch()

    temp_root = None
    worktree_path = None

    try:
        temp_root, worktree_path = (
            create_temporary_worktree()
        )

        changed_targets = copy_validate_and_stage(
            worktree_path,
            targets,
        )

        if not changed_targets:
            print()
            print("=" * 70)
            print("PUBLISH_RESULT=no_change")
            print(
                "Remote already contains the same "
                "public artifact content."
            )
            print("=" * 70)
            return "no_change"

        show_staged_changes(
            worktree_path,
            changed_targets,
        )

        if args.dry_run:
            print()
            print("=" * 70)
            print("PUBLISH_RESULT=dry_run")
            print(
                "Dry run completed. "
                "No commit or push was performed."
            )
            print("=" * 70)
            return "dry_run"

        commit_and_push(
            worktree_path,
            changed_targets,
        )

        print()
        print("=" * 70)
        print("PUBLISH_RESULT=published")
        print(
            "PUBLIC CONFIG PUBLISH "
            "FINISHED SUCCESSFULLY"
        )
        print("=" * 70)

        return "published"

    finally:
        cleanup_temporary_worktree(
            temp_root,
            worktree_path,
        )


if __name__ == "__main__":
    args = parse_args()
    lock_acquired = False

    try:
        acquire_publish_lock()
        lock_acquired = True
        main(args)

    except Exception as exc:
        print()
        print("=" * 70)
        print("PUBLIC CONFIG PUBLISH FAILED")
        print("ERROR:", repr(exc))
        print("=" * 70)
        raise SystemExit(1)

    finally:
        if lock_acquired:
            release_publish_lock()
