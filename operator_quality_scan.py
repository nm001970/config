import argparse
import time

from worker.parsers.factory import parse_config
from worker.builders.factory import build_xray_config
from worker.port_manager import get_free_port
from worker.config_writer import save_temp_config, delete_temp_config
from worker.xray_tester import start_xray
from worker.quality_checker import test_proxy_quality

from database.repositories.operator_quality_repository import (
    validate_operator,
    get_operator_candidates,
    get_config_for_operator_test,
    update_operator_result,
    get_operator_summary,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run operator-specific quality scan without changing global config quality."
    )

    parser.add_argument(
        "--operator",
        required=True,
        help="Operator name: mci, irancell, rightel, home, multi"
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Number of configs to test"
    )

    parser.add_argument(
        "--source",
        default="local_operator_scan",
        help="Test source label"
    )

    parser.add_argument(
        "--retest",
        action="store_true",
        help="Retest configs already tested for this operator"
    )

    return parser.parse_args()


def stop_process(process):
    if not process:
        return

    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def test_one_config(config_id, operator, source):
    row = get_config_for_operator_test(config_id)

    if not row:
        print(f"[SKIP] ID={config_id} not found")
        return

    process = None
    temp_path = None
    socks_port = None

    try:
        print(
            f"[OPERATOR TEST] "
            f"operator={operator} "
            f"id={row['id']} "
            f"protocol={row['protocol']}"
        )

        parsed = parse_config(row["config_text"])

        socks_port = get_free_port()

        xray_config = build_xray_config(
            parsed,
            socks_port=socks_port,
        )

        temp_path = save_temp_config(
            xray_config,
            socks_port,
        )

        print(f"[TEMP] {temp_path}")

        process, alive, error = start_xray(temp_path)

        if not alive:
            update_operator_result(
                config_id=config_id,
                operator=operator,
                alive=False,
                latency=row.get("latency"),
                error="XRAY_FAILED",
                test_source=source,
            )
            print(f"[OPERATOR DEAD] ID={config_id} XRAY_FAILED")
            return

        result = test_proxy_quality(socks_port)

        if result.get("alive"):
            saved = update_operator_result(
                config_id=config_id,
                operator=operator,
                alive=True,
                latency=row.get("latency"),
                ping_min_ms=result.get("ping_min_ms"),
                ping_avg_ms=result.get("ping_avg_ms"),
                download_mbps=result.get("download_mbps"),
                upload_mbps=result.get("upload_mbps"),
                error=None,
                test_source=source,
            )

            print(
                f"[OPERATOR DONE] "
                f"ID={config_id} "
                f"operator={operator} "
                f"status={saved['quality_status']} "
                f"score={saved['quality_score']} "
                f"ping={result.get('ping_avg_ms')} "
                f"down={result.get('download_mbps')} "
                f"up={result.get('upload_mbps')}"
            )

        else:
            error = result.get("error") or "operator quality failed"

            saved = update_operator_result(
                config_id=config_id,
                operator=operator,
                alive=False,
                latency=row.get("latency"),
                error=error,
                test_source=source,
            )

            print(
                f"[OPERATOR REJECTED] "
                f"ID={config_id} "
                f"operator={operator} "
                f"error={error}"
            )

    except Exception as e:
        update_operator_result(
            config_id=config_id,
            operator=operator,
            alive=False,
            latency=row.get("latency") if row else None,
            error=repr(e),
            test_source=source,
        )

        print(
            f"[OPERATOR ERROR] "
            f"ID={config_id} "
            f"operator={operator} "
            f"{repr(e)}"
        )

    finally:
        stop_process(process)

        if temp_path:
            try:
                delete_temp_config(temp_path)
            except Exception:
                pass

        time.sleep(0.5)


def main():
    args = parse_args()

    operator = validate_operator(args.operator)

    print("=" * 70)
    print("OPERATOR QUALITY SCAN")
    print("=" * 70)
    print(f"Operator: {operator}")
    print(f"Limit:    {args.limit}")
    print(f"Source:   {args.source}")
    print("=" * 70)

    ids = get_operator_candidates(
        operator=operator,
        limit=args.limit,
        retest=args.retest,
    )

    print(f"CANDIDATES: {ids}")

    if not ids:
        print("NO CANDIDATES")
        return

    for config_id in ids:
        test_one_config(
            config_id=config_id,
            operator=operator,
            source=args.source,
        )

    print("=" * 70)
    print("OPERATOR SUMMARY")
    print("=" * 70)
    print(get_operator_summary())


if __name__ == "__main__":
    main()