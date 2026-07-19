from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


def _empty_summary(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": False,
        "readable": False,
        "tables": [],
        "config_columns": [],
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
        "error": None,
    }


def _open_readonly_connection(
    path: Path,
) -> sqlite3.Connection:
    database_uri = (
        f"{path.resolve().as_uri()}?mode=ro"
    )

    connection = sqlite3.connect(
        database_uri,
        uri=True,
        timeout=2.0,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA query_only = ON"
    )

    return connection


def _get_tables(
    connection: sqlite3.Connection,
) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
        """
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def _get_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    safe_table_name = table_name.replace(
        '"',
        '""',
    )

    rows = connection.execute(
        f'PRAGMA table_info("{safe_table_name}")'
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def _get_scalar(
    connection: sqlite3.Connection,
    query: str,
) -> Any:
    row = connection.execute(query).fetchone()

    if row is None:
        return None

    return row[0]


def _get_group_counts(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
) -> dict[str, int]:
    safe_table_name = table_name.replace(
        '"',
        '""',
    )

    safe_column_name = column_name.replace(
        '"',
        '""',
    )

    rows = connection.execute(
        f"""
        SELECT
            COALESCE(
                CAST("{safe_column_name}" AS TEXT),
                "(null)"
            ) AS value,
            COUNT(*) AS item_count
        FROM "{safe_table_name}"
        GROUP BY "{safe_column_name}"
        ORDER BY item_count DESC, value ASC
        """
    ).fetchall()

    return {
        str(row["value"]): int(row["item_count"])
        for row in rows
    }

def _get_rows(
    connection: sqlite3.Connection,
    query: str,
    parameters: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = connection.execute(
        query,
        parameters,
    )

    column_names = [
        description[0]
        for description in cursor.description
    ]

    return [
        dict(zip(column_names, row))
        for row in cursor.fetchall()
    ]

def _get_recent_connectivity_checks(
    connection: sqlite3.Connection,
    config_columns: set[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    required_columns = {
        "id",
        "protocol",
        "status",
        "last_checked",
    }

    if not required_columns.issubset(
        config_columns
    ):
        return []

    selected_columns = [
        "id",
        "protocol",
        "status",
    ]

    optional_columns = [
        "latency",
        "success_count",
        "fail_count",
    ]

    selected_columns.extend(
        column
        for column in optional_columns
        if column in config_columns
    )

    selected_columns.append(
        "last_checked"
    )

    query = (
        "SELECT "
        + ", ".join(selected_columns)
        + " FROM configs "
        "WHERE last_checked IS NOT NULL "
        "ORDER BY last_checked DESC, id DESC "
        "LIMIT ?"
    )

    return _get_rows(
        connection,
        query,
        (limit,),
    )

def _get_recent_quality_checks(
    connection: sqlite3.Connection,
    config_columns: set[str],
    limit: int = 20,
) -> list[dict[str, Any]]:
    required_columns = {
        "id",
        "protocol",
        "quality_status",
        "last_quality_checked",
    }

    if not required_columns.issubset(
        config_columns
    ):
        return []

    selected_columns = [
        "id",
        "protocol",
        "quality_status",
    ]

    optional_columns = [
        "quality_score",
        "download_mbps",
        "upload_mbps",
        "ping_avg_ms",
    ]

    selected_columns.extend(
        column
        for column in optional_columns
        if column in config_columns
    )

    selected_columns.append(
        "last_quality_checked"
    )

    query = (
        "SELECT "
        + ", ".join(selected_columns)
        + " FROM configs "
        "WHERE last_quality_checked IS NOT NULL "
        "ORDER BY last_quality_checked DESC, id DESC "
        "LIMIT ?"
    )

    return _get_rows(
        connection,
        query,
        (limit,),
    )

def read_database_summary(
    path: Path,
) -> dict[str, Any]:
    summary = _empty_summary(path)

    if not path.is_file():
        return summary

    summary["exists"] = True

    connection = None

    try:
        connection = _open_readonly_connection(
            path
        )

        tables = _get_tables(connection)

        summary["tables"] = sorted(tables)

        if "configs" not in tables:
            raise ValueError(
                "Required table 'configs' was not found."
            )

        config_columns = _get_columns(
            connection,
            "configs",
        )

        config_column_set = set(
            config_columns
        )

        summary["config_columns"] = sorted(
            config_columns
        )

        summary["total_configs"] = int(
            _get_scalar(
                connection,
                'SELECT COUNT(*) FROM "configs"',
            )
            or 0
        )

        if "status" in config_columns:
            summary["status_counts"] = (
                _get_group_counts(
                    connection,
                    "configs",
                    "status",
                )
            )

        if "protocol" in config_columns:
            summary["protocol_counts"] = (
                _get_group_counts(
                    connection,
                    "configs",
                    "protocol",
                )
            )

        if "quality_status" in config_columns:
            summary["quality_status_counts"] = (
                _get_group_counts(
                    connection,
                    "configs",
                    "quality_status",
                )
            )

        if "last_checked" in config_columns:
            summary["latest_connectivity_check"] = (
                _get_scalar(
                    connection,
                    """
                    SELECT MAX(last_checked)
                    FROM configs
                    """,
                )
            )

        if "last_quality_checked" in config_columns:
            summary["latest_quality_check"] = (
                _get_scalar(
                    connection,
                    """
                    SELECT MAX(last_quality_checked)
                    FROM configs
                    """,
                )
            )

        summary["recent_connectivity_checks"] = (
            _get_recent_connectivity_checks(
                connection,
                config_column_set,
            )
        )

        summary["recent_quality_checks"] = (
            _get_recent_quality_checks(
                connection,
                config_column_set,
            )
        )

        if "config_pool" in tables:
            pool_columns = _get_columns(
                connection,
                "config_pool",
            )

            if "pool_status" in pool_columns:
                summary["pool_status_counts"] = (
                    _get_group_counts(
                        connection,
                        "config_pool",
                        "pool_status",
                    )
                )

            if "gate_status" in pool_columns:
                summary[
                    "pool_gate_status_counts"
                ] = _get_group_counts(
                    connection,
                    "config_pool",
                    "gate_status",
                )
            
            if "gate_checked_at" in pool_columns:
                summary["latest_gate_check"] = (
                    _get_scalar(
                        connection,
                        """
                        SELECT MAX(gate_checked_at)
                        FROM config_pool
                        """,
                    )
                )

            if "health_checked_at" in pool_columns:
                summary[
                    "latest_pool_health_check"
                ] = _get_scalar(
                    connection,
                    """
                    SELECT MAX(health_checked_at)
                    FROM config_pool
                    """,
                )

            if "health_status" in pool_columns:
                summary["pool_health_counts"] = (
                    _get_group_counts(
                        connection,
                        "config_pool",
                        "health_status",
                    )
                )

            if "operator" in pool_columns:
                summary["pool_operator_counts"] = (
                    _get_group_counts(
                        connection,
                        "config_pool",
                        "operator",
                    )
                )

        summary["readable"] = True

    except (
        sqlite3.Error,
        OSError,
        ValueError,
    ) as exc:
        summary["error"] = str(exc)

    finally:
        if connection is not None:
            connection.close()

    return summary