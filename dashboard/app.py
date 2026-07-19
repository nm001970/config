from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from operator_snapshot import read_operator_snapshot
from process_metrics import read_process_summary
from runtime_metrics import read_runtime_summary
from sqlite_metrics import read_database_summary


DASHBOARD_ROOT = Path(
    __file__
).resolve().parents[1]

RUNTIME_ROOT = Path(
    os.environ.get(
        "V2RAY_RUNTIME_ROOT",
        str(DASHBOARD_ROOT),
    )
).expanduser().resolve()

PUBLIC_EXPORTS = {
    "Irancell / Default": RUNTIME_ROOT / "public_configs.json",
    "MCI": RUNTIME_ROOT / "public_configs_mci.json",
}

LOCAL_DATABASES = {
    "Irancell / Default": RUNTIME_ROOT / "configs.db",
}

MCI_SNAPSHOT_PATH = (
    RUNTIME_ROOT
    / ".runtime"
    / "dashboard"
    / "dashboard_metrics_mci.json"
)

FRESH_MAX_MINUTES = 30
STALE_MINUTES = 120


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "-"

    size = float(size_bytes)

    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.2f} {unit}"

        size /= 1024

    return f"{size_bytes} B"


def format_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return "-"

    return datetime.fromtimestamp(timestamp).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def parse_timestamp(
    value: Any,
) -> datetime | None:
    if value is None:
        return None

    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, (int, float)):
        return datetime.fromtimestamp(
            value,
            tz=timezone.utc,
        )

    elif isinstance(value, str):
        normalized = value.strip()

        if not normalized:
            return None

        normalized = normalized.replace(
            "Z",
            "+00:00",
        )

        try:
            parsed = datetime.fromisoformat(
                normalized
            )
        except ValueError:
            return None

    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


def calculate_age_minutes(
    value: Any,
) -> float | None:
    parsed = parse_timestamp(value)

    if parsed is None:
        return None

    age_seconds = (
        datetime.now(timezone.utc) - parsed
    ).total_seconds()

    return max(
        0.0,
        age_seconds / 60,
    )


def classify_freshness(
    value: Any,
) -> tuple[str, float | None]:
    age_minutes = calculate_age_minutes(value)

    if age_minutes is None:
        return "Missing", None

    if age_minutes <= FRESH_MAX_MINUTES:
        return "Fresh", age_minutes

    if age_minutes <= STALE_MINUTES:
        return "Delayed", age_minutes

    return "Stale", age_minutes


def format_age(
    age_minutes: float | None,
) -> str:
    if age_minutes is None:
        return "-"

    if age_minutes < 1:
        return "< 1 min"

    if age_minutes < 60:
        return f"{age_minutes:.0f} min"

    age_hours = age_minutes / 60

    if age_hours < 24:
        return f"{age_hours:.1f} h"

    return f"{age_hours / 24:.1f} d"

def format_measurement(
    value: Any,
    decimals: int = 2,
    hide_non_positive: bool = False,
) -> str:
    if value is None:
        return "-"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"

    if hide_non_positive and number <= 0:
        return "-"

    return f"{number:.{decimals}f}"


def format_utc_timestamp(
    value: Any,
) -> str:
    parsed = parse_timestamp(value)

    if parsed is None:
        return "-"

    return parsed.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def inspect_file(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {
            "exists": False,
            "size_bytes": None,
            "modified_at": None,
            "error": None,
        }
    except OSError as exc:
        return {
            "exists": False,
            "size_bytes": None,
            "modified_at": None,
            "error": str(exc),
        }

    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "modified_at": stat.st_mtime,
        "error": None,
    }


def inspect_public_export(path: Path) -> dict[str, Any]:
    file_info = inspect_file(path)

    result = {
        **file_info,
        "valid_json": False,
        "generated_at": None,
        "strategy": None,
        "hot_count": 0,
        "reserve_count": 0,
    }

    if not file_info["exists"]:
        return result

    try:
        payload = json.loads(
            path.read_text(encoding="utf-8-sig")
        )

        if not isinstance(payload, dict):
            raise ValueError(
                "Public export root must be a JSON object."
            )

        hot_items = payload.get("hot") or []
        reserve_items = payload.get("reserve") or []

        if not isinstance(hot_items, list):
            raise ValueError(
                "Public export hot section must be a list."
            )

        if not isinstance(reserve_items, list):
            raise ValueError(
                "Public export reserve section must be a list."
            )

        result.update(
            {
                "valid_json": True,
                "generated_at": payload.get("generated_at"),
                "strategy": payload.get("strategy"),
                "hot_count": len(hot_items),
                "reserve_count": len(reserve_items),
                "error": None,
            }
        )

    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        result["error"] = str(exc)

    return result


