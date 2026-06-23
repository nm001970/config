import subprocess

def get_xray_version():
    result= subprocess.run(
        [
            "tools/xray/xray.exe",
            "version"
        ],
        capture_output=True,
        text=True

    )
    return result.stdout