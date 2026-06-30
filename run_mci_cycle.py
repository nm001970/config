import argparse
import os
import subprocess
import sys
import urllib.request
from datetime import datetime


DEFAULT_MCI_DB_URL = "sqlite:///configs_mci.db"
DEFAULT_MCI_EXPORT_FILE = "public_configs_mci.json"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run the full MN Proxy pipeline for MCI using a separate DB and public export file."
    )

    parser.add_argument(
        "--db",
        default=DEFAULT_MCI_DB_URL,
        help=f"Config database URL. Default: {DEFAULT_MCI_DB_URL}",
    )

    parser.add_argument(
        "--output",
        default=DEFAULT_MCI_EXPORT_FILE,
        help=f"Public configs output file. Default: {DEFAULT_MCI_EXPORT_FILE}",
    )

    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run without interactive confirmation.",
    )

    parser.add_argument(
        "--skip-ip-check",
        action="store_true",
        help="Skip direct internet IP check.",
    )

    return parser.parse_args()


def get_direct_ip():
    try:
        with urllib.request.urlopen(
            "https://api.ipify.org?format=json",
            timeout=10,
        ) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return f"IP_CHECK_FAILED: {repr(exc)}"


def run_step(title, command, env):
    print()
    print("=" * 70)
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | START: {title}")
    print("COMMAND:", " ".join(command))
    print("=" * 70)
    print()

    result = subprocess.run(
        command,
        env=env,
    )

    print()
    print("-" * 70)
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | END: {title} | EXIT CODE: {result.returncode}")
    print("-" * 70)

    if result.returncode != 0:
        raise SystemExit(result.returncode)


def build_env(args):
    env = os.environ.copy()

    env["CONFIG_DATABASE_URL"] = args.db
    env["PUBLIC_CONFIGS_FILE"] = args.output

    return env


def main():
    args = parse_args()
    env = build_env(args)

    print("=" * 70)
    print("MCI PIPELINE RUNNER")
    print("=" * 70)
    print(f"CONFIG_DATABASE_URL: {env['CONFIG_DATABASE_URL']}")
    print(f"PUBLIC_CONFIGS_FILE: {env['PUBLIC_CONFIGS_FILE']}")
    print("=" * 70)

    if not args.skip_ip_check:
        print()
        print("DIRECT INTERNET IP CHECK:")
        print(get_direct_ip())

    if not args.yes:
        print()
        print("Before continuing, make sure:")
        print("1) MCI internet is connected.")
        print("2) Other internet routes are disabled if needed.")
        print("3) Direct IP belongs to the MCI connection.")
        print()
        input("Press Enter to start the MCI full pipeline... ")

    run_step(
        "Initialize MCI database tables",
        [
            sys.executable,
            "-u",
            "-c",
            (
                "from database.db import Base, engine; "
                "import database.models; "
                "Base.metadata.create_all(bind=engine); "
                "print('MCI DB initialized')"
            ),
        ],
        env,
    )

    run_step(
        "Run full MCI auto cycle",
        [
            sys.executable,
            "-u",
            "auto_cycle.py",
        ],
        env,
    )

    run_step(
        "Final MCI public export check",
        [
            sys.executable,
            "-u",
            "check_public_export.py",
        ],
        env,
    )

    print()
    print("=" * 70)
    print("MCI PIPELINE FINISHED SUCCESSFULLY")
    print("=" * 70)
    print(f"DB:     {args.db}")
    print(f"EXPORT: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()