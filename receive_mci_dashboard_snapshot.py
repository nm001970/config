from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent

INCOMING_METRICS = Path(
    os.getenv(
        "MCI_INCOMING_METRICS",
        (
            r"C:\MNProxyIncoming\mci"
            r"\dashboard_metrics_mci.json"
        ),
    )
)

INCOMING_STATUS = Path(
    os.getenv(
        "MCI_INCOMING_STATUS",
        (
            r"C:\MNProxyIncoming\status"
            r"\worker_status_mci.json"
        ),
    )
)

TARGET_METRICS = Path(
    os.getenv(
        "MCI_DASHBOARD_METRICS_TARGET",
        str(
            PROJECT_ROOT
            / ".runtime"
            / "dashboard"
            / "dashboard_metrics_mci.json"
        ),
    )
)

RECEIVER_STATUS = Path(
    os.getenv(
        "MCI_DASHBOARD_RECEIVER_STATUS",
        str(
            PROJECT_ROOT
            / ".runtime"
            / "dashboard"
            / "mci_metrics_receiver_status.json"
        ),
    )
)

FORBIDDEN_KEYS = {
    "config_text",
    "config_hash",
    "exit_ip",
    "last_error",
    "quality_error",
}


def utc_now_text() -> str:
    return datetime.now().astimezone().isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(
            lambda: handle.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def load_json_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Required JSON file was not found: {path}"
        )

    payload = json.loads(
        path.read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(payload, dict):
        raise ValueError(
            f"JSON root must be an object: {path}"
        )

    return payload


def find_forbidden_key(
    value: Any,
    location: str = "$",
) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = str(key).lower()

            if normalized_key in FORBIDDEN_KEYS:
                return f"{location}.{key}"

            found = find_forbidden_key(
                nested_value,
                f"{location}.{key}",
            )

            if found:
                return found

    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            found = find_forbidden_key(
                nested_value,
                f"{location}[{index}]",
            )

            if found:
                return found

    return None


def parse_iso_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError(
            "Snapshot generated_at must be a string."
        )

    normalized = value.strip().replace(
        "Z",
        "+00:00",
    )

    if not normalized:
        raise ValueError(
            "Snapshot generated_at is empty."
        )

    try:
        return datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(
            "Snapshot generated_at is not valid ISO-8601."
        ) from exc


def validate_snapshot(
    path: Path,
) -> dict[str, Any]:
    payload = load_json_file(path)

    forbidden_location = find_forbidden_key(
        payload
    )

    if forbidden_location:
        raise ValueError(
            "Snapshot contains a forbidden field at "
            + forbidden_location
        )

    if payload.get("schema_version") != 1:
        raise ValueError(
            "Snapshot schema_version must be 1."
        )

    operator = payload.get("operator")

    if (
        not isinstance(operator, str)
        or operator.strip().lower() != "mci"
    ):
        raise ValueError(
            "Snapshot operator must be mci."
        )

    parse_iso_timestamp(
        payload.get("generated_at")
    )

    database = payload.get("database")

    if not isinstance(database, dict):
        raise ValueError(
            "Snapshot database section is missing."
        )

    if database.get("readable") is not True:
        raise ValueError(
            "Snapshot database summary is not readable."
        )

    total_configs = database.get(
        "total_configs"
    )

    try:
        normalized_total = int(total_configs)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Snapshot total_configs is invalid."
        ) from exc

    if normalized_total < 0:
        raise ValueError(
            "Snapshot total_configs cannot be negative."
        )

    for field in (
        "recent_connectivity_checks",
        "recent_quality_checks",
    ):
        rows = database.get(field)

        if not isinstance(rows, list):
            raise ValueError(
                f"Snapshot {field} must be a list."
            )

        if len(rows) > 100:
            raise ValueError(
                f"Snapshot {field} exceeds 100 rows."
            )

    return payload


def atomic_write_json(
    path: Path,
    payload: dict[str, Any],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=path.parent,
        )
    )

    temporary_path = Path(
        temporary_name
    )

    try:
        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=True,
                indent=2,
            )

            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temporary_path,
            path,
        )

    finally:
        temporary_path.unlink(
            missing_ok=True
        )


def create_staging_copy(
    source_path: Path,
) -> Path:
    TARGET_METRICS.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    file_descriptor, temporary_name = (
        tempfile.mkstemp(
            prefix="dashboard_metrics_mci.",
            suffix=".staging.json",
            dir=TARGET_METRICS.parent,
        )
    )

    os.close(file_descriptor)

    staging_path = Path(
        temporary_name
    )

    shutil.copy2(
        source_path,
        staging_path,
    )

    return staging_path


