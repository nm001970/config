import argparse
import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path

from worker.config_sanitizer import sanitize_config_text
from worker.parsers.factory import parse_config


PROJECT_ROOT = Path(__file__).resolve().parent
DATABASE_PATH = PROJECT_ROOT / "configs.db"
CANONICAL_REMARK = "@mnproxyBot"


RESET_VALUES = {
    "status": "new",
    "latency": 0.0,
    "exit_ip": None,
    "last_error": None,
    "last_checked": None,
    "quality_status": "pending",
    "quality_score": 0.0,
    "score": 0.0,
    "fail_count": 0,
    "ping_min_ms": None,
    "ping_avg_ms": None,
    "download_mbps": None,
    "upload_mbps": None,
    "quality_error": None,
    "quality_reject_reason": None,
    "quality_last_checked": None,
}


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Canonicalize stored VMess configs and repair "
            "rows affected by the old fragment-branding bug."
        )
    )

    mode = parser.add_mutually_exclusive_group()

    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Analyze changes without modifying the database.",
    )

    mode.add_argument(
        "--apply",
        action="store_true",
        help="Create a backup and apply the migration.",
    )

    return parser.parse_args()


def calculate_hash(config_text: str) -> str:
    return hashlib.sha256(
        config_text.encode("utf-8")
    ).hexdigest()


def get_table_columns(connection):
    rows = connection.execute(
        "PRAGMA table_info(configs)"
    ).fetchall()

    return {
        row[1]
        for row in rows
    }


def create_backup():
    stamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = PROJECT_ROOT / (
        f"configs.before_vmess_migration_{stamp}.db"
    )

    source = sqlite3.connect(DATABASE_PATH)
    destination = sqlite3.connect(backup_path)

    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()

    return backup_path


def build_migration_plan(connection):
    rows = connection.execute(
        """
        SELECT
            id,
            config_hash,
            config_text,
            status,
            quality_status
        FROM configs
        WHERE protocol = 'vmess'
        ORDER BY id
        """
    ).fetchall()

    plan = []
    parse_failed_before = 0
    unchanged = 0

    canonical_hash_owners = {}

    for (
        config_id,
        stored_hash,
        stored_text,
        status,
        quality_status,
    ) in rows:
        old_parse_failed = False

        try:
            parse_config(stored_text)
        except Exception:
            old_parse_failed = True
            parse_failed_before += 1

        canonical_text = sanitize_config_text(
            stored_text,
            remark=CANONICAL_REMARK,
        )

        if not canonical_text:
            raise RuntimeError(
                "Sanitizer rejected VMess config "
                f"ID={config_id}"
            )

        parsed = parse_config(canonical_text)

        if parsed.protocol != "vmess":
            raise RuntimeError(
                "Unexpected protocol after sanitization "
                f"ID={config_id}: {parsed.protocol}"
            )

        if (
            parsed.metadata.get("remark")
            != CANONICAL_REMARK
        ):
            raise RuntimeError(
                "Canonical branding missing "
                f"ID={config_id}"
            )

        canonical_hash = calculate_hash(
            canonical_text
        )

        previous_owner = canonical_hash_owners.get(
            canonical_hash
        )

        if previous_owner is not None:
            raise RuntimeError(
                "Canonical duplicate detected between "
                f"IDs {previous_owner} and {config_id}"
            )

        canonical_hash_owners[
            canonical_hash
        ] = config_id

        changed = (
            canonical_text != stored_text
            or canonical_hash != stored_hash
        )

        if not changed:
            unchanged += 1
            continue

        plan.append(
            {
                "id": config_id,
                "stored_hash": stored_hash,
                "canonical_hash": canonical_hash,
                "canonical_text": canonical_text,
                "old_parse_failed": old_parse_failed,
                "status": status,
                "quality_status": quality_status,
            }
        )

    return {
        "total": len(rows),
        "parse_failed_before": parse_failed_before,
        "changed": len(plan),
        "unchanged": unchanged,
        "reset_for_retest": sum(
            1
            for item in plan
            if item["old_parse_failed"]
        ),
        "plan": plan,
    }


def verify_existing_hash_conflicts(
    connection,
    plan,
):
    migrating_ids = {
        item["id"]
        for item in plan
    }

    for item in plan:
        owners = connection.execute(
            """
            SELECT id, protocol
            FROM configs
            WHERE config_hash = ?
              AND id != ?
            """,
            (
                item["canonical_hash"],
                item["id"],
            ),
        ).fetchall()

        foreign_owners = [
            owner
            for owner in owners
            if owner[0] not in migrating_ids
        ]

        if foreign_owners:
            raise RuntimeError(
                "Existing config_hash conflict for "
                f"VMess ID={item['id']}: "
                f"{foreign_owners}"
            )


