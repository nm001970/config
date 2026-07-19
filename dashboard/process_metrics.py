from __future__ import annotations

import json
import os
import subprocess
from typing import Any


WORKER_SPECS = [
    {
        "key": "raw_to_reserve",
        "label": "Raw to Reserve",
        "runner": "run_v2_raw_to_reserve_irancell.bat",
    },
    {
        "key": "reserve_health",
        "label": "Reserve Health",
        "runner": "run_v2_reserve_health_irancell.bat",
    },
    {
        "key": "hot_manager",
        "label": "Hot Manager",
        "runner": "run_v2_hot_manager_irancell.bat",
    },
    {
        "key": "config_update",
        "label": "Config Update",
        "runner": "run_config_update_irancell.bat",
    },
    {
        "key": "export_publish",
        "label": "Export and Publish",
        "runner": "run_export_publish_irancell.bat",
    },
    {
        "key": "mci_publisher",
        "label": "MCI Central Publisher",
        "runner": "run_publish_received_mci_loop.bat",
    },
]


LEGACY_PATTERNS = [
    "auto_cycle.py",
    "auto_publish.py",
    "v2_pool_supervisor.py",
    "run_irancell_worker_console.bat",
]


def _empty_summary() -> dict[str, Any]:
    return {
        "supported": os.name == "nt",
        "query_ok": False,
        "workers": [],
        "worker_count": len(WORKER_SPECS),
        "running_count": 0,
        "stopped_count": 0,
        "duplicate_count": 0,
        "all_running": False,
        "legacy_processes": [],
        "error": None,
    }


def _read_windows_processes() -> list[dict[str, Any]]:
    command = (
        "[Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new($false);"
        "$OutputEncoding = "
        "[Console]::OutputEncoding;"
        "$items = @("
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId, Name, CommandLine"
        ");"
        "$items | ConvertTo-Json -Compress -Depth 3"
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
        check=False,
    )

    if completed.returncode != 0:
        error_text = completed.stderr.strip()

        raise RuntimeError(
            error_text
            or (
                "PowerShell process query failed with "
                f"exit code {completed.returncode}."
            )
        )

    output = completed.stdout.strip()

    if not output:
        return []

    payload = json.loads(output)

    if isinstance(payload, dict):
        payload = [payload]

    if not isinstance(payload, list):
        raise ValueError(
            "PowerShell process response must be a list."
        )

    processes = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        command_line = item.get("CommandLine")
        process_name = item.get("Name")
        process_id = item.get("ProcessId")

        if not isinstance(command_line, str):
            continue

        try:
            normalized_process_id = int(process_id)
        except (TypeError, ValueError):
            continue

        processes.append(
            {
                "process_id": normalized_process_id,
                "name": str(process_name or ""),
                "command_line": command_line,
            }
        )

    return processes


def _build_worker_rows(
    processes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []

    for worker_spec in WORKER_SPECS:
        runner = worker_spec["runner"].lower()

        matches = [
            process
            for process in processes
            if process["name"].lower() == "cmd.exe"
            and runner
            in process["command_line"].lower()
        ]

        process_ids = sorted(
            process["process_id"]
            for process in matches
        )

        if len(process_ids) == 0:
            status = "Stopped"
        elif len(process_ids) == 1:
            status = "Running"
        else:
            status = "Duplicate"

        rows.append(
            {
                "key": worker_spec["key"],
                "label": worker_spec["label"],
                "runner": worker_spec["runner"],
                "status": status,
                "process_count": len(process_ids),
                "process_ids": process_ids,
            }
        )

    return rows


def _find_legacy_processes(
    processes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches = []

    for process in processes:
        command_line = process[
            "command_line"
        ].lower()

        matched_patterns = [
            pattern
            for pattern in LEGACY_PATTERNS
            if pattern.lower() in command_line
        ]

        if not matched_patterns:
            continue

        matches.append(
            {
                "process_id": process["process_id"],
                "name": process["name"],
                "matched_patterns": matched_patterns,
            }
        )

    return matches


def read_process_summary() -> dict[str, Any]:
    summary = _empty_summary()

    if not summary["supported"]:
        summary["error"] = (
            "Process monitoring is supported only "
            "on Windows."
        )
        return summary

    try:
        processes = _read_windows_processes()

        worker_rows = _build_worker_rows(
            processes
        )

        running_count = sum(
            worker["status"] == "Running"
            for worker in worker_rows
        )

        stopped_count = sum(
            worker["status"] == "Stopped"
            for worker in worker_rows
        )

        duplicate_count = sum(
            worker["status"] == "Duplicate"
            for worker in worker_rows
        )

        summary.update(
            {
                "query_ok": True,
                "workers": worker_rows,
                "running_count": running_count,
                "stopped_count": stopped_count,
                "duplicate_count": duplicate_count,
                "all_running": (
                    running_count
                    == len(WORKER_SPECS)
                    and duplicate_count == 0
                ),
                "legacy_processes": (
                    _find_legacy_processes(
                        processes
                    )
                ),
                "error": None,
            }
        )

    except (
        json.JSONDecodeError,
        OSError,
        subprocess.SubprocessError,
        RuntimeError,
        ValueError,
    ) as exc:
        summary["error"] = str(exc)

    return summary