def write_receiver_status(
    status: str,
    *,
    detail: str = "",
    metrics_hash: str = "",
    metrics_size: int = 0,
    generated_at: str = "",
) -> None:
    payload = {
        "receiver_id":
            "computer_mci_metrics_receiver",
        "operator":
            "mci",
        "status":
            status,
        "detail":
            detail,
        "created_at":
            utc_now_text(),
        "incoming_metrics":
            str(INCOMING_METRICS),
        "target_metrics":
            str(TARGET_METRICS),
        "metrics_sha256":
            metrics_hash,
        "metrics_size":
            metrics_size,
        "metrics_generated_at":
            generated_at,
    }

    atomic_write_json(
        RECEIVER_STATUS,
        payload,
    )


def main() -> int:
    staging_path: Path | None = None
    metrics_hash = ""
    metrics_size = 0
    generated_at = ""

    print("=" * 70)
    print("MN Proxy - MCI Dashboard Snapshot Receiver")
    print("INCOMING:", INCOMING_METRICS)
    print("STATUS:", INCOMING_STATUS)
    print("TARGET:", TARGET_METRICS)
    print("=" * 70)

    try:
        worker_status = load_json_file(
            INCOMING_STATUS
        )

        if (
            worker_status.get("operator")
            != "mci"
        ):
            raise ValueError(
                "Worker status operator is not mci."
            )

        if (
            worker_status.get("status")
            != "candidate_ready"
        ):
            raise ValueError(
                "Worker status is not candidate_ready."
            )

        if (
            worker_status.get("metrics_sent")
            is not True
        ):
            raise ValueError(
                "Worker status does not confirm "
                "metrics_sent=true."
            )

        if not INCOMING_METRICS.is_file():
            raise FileNotFoundError(
                "Incoming MCI metrics file "
                "was not found."
            )

        if INCOMING_METRICS.name.endswith(
            (".tmp", ".partial")
        ):
            raise ValueError(
                "Incoming metrics file still has "
                "a temporary suffix."
            )

        metrics_size = (
            INCOMING_METRICS.stat().st_size
        )

        if metrics_size < 100:
            raise ValueError(
                "Incoming metrics file is too small: "
                f"{metrics_size} bytes."
            )

        metrics_hash = sha256_file(
            INCOMING_METRICS
        )

        declared_hash = str(
            worker_status.get(
                "metrics_sha256"
            )
            or ""
        ).strip().lower()

        if not declared_hash:
            raise ValueError(
                "Worker status does not contain "
                "metrics_sha256."
            )

        if declared_hash != metrics_hash:
            raise ValueError(
                "Incoming metrics hash does not "
                "match worker status."
            )

        staging_path = create_staging_copy(
            INCOMING_METRICS
        )

        if (
            sha256_file(staging_path)
            != metrics_hash
        ):
            raise ValueError(
                "Staging metrics hash mismatch."
            )

        payload = validate_snapshot(
            staging_path
        )

        generated_at = str(
            payload["generated_at"]
        )

        declared_generated_at = str(
            worker_status.get(
                "metrics_generated_at"
            )
            or ""
        ).strip()

        if (
            declared_generated_at
            and declared_generated_at
            != generated_at
        ):
            raise ValueError(
                "Metrics generated_at does not "
                "match worker status."
            )

        current_hash = (
            sha256_file(TARGET_METRICS)
            if TARGET_METRICS.is_file()
            else ""
        )

        if current_hash == metrics_hash:
            staging_path.unlink(
                missing_ok=True
            )

            staging_path = None

            result_status = "no_change"
            detail = (
                "Incoming MCI dashboard snapshot "
                "is identical to the current target."
            )

        else:
            os.replace(
                staging_path,
                TARGET_METRICS,
            )

            staging_path = None

            if (
                sha256_file(TARGET_METRICS)
                != metrics_hash
            ):
                raise ValueError(
                    "Installed metrics hash mismatch."
                )

            validate_snapshot(
                TARGET_METRICS
            )

            result_status = "installed"
            detail = (
                "MCI dashboard snapshot was "
                "validated and installed atomically."
            )

        write_receiver_status(
            result_status,
            detail=detail,
            metrics_hash=metrics_hash,
            metrics_size=metrics_size,
            generated_at=generated_at,
        )

        print("STATUS:", result_status)
        print("SHA256:", metrics_hash)
        print("SIZE:", metrics_size)
        print("GENERATED_AT:", generated_at)
        print("TARGET:", TARGET_METRICS)

        return 0

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
        TypeError,
        ValueError,
    ) as exc:
        if staging_path is not None:
            staging_path.unlink(
                missing_ok=True
            )

        write_receiver_status(
            "failed",
            detail=repr(exc),
            metrics_hash=metrics_hash,
            metrics_size=metrics_size,
            generated_at=generated_at,
        )

        print("STATUS: failed")
        print("ERROR:", repr(exc))

        return 1


if __name__ == "__main__":
    raise SystemExit(main())