def apply_plan(
    connection,
    plan,
    available_columns,
):
    updated = 0
    reset_for_retest = 0

    for item in plan:
        assignments = [
            "config_text = ?",
            "config_hash = ?",
        ]

        values = [
            item["canonical_text"],
            item["canonical_hash"],
        ]

        if item["old_parse_failed"]:
            for column, value in RESET_VALUES.items():
                if column not in available_columns:
                    continue

                assignments.append(
                    f"{column} = ?"
                )
                values.append(value)

            reset_for_retest += 1

        values.append(item["id"])

        connection.execute(
            f"""
            UPDATE configs
            SET {", ".join(assignments)}
            WHERE id = ?
            """,
            values,
        )

        updated += 1

    return updated, reset_for_retest


def verify_database(connection):
    rows = connection.execute(
        """
        SELECT id, config_hash, config_text
        FROM configs
        WHERE protocol = 'vmess'
        ORDER BY id
        """
    ).fetchall()

    hashes = set()

    for config_id, stored_hash, config_text in rows:
        calculated_hash = calculate_hash(
            config_text
        )

        if calculated_hash != stored_hash:
            raise RuntimeError(
                "Hash mismatch after migration "
                f"ID={config_id}"
            )

        if "#" in config_text:
            raise RuntimeError(
                "VMess URI fragment remains "
                f"ID={config_id}"
            )

        parsed = parse_config(config_text)

        if parsed.protocol != "vmess":
            raise RuntimeError(
                "Invalid protocol after migration "
                f"ID={config_id}"
            )

        if (
            parsed.metadata.get("remark")
            != CANONICAL_REMARK
        ):
            raise RuntimeError(
                "Branding verification failed "
                f"ID={config_id}"
            )

        if stored_hash in hashes:
            raise RuntimeError(
                "Duplicate VMess hash after migration "
                f"ID={config_id}"
            )

        hashes.add(stored_hash)

    integrity_result = connection.execute(
        "PRAGMA integrity_check"
    ).fetchone()[0]

    if integrity_result != "ok":
        raise RuntimeError(
            "SQLite integrity check failed: "
            + str(integrity_result)
        )

    return len(rows)


def main():
    args = parse_args()

    apply_changes = args.apply

    if not args.apply and not args.dry_run:
        apply_changes = False

    if not DATABASE_PATH.exists():
        raise SystemExit(
            f"Database not found: {DATABASE_PATH}"
        )

    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30,
    )

    try:
        available_columns = get_table_columns(
            connection
        )

        result = build_migration_plan(
            connection
        )

        verify_existing_hash_conflicts(
            connection,
            result["plan"],
        )

        print("=" * 70)
        print("VMESS CANONICAL MIGRATION")
        print("=" * 70)
        print("DATABASE:", DATABASE_PATH)
        print("CANONICAL REMARK:", CANONICAL_REMARK)
        print(
            "MODE:",
            "APPLY" if apply_changes else "DRY RUN",
        )
        print()
        print("TOTAL VMESS:", result["total"])
        print(
            "PARSE FAILED BEFORE:",
            result["parse_failed_before"],
        )
        print(
            "ROWS TO UPDATE:",
            result["changed"],
        )
        print(
            "ROWS ALREADY CANONICAL:",
            result["unchanged"],
        )
        print(
            "ROWS TO RESET FOR RETEST:",
            result["reset_for_retest"],
        )

        print()
        print("AVAILABLE RESET COLUMNS:")

        for column in RESET_VALUES:
            if column in available_columns:
                print(" ", column)

        if not apply_changes:
            print()
            print(
                "DRY RUN COMPLETED. "
                "DATABASE WAS NOT MODIFIED."
            )
            return

        connection.close()

        backup_path = create_backup()

        print()
        print("BACKUP CREATED:", backup_path)

        connection = sqlite3.connect(
            DATABASE_PATH,
            timeout=30,
        )

        try:
            connection.execute(
                "BEGIN IMMEDIATE"
            )

            updated, reset_for_retest = apply_plan(
                connection,
                result["plan"],
                available_columns,
            )

            verified_total = verify_database(
                connection
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise

        print()
        print("UPDATED:", updated)
        print(
            "RESET FOR RETEST:",
            reset_for_retest,
        )
        print(
            "VERIFIED VMESS:",
            verified_total,
        )
        print("SQLITE INTEGRITY: ok")
        print()
        print(
            "VMESS MIGRATION COMPLETED SUCCESSFULLY"
        )

    finally:
        try:
            connection.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()