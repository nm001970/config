import subprocess
import time


def start_xray(config_path):

    process = subprocess.Popen(
        [
            "tools/xray/xray.exe",
            "run",
            "-config",
            config_path
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    time.sleep(0.5)

    alive = process.poll() is None

    error = None

    if not alive:
        try:
            stdout, stderr = process.communicate(timeout=1)
            error = stderr or stdout
        except Exception as e:
            error = str(e)

    return process, alive, error