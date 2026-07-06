import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_sqlite_path():
    database_url = os.getenv("CONFIG_DATABASE_URL") or os.getenv("DATABASE_URL") or "sqlite:///configs.db"

    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise RuntimeError(f"Only sqlite database urls are supported here: {database_url}")

    db_part = database_url[len(prefix):]

    if db_part.startswith("/"):
        return Path(db_part)

    return PROJECT_ROOT / db_part


def get_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return [row[1] for row in rows]


def require_columns(columns, required):
    missing = [c for c in required if c not in columns]
    if missing:
        raise RuntimeError(f"Missing required columns: {missing}")


def pick_rows(conn, columns, where_sql, order_sql, limit, already_ids):
    if limit <= 0:
        return []

    exclude_sql = ""
    params = []

    if already_ids:
        placeholders = ",".join("?" for _ in already_ids)
        exclude_sql = f" AND id NOT IN ({placeholders})"
        params.extend(list(already_ids))

    sql = f"""
        SELECT *
        FROM configs
        WHERE ({where_sql})
        {exclude_sql}
        ORDER BY {order_sql}
        LIMIT {int(limit)}
    """

    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def summarize_rows(rows):
    status_counter = Counter()
    protocol_counter = Counter()

    for row in rows:
        status_counter[str(row.get("status") or "NULL")] += 1
        protocol_counter[str(row.get("protocol") or "NULL")] += 1

    return {
        "count": len(rows),
        "status": dict(status_counter),
        "protocol": dict(protocol_counter),
    }


def get_available_order_columns(columns):
    if "last_checked" in columns:
        last_checked_oldest = "last_checked IS NULL DESC, last_checked ASC, id DESC"
        last_checked_newest = "last_checked IS NULL DESC, last_checked DESC, id DESC"
    else:
        last_checked_oldest = "id ASC"
        last_checked_newest = "id DESC"

    return last_checked_oldest, last_checked_newest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=int(os.getenv("SMART_BATCH_LIMIT", "3500")))
    parser.add_argument("--newest-ratio", type=float, default=float(os.getenv("SMART_BATCH_NEWEST_RATIO", "0.60")))
    parser.add_argument("--alive-ratio", type=float, default=float(os.getenv("SMART_BATCH_ALIVE_RATIO", "0.25")))
    parser.add_argument("--retry-ratio", type=float, default=float(os.getenv("SMART_BATCH_RETRY_RATIO", "0.15")))
    parser.add_argument("--output", default=os.getenv("SMART_BATCH_PLAN_FILE", "smart_batch_plan.json"))
    args = parser.parse_args()

    db_path = resolve_sqlite_path()

    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    columns = get_columns(conn, "configs")
    require_columns(columns, ["id", "status", "protocol"])

    last_checked_oldest, last_checked_newest = get_available_order_columns(columns)

    total_count = conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0]

    newest_limit = int(args.limit * args.newest_ratio)
    alive_limit = int(args.limit * args.alive_ratio)
    retry_limit = args.limit - newest_limit - alive_limit

    already_ids = set()
    buckets = {}

    newest_where = """
        status IN ('new', 'not_tested')
        OR status IS NULL
    """

    newest_rows = pick_rows(
        conn,
        columns,
        newest_where,
        "id DESC",
        newest_limit,
        already_ids,
    )
    already_ids.update(row["id"] for row in newest_rows)
    buckets["newest_untested"] = newest_rows

    alive_where = """
        status IN ('alive', 'qualified')
    """

    alive_rows = pick_rows(
        conn,
        columns,
        alive_where,
        last_checked_oldest,
        alive_limit,
        already_ids,
    )
    already_ids.update(row["id"] for row in alive_rows)
    buckets["stale_alive_recheck"] = alive_rows

    retry_where = """
        status IN ('dead', 'error', 'rejected', 'timeout', 'failed')
    """

    retry_rows = pick_rows(
        conn,
        columns,
        retry_where,
        last_checked_oldest,
        retry_limit,
        already_ids,
    )
    already_ids.update(row["id"] for row in retry_rows)
    buckets["old_failed_retry"] = retry_rows

    remaining = args.limit - len(already_ids)

    if remaining > 0:
        fill_rows = pick_rows(
            conn,
            columns,
            "1=1",
            "id DESC",
            remaining,
            already_ids,
        )
        already_ids.update(row["id"] for row in fill_rows)
        buckets["newest_fill"] = fill_rows
    else:
        buckets["newest_fill"] = []

    selected = []
    for rows in buckets.values():
        selected.extend(rows)

    selected_ids = [row["id"] for row in selected]

    plan = {
        "created_at": datetime.now().isoformat(),
        "database": str(db_path),
        "total_configs": total_count,
        "requested_limit": args.limit,
        "selected_total": len(selected),
        "selected_min_id": min(selected_ids) if selected_ids else None,
        "selected_max_id": max(selected_ids) if selected_ids else None,
        "columns_detected": columns,
        "bucket_summary": {
            name: summarize_rows(rows)
            for name, rows in buckets.items()
        },
        "overall_summary": summarize_rows(selected),
        "selected_ids_preview": {
            "first_30": selected_ids[:30],
            "last_30": selected_ids[-30:],
        },
    }

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    print("=" * 70)
    print("SMART BATCH PLAN PREVIEW")
    print("=" * 70)
    print("DB:", db_path)
    print("TOTAL CONFIGS:", total_count)
    print("REQUESTED LIMIT:", args.limit)
    print("SELECTED TOTAL:", len(selected))
    print("OUTPUT:", output_path)
    print()

    for name, summary in plan["bucket_summary"].items():
        print(f"[{name}]")
        print("  count:", summary["count"])
        print("  status:", summary["status"])
        print("  protocol:", summary["protocol"])
        print()

    print("[overall]")
    print("  count:", plan["overall_summary"]["count"])
    print("  status:", plan["overall_summary"]["status"])
    print("  protocol:", plan["overall_summary"]["protocol"])
    print()
    print("RESULT: PREVIEW ONLY - DATABASE WAS NOT MODIFIED")


if __name__ == "__main__":
    main()