import hashlib
import sqlite3
from pathlib import Path

from worker.parsers.factory import parse_config


CURRENT_DB = Path("configs.db")
BACKUP_DB = Path(
    "configs.before_vmess_migration_20260716_114324.db"
)
CANONICAL_REMARK = "@mnproxyBot"

CHECK_COLUMNS = [
    "status",
    "latency",
    "exit_ip",
    "last_error",
    "last_checked",
    "quality_status",
    "quality_score",
    "score",
    "fail_count",
    "ping_min_ms",
    "ping_avg_ms",
    "download_mbps",
    "upload_mbps",
    "quality_error",
]

EXPECTED_RESET = {
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
}


def sha256(text):
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def normalize_number(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return value


def load_vmess_rows(connection):
    selected_columns = [
        "id",
        "config_hash",
        "config_text",
        *CHECK_COLUMNS,
    ]

    rows = connection.execute(
        f"""
        SELECT {", ".join(selected_columns)}
        FROM configs
        WHERE protocol = 'vmess'
        ORDER BY id
        """
    ).fetchall()

    return {
        row[0]: dict(
            zip(selected_columns, row)
        )
        for row in rows
    }


def main():
    if not CURRENT_DB.exists():
        raise RuntimeError(
            f"Current database missing: {CURRENT_DB}"
        )

    if not BACKUP_DB.exists():
        raise RuntimeError(
            f"Backup database missing: {BACKUP_DB}"
        )

    current = sqlite3.connect(CURRENT_DB)
    backup = sqlite3.connect(BACKUP_DB)

    try:
        current_integrity = current.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        backup_integrity = backup.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]

        current_rows = load_vmess_rows(current)
        backup_rows = load_vmess_rows(backup)

        if set(current_rows) != set(backup_rows):
            raise RuntimeError(
                "VMess ID set differs between current "
                "database and backup"
            )

        changed_text = 0
        changed_hash = 0
        old_parse_failed = 0
        current_parse_failed = []
        branding_failed = []
        fragment_remaining = []
        hash_mismatch = []
        reset_failed = []
        preserved_metadata_failed = []

        current_hashes = set()

        for config_id in sorted(current_rows):
            current_row = current_rows[config_id]
            backup_row = backup_rows[config_id]

            old_was_invalid = False

            try:
                parse_config(
                    backup_row["config_text"]
                )
            except Exception:
                old_was_invalid = True
                old_parse_failed += 1

            if (
                current_row["config_text"]
                != backup_row["config_text"]
            ):
                changed_text += 1

            if (
                current_row["config_hash"]
                != backup_row["config_hash"]
            ):
                changed_hash += 1

            calculated_hash = sha256(
                current_row["config_text"]
            )

            if (
                calculated_hash
                != current_row["config_hash"]
            ):
                hash_mismatch.append(config_id)

            if current_row["config_hash"] in current_hashes:
                raise RuntimeError(
                    "Duplicate current VMess hash: "
                    f"ID={config_id}"
                )

            current_hashes.add(
                current_row["config_hash"]
            )

            if "#" in current_row["config_text"]:
                fragment_remaining.append(config_id)

            try:
                parsed = parse_config(
                    current_row["config_text"]
                )
            except Exception as exc:
                current_parse_failed.append(
                    (config_id, repr(exc))
                )
                continue

            if (
                parsed.metadata.get("remark")
                != CANONICAL_REMARK
            ):
                branding_failed.append(config_id)

            if old_was_invalid:
                failed_columns = []

                for column, expected in EXPECTED_RESET.items():
                    actual = current_row[column]

                    if (
                        normalize_number(actual)
                        != normalize_number(expected)
                    ):
                        failed_columns.append(
                            (
                                column,
                                actual,
                                expected,
                            )
                        )

                if failed_columns:
                    reset_failed.append(
                        (config_id, failed_columns)
                    )

            else:
                changed_metadata = []

                for column in CHECK_COLUMNS:
                    if (
                        current_row[column]
                        != backup_row[column]
                    ):
                        changed_metadata.append(
                            (
                                column,
                                backup_row[column],
                                current_row[column],
                            )
                        )

                if changed_metadata:
                    preserved_metadata_failed.append(
                        (config_id, changed_metadata)
                    )

        print("=" * 70)
        print("VMESS POST-MIGRATION VERIFICATION")
        print("=" * 70)
        print("CURRENT DB:", CURRENT_DB.resolve())
        print("BACKUP DB:", BACKUP_DB.resolve())
        print()
        print("CURRENT SQLITE INTEGRITY:", current_integrity)
        print("BACKUP SQLITE INTEGRITY:", backup_integrity)
        print("CURRENT VMESS:", len(current_rows))
        print("BACKUP VMESS:", len(backup_rows))
        print("TEXT CHANGED:", changed_text)
        print("HASH CHANGED:", changed_hash)
        print("OLD PARSE FAILED:", old_parse_failed)
        print("CURRENT PARSE FAILED:", len(current_parse_failed))
        print("BRANDING FAILED:", len(branding_failed))
        print("FRAGMENT REMAINING:", len(fragment_remaining))
        print("HASH MISMATCH:", len(hash_mismatch))
        print("RESET FAILED:", len(reset_failed))
        print(
            "PRESERVED METADATA FAILED:",
            len(preserved_metadata_failed),
        )

        if current_parse_failed:
            print(
                "CURRENT PARSE FAILURE SAMPLES:",
                current_parse_failed[:10],
            )

        if reset_failed:
            print(
                "RESET FAILURE SAMPLES:",
                reset_failed[:5],
            )

        if preserved_metadata_failed:
            print(
                "METADATA FAILURE SAMPLES:",
                preserved_metadata_failed[:5],
            )

        errors = [
            current_integrity != "ok",
            backup_integrity != "ok",
            len(current_rows) != 2990,
            len(backup_rows) != 2990,
            changed_text != 2990,
            changed_hash != 2990,
            old_parse_failed != 852,
            bool(current_parse_failed),
            bool(branding_failed),
            bool(fragment_remaining),
            bool(hash_mismatch),
            bool(reset_failed),
            bool(preserved_metadata_failed),
        ]

        if any(errors):
            raise RuntimeError(
                "POST-MIGRATION VERIFICATION FAILED"
            )

        print()
        print(
            "RESULT: VMESS MIGRATION IS FULLY VERIFIED"
        )

    finally:
        current.close()
        backup.close()


if __name__ == "__main__":
    main()
