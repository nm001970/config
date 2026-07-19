from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SNAPSHOT_SCHEMA_VERSION = 1

FORBIDDEN_KEYS = {
    "config_text",
    "config_hash",
    "exit_ip",
    "last_error",
    "quality_error",
}

COUNT_FIELDS = {
    "status_counts",
    "protocol_counts",
    "quality_status_counts",
    "pool_status_counts",
    "pool_gate_status_counts",
    "pool_health_counts",
    "pool_operator_counts",
}

TIMESTAMP_FIELDS = {
    "latest_connectivity_check",
    "latest_quality_check",
    "latest_gate_check",
    "latest_pool_health_check",
}

CONNECTIVITY_FIELDS = {
    "id",
    "protocol",
    "status",
    "latency",
    "success_count",
    "fail_count",
    "last_checked",
}

QUALITY_FIELDS = {
    "id",
    "protocol",
    "quality_status",
    "quality_score",
    "download_mbps",
    "upload_mbps",
    "ping_avg_ms",
    "last_quality_checked",
}


def _empty_database_summary() -> dict[str, Any]:
    return {
        "readable": False,
        "total_configs": 0,
        "status_counts": {},
        "protocol_counts": {},
        "quality_status_counts": {},
        "latest_connectivity_check": None,
        "latest_quality_check": None,
        "pool_status_counts": {},
        "pool_gate_status_counts": {},
        "pool_health_counts": {},
        "pool_operator_counts": {},
        "latest_gate_check": None,
        "latest_pool_health_check": None,
        "recent_connectivity_checks": [],
        "recent_quality_checks": [],
    }


def _empty_snapshot(
    path: Path,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": False,
        "valid": False,
        "schema_version": None,
        "operator": None,
        "source_id": None,
        "generated_at": None,
        "database": _empty_database_summary(),
        "error": None,
    }


def _find_forbidden_key(
    value: Any,
    location: str = "$",
) -> str | None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            normalized_key = str(key).lower()

            if normalized_key in FORBIDDEN_KEYS:
                return f"{location}.{key}"

            forbidden_location = _find_forbidden_key(
                nested_value,
                f"{location}.{key}",
            )

            if forbidden_location:
                return forbidden_location

    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            forbidden_location = _find_forbidden_key(
                nested_value,
                f"{location}[{index}]",
            )

            if forbidden_location:
                return forbidden_location

    return None


def _safe_non_negative_int(
    value: Any,
) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _normalize_count_map(
    value: Any,
) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}

    result = {}

    for key, count in value.items():
        result[str(key)] = (
            _safe_non_negative_int(count)
        )

    return result


def _normalize_recent_rows(
    value: Any,
    allowed_fields: set[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    rows = []

    for item in value[:limit]:
        if not isinstance(item, dict):
            continue

        rows.append(
            {
                key: item.get(key)
                for key in allowed_fields
                if key in item
            }
        )

    return rows


def _normalize_database_summary(
    value: Any,
) -> dict[str, Any]:
    summary = _empty_database_summary()

    if not isinstance(value, dict):
        return summary

    summary["readable"] = bool(
        value.get("readable", False)
    )

    summary["total_configs"] = (
        _safe_non_negative_int(
            value.get("total_configs")
        )
    )

    for field in COUNT_FIELDS:
        summary[field] = _normalize_count_map(
            value.get(field)
        )

    for field in TIMESTAMP_FIELDS:
        timestamp = value.get(field)

        summary[field] = (
            timestamp
            if isinstance(timestamp, str)
            else None
        )

    summary["recent_connectivity_checks"] = (
        _normalize_recent_rows(
            value.get(
                "recent_connectivity_checks"
            ),
            CONNECTIVITY_FIELDS,
        )
    )

    summary["recent_quality_checks"] = (
        _normalize_recent_rows(
            value.get(
                "recent_quality_checks"
            ),
            QUALITY_FIELDS,
        )
    )

    return summary


def read_operator_snapshot(
    path: Path,
    expected_operator: str | None = None,
) -> dict[str, Any]:
    summary = _empty_snapshot(path)

    if not path.is_file():
        return summary

    summary["exists"] = True

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

        if not isinstance(payload, dict):
            raise ValueError(
                "Snapshot root must be a JSON object."
            )

        forbidden_location = _find_forbidden_key(
            payload
        )

        if forbidden_location:
            raise ValueError(
                "Snapshot contains a forbidden field at "
                + forbidden_location
            )

        schema_version = payload.get(
            "schema_version"
        )

        if schema_version != SNAPSHOT_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported snapshot schema version: "
                f"{schema_version!r}"
            )

        operator = payload.get("operator")

        if not isinstance(operator, str):
            raise ValueError(
                "Snapshot operator is missing."
            )

        operator = operator.strip().lower()

        if (
            expected_operator is not None
            and operator
            != expected_operator.strip().lower()
        ):
            raise ValueError(
                "Unexpected snapshot operator: "
                f"{operator!r}"
            )

        generated_at = payload.get(
            "generated_at"
        )

        if not isinstance(generated_at, str):
            raise ValueError(
                "Snapshot generated_at is missing."
            )

        database_payload = payload.get(
            "database"
        )

        if not isinstance(database_payload, dict):
            raise ValueError(
                "Snapshot database section is missing."
            )

        source_id = payload.get("source_id")

        summary.update(
            {
                "valid": True,
                "schema_version": schema_version,
                "operator": operator,
                "source_id": (
                    source_id
                    if isinstance(source_id, str)
                    else None
                ),
                "generated_at": generated_at,
                "database": (
                    _normalize_database_summary(
                        database_payload
                    )
                ),
                "error": None,
            }
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        summary["error"] = str(exc)

    return summary