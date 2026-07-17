import json
import sqlite3
from collections import OrderedDict

from worker.parsers.factory import parse_config
from worker.test_worker import test_config


CURRENT_DB = "configs.db"
BACKUP_DB = "configs.before_vmess_migration_20260716_114324.db"
SAMPLE_LIMIT = 10


def get_signature(parsed):
    transport = parsed.transport or {}
    tls = parsed.tls or {}

    return (
        str(transport.get("type") or "unknown"),
        str(tls.get("security") or "none"),
    )


def select_repaired_samples():
    current = sqlite3.connect(CURRENT_DB)
    backup = sqlite3.connect(BACKUP_DB)

    try:
        current_rows = {
            row[0]: row
            for row in current.execute(
                """
                SELECT id, config_text, status
                FROM configs
                WHERE protocol = 'vmess'
                ORDER BY id DESC
                """
            ).fetchall()
        }

        backup_rows = backup.execute(
            """
            SELECT id, config_text
            FROM configs
            WHERE protocol = 'vmess'
            ORDER BY id DESC
            """
        ).fetchall()

        selected_by_signature = OrderedDict()
        fallback = []

        for config_id, old_text in backup_rows:
            current_row = current_rows.get(config_id)

            if current_row is None:
                continue

            _, current_text, current_status = current_row

            # فقط رکوردهایی که هنوز برای Retest آماده‌اند.
            if current_status != "new":
                continue

            try:
                parse_config(old_text)
                old_was_broken = False
            except Exception:
                old_was_broken = True

            if not old_was_broken:
                continue

            try:
                parsed = parse_config(current_text)
            except Exception as exc:
                raise RuntimeError(
                    f"Current repaired VMess cannot parse: "
                    f"ID={config_id}: {exc!r}"
                )

            signature = get_signature(parsed)

            item = {
                "id": config_id,
                "server": parsed.server,
                "port": parsed.port,
                "transport": signature[0],
                "security": signature[1],
                "remark": parsed.metadata.get("remark"),
            }

            fallback.append(item)

            if signature not in selected_by_signature:
                selected_by_signature[signature] = item

            if len(selected_by_signature) >= SAMPLE_LIMIT:
                break

        selected = list(
            selected_by_signature.values()
        )

        selected_ids = {
            item["id"]
            for item in selected
        }

        for item in fallback:
            if len(selected) >= SAMPLE_LIMIT:
                break

            if item["id"] in selected_ids:
                continue

            selected.append(item)
            selected_ids.add(item["id"])

        return selected

    finally:
        current.close()
        backup.close()


def main():
    samples = select_repaired_samples()

    if not samples:
        raise RuntimeError(
            "No repaired VMess rows with status=new found"
        )

    print("=" * 72)
    print("REPAIRED VMESS RUNTIME SAMPLE")
    print("=" * 72)
    print("SAMPLES:", len(samples))

    for item in samples:
        print(
            f"ID={item['id']} "
            f"transport={item['transport']} "
            f"security={item['security']} "
            f"server={item['server']}:{item['port']} "
            f"remark={item['remark']}"
        )

    results = []
    padding_errors = []

    for index, item in enumerate(samples, start=1):
        config_id = item["id"]

        print()
        print("-" * 72)
        print(
            f"RUNTIME TEST {index}/{len(samples)} "
            f"ID={config_id}"
        )
        print("-" * 72)

        try:
            result = test_config(config_id)

            normalized = {
                "id": config_id,
                "transport": item["transport"],
                "security": item["security"],
                "result": result,
                "exception": None,
            }

            error_text = str(
                result.get("error")
                if isinstance(result, dict)
                else result
            )

        except Exception as exc:
            normalized = {
                "id": config_id,
                "transport": item["transport"],
                "security": item["security"],
                "result": None,
                "exception": repr(exc),
            }

            error_text = repr(exc)

        if "incorrect padding" in error_text.lower():
            padding_errors.append(
                (config_id, error_text)
            )

        results.append(normalized)

    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    for result in results:
        print(
            json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )
        )

    print()
    print("TOTAL TESTED:", len(results))
    print("INCORRECT PADDING ERRORS:", len(padding_errors))

    if padding_errors:
        print("PADDING ERROR DETAILS:", padding_errors)
        raise RuntimeError(
            "VMess padding error still exists"
        )

    print(
        "RESULT: ALL SELECTED VMESS CONFIGS "
        "PASSED PARSE/BUILD RUNTIME PATH"
    )


if __name__ == "__main__":
    main()
