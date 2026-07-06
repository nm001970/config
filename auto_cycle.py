import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"

# اگر خواستی audit_scoring هم آخر چرخه اجرا شود، در .env بگذار:
# AUTO_CYCLE_RUN_AUDIT=true
RUN_AUDIT = os.getenv("AUTO_CYCLE_RUN_AUDIT", "false").lower() == "true"


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_log_file():
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"auto_cycle_{stamp}.log"


def write_log(log_file, text):
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(text)


def run_step(title, command, log_file):
    separator = "\n" + "=" * 70 + "\n"
    header = (
        separator
        + f"{now_text()} | START: {title}\n"
        + f"COMMAND: {' '.join(command)}\n"
        + "=" * 70
        + "\n"
    )

    print(header)
    write_log(log_file, header)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        bufsize=1,
    )

    assert process.stdout is not None

    for line in process.stdout:
        print(line, end="")
        write_log(log_file, line)

    return_code = process.wait()

    footer = (
        "\n"
        + "-" * 70
        + "\n"
        + f"{now_text()} | END: {title} | EXIT CODE: {return_code}\n"
        + "-" * 70
        + "\n"
    )

    print(footer)
    write_log(log_file, footer)

    if return_code != 0:
        raise RuntimeError(f"Step failed: {title} | exit code={return_code}")


def require_file(path):
    full_path = PROJECT_ROOT / path

    if not full_path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")


def main():
    log_file = make_log_file()

    print("=" * 70)
    print("AUTO CYCLE STARTED")
    print("PROJECT:", PROJECT_ROOT)
    print("LOG FILE:", log_file)
    print("=" * 70)

    write_log(
        log_file,
        "=" * 70
        + "\n"
        + f"{now_text()} | AUTO CYCLE STARTED\n"
        + f"PROJECT: {PROJECT_ROOT}\n"
        + f"LOG FILE: {log_file}\n"
        + "=" * 70
        + "\n"
    )

    required_files = [
        "main.py",
        "export_public_configs.py",
        "check_public_export.py",
        "public_configs.json",
    ]

    if RUN_AUDIT:
        required_files.append("audit_scoring.py")

    for file_path in required_files:
        require_file(file_path)

    python = sys.executable

    steps = [
        (
            "Update configs",
            [python, "-u", "main.py", "update"],
        ),
        (
            "Full database test cycle",
            [python, "-u", "main.py", "fullcycle"],
        ),
        (
            "Export public configs",
            [python, "-u", "safe_export_public_configs.py"],
        ),
        (
            "Check public export",
            [python, "-u", "check_public_export.py"],
        ),
    ]

    if RUN_AUDIT:
        steps.append(
            (
                "Audit scoring",
                [python, "-u", "audit_scoring.py"],
            )
        )

    for title, command in steps:
        run_step(title, command, log_file)

    success_text = (
        "\n"
        + "=" * 70
        + "\n"
        + f"{now_text()} | AUTO CYCLE FINISHED SUCCESSFULLY\n"
        + "=" * 70
        + "\n"
    )

    print(success_text)
    write_log(log_file, success_text)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\nAUTO CYCLE FAILED")
        print("ERROR:", repr(e))
        raise