def build_export_rows(
    summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []

    for operator, summary in summaries.items():
        rows.append(
            {
                "Operator": operator,
                "Status": (
                    "Ready"
                    if summary["valid_json"]
                    else "Unavailable"
                ),
                "Hot": summary["hot_count"],
                "Reserve": summary["reserve_count"],
                "Generated at": (
                    summary["generated_at"] or "-"
                ),
                "File size": format_size(
                    summary["size_bytes"]
                ),
                "Modified": format_timestamp(
                    summary["modified_at"]
                ),
                "Error": summary["error"] or "-",
            }
        )

    return rows


def build_database_rows(
    summaries: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []

    for operator, summary in summaries.items():
        if not summary["exists"]:
            status = "Missing"
        elif summary["readable"]:
            status = "Ready"
        else:
            status = "Error"

        rows.append(
            {
                "Operator": operator,
                "Status": status,
                "Total": summary["total_configs"],
                "Alive": summary["status_counts"].get(
                    "alive",
                    0,
                ),
                "New": summary["status_counts"].get(
                    "new",
                    0,
                ),
                "Qualified": (
                    summary["quality_status_counts"].get(
                        "qualified",
                        0,
                    )
                ),
                "Hot": summary["pool_status_counts"].get(
                    "hot",
                    0,
                ),
                "Reserve": (
                    summary["pool_status_counts"].get(
                        "reserve",
                        0,
                    )
                ),
                "Quarantined": (
                    summary["pool_status_counts"].get(
                        "quarantined",
                        0,
                    )
                ),
                "Last connectivity": (
                    summary["latest_connectivity_check"]
                    or "-"
                ),
                "Last quality": (
                    summary["latest_quality_check"]
                    or "-"
                ),
                "Error": summary["error"] or "-",
            }
        )

    return rows

def build_count_rows(
    counts: dict[str, int],
) -> list[dict[str, Any]]:
    return [
        {
            "Status": status,
            "Count": count,
        }
        for status, count in counts.items()
    ]

def calculate_percentage(
    value: int,
    total: int,
) -> float:
    if total <= 0:
        return 0.0

    return round(
        (value / total) * 100,
        2,
    )


def build_distribution_rows(
    counts: dict[str, int],
) -> list[dict[str, Any]]:
    return [
        {
            "Category": category,
            "Count": count,
        }
        for category, count in counts.items()
    ]

def build_worker_rows(
    process_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []

    for worker in process_summary["workers"]:
        rows.append(
            {
                "Worker": worker["label"],
                "Status": worker["status"],
                "Instances": worker["process_count"],
                "Process IDs": (
                    ", ".join(
                        str(process_id)
                        for process_id
                        in worker["process_ids"]
                    )
                    or "-"
                ),
                "Runner": worker["runner"],
            }
        )

    return rows

def build_runtime_rows(
    runtime_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []

    for log_summary in runtime_summary["logs"]:
        freshness_status, age_minutes = (
            classify_freshness(
                log_summary["modified_at"]
            )
        )

        if log_summary["error"]:
            status = "Error"
        elif not log_summary["exists"]:
            status = "Missing"
        else:
            status = freshness_status

        rows.append(
            {
                "Worker": log_summary["label"],
                "Status": status,
                "Latest log": (
                    log_summary["name"] or "-"
                ),
                "Log size": format_size(
                    log_summary["size_bytes"]
                ),
                "Age": format_age(age_minutes),
                "Modified UTC": (
                    log_summary["modified_at"] or "-"
                ),
                "Error": (
                    log_summary["error"] or "-"
                ),
            }
        )

    return rows

def build_recent_connectivity_rows(
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []

    for check in checks:
        rows.append(
            {
                "Config ID": check.get("id", "-"),
                "Protocol": (
                    check.get("protocol") or "-"
                ),
                "Status": (
                    check.get("status") or "-"
                ),
                "Latency (ms)": format_measurement(
                    check.get("latency"),
                    hide_non_positive=True,
                ),
                "Successes": int(
                    check.get("success_count") or 0
                ),
                "Failures": int(
                    check.get("fail_count") or 0
                ),
                "Checked UTC": (
                    format_utc_timestamp(
                        check.get("last_checked")
                    )
                ),
            }
        )

    return rows


def build_recent_quality_rows(
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []

    for check in checks:
        rows.append(
            {
                "Config ID": check.get("id", "-"),
                "Protocol": (
                    check.get("protocol") or "-"
                ),
                "Quality status": (
                    check.get("quality_status") or "-"
                ),
                "Score": format_measurement(
                    check.get("quality_score")
                ),
                "Download (Mbps)": (
                    format_measurement(
                        check.get("download_mbps")
                    )
                ),
                "Upload (Mbps)": (
                    format_measurement(
                        check.get("upload_mbps")
                    )
                ),
                "Ping average (ms)": (
                    format_measurement(
                        check.get("ping_avg_ms")
                    )
                ),
                "Checked UTC": (
                    format_utc_timestamp(
                        check.get(
                            "last_quality_checked"
                        )
                    )
                ),
            }
        )

    return rows


def build_freshness_rows(
    export_summaries: dict[str, dict[str, Any]],
    database_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    sources = [
        (
            "Irancell public export",
            export_summaries[
                "Irancell / Default"
            ]["generated_at"]
            or export_summaries[
                "Irancell / Default"
            ]["modified_at"],
        ),
        (
            "MCI public export",
            export_summaries[
                "MCI"
            ]["generated_at"]
            or export_summaries[
                "MCI"
            ]["modified_at"],
        ),
        (
            "Connectivity testing",
            database_summary[
                "latest_connectivity_check"
            ],
        ),
        (
            "Quality testing",
            database_summary[
                "latest_quality_check"
            ],
        ),
        (
            "Pool gate",
            database_summary[
                "latest_gate_check"
            ],
        ),
        (
            "Pool health",
            database_summary[
                "latest_pool_health_check"
            ],
        ),
    ]

    rows = []

    for source_name, timestamp in sources:
        status, age_minutes = classify_freshness(
            timestamp
        )

        parsed_timestamp = parse_timestamp(
            timestamp
        )

        rows.append(
            {
                "Pipeline stage": source_name,
                "Status": status,
                "Age": format_age(age_minutes),
                "Last activity UTC": (
                    parsed_timestamp.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    if parsed_timestamp
                    else "-"
                ),
            }
        )

    return rows

st.set_page_config(
    page_title="MPROXY Admin",
    page_icon="ðŸ“¡",
    layout="wide",
)

st.title("MPROXY Admin Dashboard")

st.caption(
    "Read-only local monitoring â€” no database writes, "
    "worker execution, publishing, or Railway changes."
)

st.button("Refresh data")

export_summaries = {
    operator: inspect_public_export(path)
    for operator, path in PUBLIC_EXPORTS.items()
}

database_summaries = {
    operator: read_database_summary(path)
    for operator, path in LOCAL_DATABASES.items()
}

process_summary = read_process_summary()
runtime_summary = read_runtime_summary(
    RUNTIME_ROOT
)

mci_snapshot = read_operator_snapshot(
    MCI_SNAPSHOT_PATH,
    expected_operator="mci",
)
 
default_summary = export_summaries["Irancell / Default"]
mci_summary = export_summaries["MCI"]
default_database_summary = database_summaries[
    "Irancell / Default"
]
mci_database_summary = mci_snapshot[
    "database"
]

metric_columns = st.columns(4)

metric_columns[0].metric(
    "Irancell Hot",
    default_summary["hot_count"],
)

metric_columns[1].metric(
    "Irancell Reserve",
    default_summary["reserve_count"],
)

metric_columns[2].metric(
    "MCI Hot",
    mci_summary["hot_count"],
)

metric_columns[3].metric(
    "MCI Reserve",
    mci_summary["reserve_count"],
)

st.subheader("Public delivery artifacts")

st.dataframe(
    build_export_rows(export_summaries),
    width="stretch",
)

st.subheader("Irancell database metrics")

database_metric_columns = st.columns(5)

database_metric_columns[0].metric(
    "Total configs",
    default_database_summary["total_configs"],
)

database_metric_columns[1].metric(
    "Alive",
    default_database_summary[
        "status_counts"
    ].get("alive", 0),
)

database_metric_columns[2].metric(
    "Qualified",
    default_database_summary[
        "quality_status_counts"
    ].get("qualified", 0),
)

database_metric_columns[3].metric(
    "Hot",
    default_database_summary[
        "pool_status_counts"
    ].get("hot", 0),
)

database_metric_columns[4].metric(
    "Reserve",
    default_database_summary[
        "pool_status_counts"
    ].get("reserve", 0),
)

st.subheader("Worker processes")

if process_summary["query_ok"]:
    worker_metric_columns = st.columns(4)

    worker_metric_columns[0].metric(
        "Configured workers",
        process_summary["worker_count"],
    )

    worker_metric_columns[1].metric(
        "Running",
        process_summary["running_count"],
    )

    worker_metric_columns[2].metric(
        "Stopped",
        process_summary["stopped_count"],
    )

    worker_metric_columns[3].metric(
        "Duplicates",
        process_summary["duplicate_count"],
    )

    st.dataframe(
        build_worker_rows(process_summary),
        width="stretch",
        hide_index=True,
    )

    if process_summary["all_running"]:
        st.success(
            "All configured worker processes are running "
            "with exactly one instance."
        )

    elif process_summary["duplicate_count"] > 0:
        duplicate_workers = [
            worker["label"]
            for worker in process_summary["workers"]
            if worker["status"] == "Duplicate"
        ]

        st.error(
            "Duplicate worker processes detected: "
            + ", ".join(duplicate_workers)
        )

    else:
        stopped_workers = [
            worker["label"]
            for worker in process_summary["workers"]
            if worker["status"] == "Stopped"
        ]

        st.warning(
            "Stopped worker processes: "
            + ", ".join(stopped_workers)
        )

    if process_summary["legacy_processes"]:
        st.error(
            "Legacy or conflicting worker processes "
            "were detected."
        )

        st.dataframe(
            process_summary["legacy_processes"],
            width="stretch",
            hide_index=True,
        )

else:
    st.error(
        "Worker process inspection failed: "
        + (
            process_summary["error"]
            or "Unknown process query error."
        )
    )


st.subheader("MCI database metrics")

st.caption(
    "MCI metrics are received as a sanitized JSON "
    "snapshot from the separate MCI laptop."
)

st.caption(
    "Snapshot source: "
    f"`{MCI_SNAPSHOT_PATH}`"
)
mci_snapshot_freshness, mci_snapshot_age_minutes = (
    classify_freshness(
        mci_snapshot["generated_at"]
    )
)

mci_snapshot_columns = st.columns(5)

mci_snapshot_columns[0].metric(
    "Snapshot status",
    (
        "Valid"
        if mci_snapshot["valid"]
        else "Missing / Invalid"
    ),
)

mci_snapshot_columns[1].metric(
    "Freshness",
    mci_snapshot_freshness,
)

mci_snapshot_columns[2].metric(
    "Age",
    format_age(
        mci_snapshot_age_minutes
    ),
)

mci_snapshot_columns[3].metric(
    "Total configs",
    mci_database_summary[
        "total_configs"
    ],
)

mci_snapshot_columns[4].metric(
    "Source",
    mci_snapshot["source_id"] or "-",
)

if not mci_snapshot["exists"]:
    st.info(
        "The MCI dashboard snapshot has not reached "
        "the central runtime yet."
    )

elif not mci_snapshot["valid"]:
    st.error(
        "The MCI dashboard snapshot is invalid: "
        + (
            mci_snapshot["error"]
            or "Unknown validation error."
        )
    )

elif not mci_database_summary["readable"]:
    st.error(
        "The MCI snapshot is valid, but its database "
        "summary is marked as unreadable."
    )

else:
    mci_status_counts = mci_database_summary[
        "status_counts"
    ]

    mci_quality_counts = mci_database_summary[
        "quality_status_counts"
    ]

    mci_pool_counts = mci_database_summary[
        "pool_status_counts"
    ]

    mci_metric_columns = st.columns(5)

    mci_metric_columns[0].metric(
        "Alive",
        mci_status_counts.get(
            "alive",
            0,
        ),
    )

    mci_metric_columns[1].metric(
        "Qualified",
        mci_quality_counts.get(
            "qualified",
            0,
        ),
    )

    mci_metric_columns[2].metric(
        "Hot",
        mci_pool_counts.get(
            "hot",
            0,
        ),
    )

    mci_metric_columns[3].metric(
        "Reserve",
        mci_pool_counts.get(
            "reserve",
            0,
        ),
    )

    mci_metric_columns[4].metric(
        "Quarantined",
        mci_pool_counts.get(
            "quarantined",
            0,
        ),
    )

    mci_count_tables = st.columns(3)

    with mci_count_tables[0]:
        st.markdown("#### Status")

        st.dataframe(
            [
                {
                    "Status": key,
                    "Count": count,
                }
                for key, count
                in sorted(
                    mci_status_counts.items()
                )
            ],
            width="stretch",
            hide_index=True,
        )

    with mci_count_tables[1]:
        st.markdown("#### Quality")

        st.dataframe(
            [
                {
                    "Quality status": key,
                    "Count": count,
                }
                for key, count
                in sorted(
                    mci_quality_counts.items()
                )
            ],
            width="stretch",
            hide_index=True,
        )

    with mci_count_tables[2]:
        st.markdown("#### Pool")

        st.dataframe(
            [
                {
                    "Pool status": key,
                    "Count": count,
                }
                for key, count
                in sorted(
                    mci_pool_counts.items()
                )
            ],
            width="stretch",
            hide_index=True,
        )

    st.markdown("#### Recent MCI checks")

    mci_connectivity_tab, mci_quality_tab = (
        st.tabs(
            [
                "Connectivity checks",
                "Quality checks",
            ]
        )
    )

    with mci_connectivity_tab:
        mci_connectivity_rows = (
            mci_database_summary.get(
                "recent_connectivity_checks",
                [],
            )
        )

        if mci_connectivity_rows:
            st.dataframe(
                build_recent_connectivity_rows(
                    mci_connectivity_rows
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info(
                "No recent MCI connectivity "
                "checks are available."
            )

    with mci_quality_tab:
        mci_quality_rows = (
            mci_database_summary.get(
                "recent_quality_checks",
                [],
            )
        )

        if mci_quality_rows:
            st.dataframe(
                build_recent_quality_rows(
                    mci_quality_rows
                ),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info(
                "No recent MCI quality checks "
                "are available."
            )


st.subheader("Worker runtime activity")
st.caption(
    f"Runtime data source: {RUNTIME_ROOT}"
)

runtime_metric_columns = st.columns(3)

runtime_metric_columns[0].metric(
    "Expected worker logs",
    len(runtime_summary["logs"]),
)

runtime_metric_columns[1].metric(
    "Logs found",
    runtime_summary["found_log_count"],
)

runtime_metric_columns[2].metric(
    "Logs missing",
    runtime_summary["missing_log_count"],
)

st.dataframe(
    build_runtime_rows(runtime_summary),
    width="stretch",
    hide_index=True,
)

runtime_errors = [
    log_summary["label"]
    for log_summary in runtime_summary["logs"]
    if log_summary["error"]
]

missing_runtime_logs = [
    log_summary["label"]
    for log_summary in runtime_summary["logs"]
    if not log_summary["exists"]
]

stale_runtime_logs = []

for log_summary in runtime_summary["logs"]:
    if not log_summary["exists"]:
        continue

    status, _ = classify_freshness(
        log_summary["modified_at"]
    )

    if status == "Stale":
        stale_runtime_logs.append(
            log_summary["label"]
        )

if runtime_errors:
    st.error(
        "Runtime log inspection errors: "
        + ", ".join(runtime_errors)
    )

if missing_runtime_logs:
    st.warning(
        "Missing worker logs: "
        + ", ".join(missing_runtime_logs)
    )

if stale_runtime_logs:
    st.warning(
        "Worker logs without recent activity: "
        + ", ".join(stale_runtime_logs)
    )

if (
    not runtime_errors
    and not missing_runtime_logs
    and not stale_runtime_logs
):
    st.success(
        "All expected worker logs are present "
        "and have recent activity."
    )

st.markdown("#### MCI central publisher")

mci_publisher = runtime_summary[
    "mci_publisher"
]

mci_publisher_file = mci_publisher["file"]

publisher_timestamp = (
    mci_publisher["created_at"]
    or mci_publisher_file["modified_at"]
)

publisher_freshness, publisher_age_minutes = (
    classify_freshness(publisher_timestamp)
)

publisher_columns = st.columns(4)

publisher_columns[0].metric(
    "Publisher status",
    mci_publisher["status"] or "Missing",
)

publisher_columns[1].metric(
    "Freshness",
    publisher_freshness,
)

publisher_columns[2].metric(
    "Age",
    format_age(publisher_age_minutes),
)

publisher_columns[3].metric(
    "Operator",
    mci_publisher["operator"] or "-",
)

if mci_publisher["valid_json"]:
    st.code(
        mci_publisher["commit"] or "-",
        language=None,
    )

    if mci_publisher["detail"]:
        st.caption(
            mci_publisher["detail"]
        )

elif mci_publisher["error"]:
    st.error(
        "MCI publisher status could not be read: "
        + mci_publisher["error"]
    )

else:
    st.info(
        "MCI publisher status file is not present."
    )


st.subheader("Pipeline freshness")

freshness_rows = build_freshness_rows(
    export_summaries,
    default_database_summary,
)

st.caption(
    f"Fresh: up to {FRESH_MAX_MINUTES} minutes â€” "
    f"Delayed: up to {STALE_MINUTES} minutes â€” "
    "Stale: older than that."
)

st.dataframe(
    freshness_rows,
    width="stretch",
    hide_index=True,
)

stale_sources = [
    row["Pipeline stage"]
    for row in freshness_rows
    if row["Status"] == "Stale"
]

missing_sources = [
    row["Pipeline stage"]
    for row in freshness_rows
    if row["Status"] == "Missing"
]

if missing_sources:
    st.error(
        "Missing activity timestamp: "
        + ", ".join(missing_sources)
    )

if stale_sources:
    st.warning(
        "Stale pipeline stages: "
        + ", ".join(stale_sources)
    )

if not stale_sources and not missing_sources:
    st.success(
        "All monitored pipeline stages have "
        "valid recent activity timestamps."
    )

st.subheader("Irancell Configuration analysis")

status_counts = default_database_summary[
    "status_counts"
]

quality_status_counts = default_database_summary[
    "quality_status_counts"
]

protocol_counts = default_database_summary[
    "protocol_counts"
]

alive_count = status_counts.get("alive", 0)

completed_connectivity_tests = (
    alive_count
    + status_counts.get("dead", 0)
    + status_counts.get("error", 0)
)

qualified_count = quality_status_counts.get(
    "qualified",
    0,
)

completed_quality_tests = (
    qualified_count
    + quality_status_counts.get("rejected", 0)
)

alive_rate = calculate_percentage(
    alive_count,
    completed_connectivity_tests,
)

qualification_rate = calculate_percentage(
    qualified_count,
    completed_quality_tests,
)

analysis_metric_columns = st.columns(4)

analysis_metric_columns[0].metric(
    "Completed connectivity tests",
    completed_connectivity_tests,
)

analysis_metric_columns[1].metric(
    "Alive rate",
    f"{alive_rate:.2f}%",
)

analysis_metric_columns[2].metric(
    "Completed quality tests",
    completed_quality_tests,
)

analysis_metric_columns[3].metric(
    "Qualification rate",
    f"{qualification_rate:.2f}%",
)

distribution_columns = st.columns(3)

with distribution_columns[0]:
    st.markdown("#### Connectivity status")

    st.bar_chart(
        build_distribution_rows(
            status_counts
        ),
        x="Category",
        y="Count",
        width="stretch",
    )

    st.dataframe(
        build_count_rows(status_counts),
        width="stretch",
        hide_index=True,
    )

with distribution_columns[1]:
    st.markdown("#### Protocol distribution")

    st.bar_chart(
        build_distribution_rows(
            protocol_counts
        ),
        x="Category",
        y="Count",
        width="stretch",
    )

    st.dataframe(
        build_count_rows(protocol_counts),
        width="stretch",
        hide_index=True,
    )

with distribution_columns[2]:
    st.markdown("#### Quality status")

    st.bar_chart(
        build_distribution_rows(
            quality_status_counts
        ),
        x="Category",
        y="Count",
        width="stretch",
    )

    st.dataframe(
        build_count_rows(
            quality_status_counts
        ),
        width="stretch",
        hide_index=True,
    )

st.subheader("Recent database activity")

st.caption(
    "Newest completed database checks first. "
    "A maximum of 20 records is displayed for "
    "each category."
)

recent_connectivity_checks = (
    default_database_summary.get(
        "recent_connectivity_checks",
        [],
    )
)

recent_quality_checks = (
    default_database_summary.get(
        "recent_quality_checks",
        [],
    )
)

connectivity_tab, quality_tab = st.tabs(
    [
        "Connectivity checks",
        "Quality checks",
    ]
)

with connectivity_tab:
    if recent_connectivity_checks:
        st.dataframe(
            build_recent_connectivity_rows(
                recent_connectivity_checks
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(
            "No completed connectivity checks "
            "were found."
        )

with quality_tab:
    if recent_quality_checks:
        st.dataframe(
            build_recent_quality_rows(
                recent_quality_checks
            ),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info(
            "No completed quality checks were found."
        )

st.subheader("Irancell pool monitoring")

pool_status_counts = default_database_summary[
    "pool_status_counts"
]

pool_gate_counts = default_database_summary[
    "pool_gate_status_counts"
]

pool_health_counts = default_database_summary[
    "pool_health_counts"
]

pool_metric_columns = st.columns(6)

pool_metric_columns[0].metric(
    "Hot",
    pool_status_counts.get("hot", 0),
)

pool_metric_columns[1].metric(
    "Reserve",
    pool_status_counts.get("reserve", 0),
)

pool_metric_columns[2].metric(
    "Gate accepted",
    pool_gate_counts.get("accepted", 0),
)

pool_metric_columns[3].metric(
    "Gate rejected",
    pool_gate_counts.get("rejected", 0),
)

pool_metric_columns[4].metric(
    "Healthy",
    pool_health_counts.get("healthy", 0),
)

pool_metric_columns[5].metric(
    "Failed",
    pool_health_counts.get("failed", 0),
)

pool_table_columns = st.columns(3)

with pool_table_columns[0]:
    st.markdown("#### Pool status")

    st.dataframe(
        build_count_rows(pool_status_counts),
        width="stretch",
        hide_index=True,
    )

with pool_table_columns[1]:
    st.markdown("#### Gate status")

    st.dataframe(
        build_count_rows(pool_gate_counts),
        width="stretch",
        hide_index=True,
    )

with pool_table_columns[2]:
    st.markdown("#### Health status")

    st.dataframe(
        build_count_rows(pool_health_counts),
        width="stretch",
        hide_index=True,
    )

pool_check_columns = st.columns(2)

pool_check_columns[0].metric(
    "Latest gate check",
    default_database_summary[
        "latest_gate_check"
    ] or "-",
)

pool_check_columns[1].metric(
    "Latest health check",
    default_database_summary[
        "latest_pool_health_check"
    ] or "-",
) 

st.subheader("Local configuration databases")

st.dataframe(
    build_database_rows(database_summaries),
    width="stretch",
    hide_index=True,
)

has_export_error = any(
    not summary["valid_json"]
    for summary in export_summaries.values()
)

if has_export_error:
    st.warning(
        "One or more public export files are missing "
        "or could not be parsed."
    )
else:
    st.success(
        "Both public delivery artifacts are readable."
    )

if not database_summaries[
    "Irancell / Default"
]["readable"]:
    st.error(
        "The Irancell database could not be read."
    )
