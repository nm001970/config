from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOG_SPECS = [
    {
        "key": "raw_to_reserve",
        "label": "Raw to Reserve",
        "pattern": "raw_to_reserve_irancell_*.log",
    },
    {
        "key": "reserve_health",
        "label": "Reserve Health",
        "pattern": "reserve_health_irancell_*.log",
    },
    {
        "key": "hot_manager",
        "label": "Hot Manager",
        "pattern": "hot_manager_irancell_*.log",
    },
    {
        "key": "config_update",
        "label": "Config Update",
        "pattern": "config_update_irancell_*.log",
    },
    {
        "key": "export_publish",
        "label": "Export and Publish",
        "pattern": "export_publish_irancell_*.log",
    },
]


def _empty_file_info() -> dict[str, Any]:
    return {
        "exists": False,
        "name": None,
        "path": None,
        "size_bytes": None,
        "modified_at": None,
        "age_minutes": None,
        "error": None,
    }


def _calculate_age_minutes(
    modified_timestamp: float,
) -> float:
    modified_at = datetime.fromtimestamp(
        modified_timestamp,
        tz=timezone.utc,
    )

    age_seconds = (
        datetime.now(timezone.utc) - modified_at
    ).total_seconds()

    return max(
        0.0,
        age_seconds / 60,
    )


def _inspect_file(
    path: Path,
) -> dict[str, Any]:
    result = _empty_file_info()

    try:
        stat = path.stat()

        result.update(
            {
                "exists": True,
                "name": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime,
                    tz=timezone.utc,
                ).isoformat(),
                "age_minutes": _calculate_age_minutes(
                    stat.st_mtime
                ),
                "error": None,
            }
        )

    except FileNotFoundError:
        pass

    except OSError as exc:
        result["error"] = str(exc)

    return result


def _find_latest_file(
    directory: Path,
    pattern: str,
) -> dict[str, Any]:
    result = _empty_file_info()

    if not directory.is_dir():
        return result

    try:
        candidates = [
            path
            for path in directory.glob(pattern)
            if path.is_file()
        ]

        if not candidates:
            return result

        latest_path = max(
            candidates,
            key=lambda path: path.stat().st_mtime,
        )

        return _inspect_file(latest_path)

    except OSError as exc:
        result["error"] = str(exc)
        return result


def _empty_publisher_summary(
    status_path: Path,
) -> dict[str, Any]:
    return {
        "file": _inspect_file(status_path),
        "valid_json": False,
        "publisher_id": None,
        "operator": None,
        "status": None,
        "detail": None,
        "commit": None,
        "created_at": None,
        "error": None,
    }


def _read_mci_publisher_status(
    status_path: Path,
) -> dict[str, Any]:
    summary = _empty_publisher_summary(
        status_path
    )

    if not status_path.is_file():
        return summary

    try:
        payload = json.loads(
            status_path.read_text(
                encoding="utf-8-sig"
            )
        )

        if not isinstance(payload, dict):
            raise ValueError(
                "MCI publisher status root must "
                "be a JSON object."
            )

        summary.update(
            {
                "valid_json": True,
                "publisher_id": payload.get(
                    "publisher_id"
                ),
                "operator": payload.get("operator"),
                "status": payload.get("status"),
                "detail": payload.get("detail"),
                "commit": payload.get("commit"),
                "created_at": payload.get(
                    "created_at"
                ),
                "error": None,
            }
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        summary["error"] = str(exc)

    return summary


def read_runtime_summary(
    project_root: Path,
) -> dict[str, Any]:
    logs_directory = project_root / "logs"

    log_rows = []

    for spec in LOG_SPECS:
        file_info = _find_latest_file(
            logs_directory,
            spec["pattern"],
        )

        log_rows.append(
            {
                "key": spec["key"],
                "label": spec["label"],
                "pattern": spec["pattern"],
                **file_info,
            }
        )

    publisher_status_path = (
        project_root / "publisher_status_mci.json"
    )

    return {
        "logs_directory": str(logs_directory),
        "logs_directory_exists": (
            logs_directory.is_dir()
        ),
        "logs": log_rows,
        "found_log_count": sum(
            row["exists"]
            for row in log_rows
        ),
        "missing_log_count": sum(
            not row["exists"]
            for row in log_rows
        ),
        "mci_publisher": (
            _read_mci_publisher_status(
                publisher_status_path
            )
        ),
    }