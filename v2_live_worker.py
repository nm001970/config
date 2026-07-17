from database.repositories.config_repository import (
    get_config_by_id,
)
from database.repositories.pool_repository import (
    mark_gate_testing,
    upsert_gate_result,
)
from delivery_validate_public_configs import (
    validate_config_text,
)
from quality_worker import test_quality
from worker.test_worker import test_config


def normalize_operator(operator):
    value = str(operator or "").strip().lower()

    if not value:
        raise ValueError("operator is required")

    return value


def normalize_config_id(config_id):
    value = int(config_id)

    if value <= 0:
        raise ValueError(
            "config_id must be greater than zero"
        )

    return value


def _quality_rejection_code(result):
    stage = str(
        (result or {}).get("failed_stage") or ""
    ).strip().lower()

    if stage == "ping":
        return "quality_ping_rejected"

    if stage == "download":
        return "quality_download_rejected"

    if stage == "upload":
        return "quality_upload_rejected"

    if stage == "xray":
        return "quality_xray_rejected"

    if stage == "exception":
        return "quality_error"

    return "quality_rejected"


def _quality_error_text(result):
    result = dict(result or {})

    return (
        result.get("quality_reject_reason")
        or result.get("error")
        or result.get("last_error")
        or "quality_check_failed"
    )


def _get_config_or_none(config_id):
    return get_config_by_id(
        int(config_id)
    )


def process_config(
    config_id,
    operator,
    direct_ip=None,
):
    """
    Run one strict Raw -> Reserve pipeline.

    Required external ownership:
        - config_id has already been selected by the Smart planner.
        - config_id has already been claimed by
          claim_config_ids_for_raw_to_reserve().

    Stages:
        1. Connectivity
        2. Quality: Ping -> Download -> optional Upload
        3. Reserve Admission Gate

    Returns a stable result code for cycle-level aggregation.

    Unexpected infrastructure or persistence exceptions are allowed to
    propagate. v2_raw_to_reserve_cycle.py then safely releases unfinished
    Raw and Gate claims.
    """
    config_id = normalize_config_id(
        config_id
    )
    operator = normalize_operator(
        operator
    )

    print()
    print("=" * 70)
    print("RAW TO RESERVE PIPELINE START")
    print("CONFIG ID:", config_id)
    print("OPERATOR:", operator)
    print("=" * 70)

    # --------------------------------------------------------------
    # Stage 1: Connectivity
    # --------------------------------------------------------------
    print()
    print("[STAGE 1/3] CONNECTIVITY")

    test_config(config_id)

    row = _get_config_or_none(
        config_id
    )

    if row is None:
        print(
            "[PIPELINE ERROR] "
            f"ID={config_id} "
            "CONFIG_NOT_FOUND_AFTER_CONNECTIVITY"
        )
        return "config_not_found"

    if row.status != "alive":
        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=connectivity "
            f"STATUS={row.status} "
            f"ERROR={row.last_error}"
        )
        return "connectivity_rejected"

    print(
        "[STAGE PASSED] "
        f"ID={config_id} "
        "STAGE=connectivity "
        f"LATENCY={row.latency} "
        f"EXIT_IP={row.exit_ip}"
    )

    # --------------------------------------------------------------
    # Stage 2: Quality
    # --------------------------------------------------------------
    print()
    print("[STAGE 2/3] QUALITY")

    quality_result = test_quality(
        config_id
    )

    if quality_result is None:
        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=quality "
            "ERROR=quality_result_missing"
        )
        return "quality_error"

    try:
        quality_result = dict(
            quality_result
        )

    except (TypeError, ValueError) as exception:
        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=quality "
            "ERROR=invalid_quality_result "
            f"DETAIL={repr(exception)}"
        )
        return "quality_error"

    if not quality_result.get("alive"):
        rejection_code = (
            _quality_rejection_code(
                quality_result
            )
        )
        error_text = _quality_error_text(
            quality_result
        )

        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=quality "
            f"FAILED_STAGE="
            f"{quality_result.get('failed_stage')} "
            f"ERROR={error_text}"
        )

        return rejection_code

    row = _get_config_or_none(
        config_id
    )

    if row is None:
        print(
            "[PIPELINE ERROR] "
            f"ID={config_id} "
            "CONFIG_NOT_FOUND_AFTER_QUALITY"
        )
        return "config_not_found"

    if row.quality_status != "qualified":
        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=quality_persistence "
            f"STATUS={row.quality_status} "
            f"ERROR={row.quality_error}"
        )
        return "quality_persistence_error"

    print(
        "[STAGE PASSED] "
        f"ID={config_id} "
        "STAGE=quality "
        f"SCORE={row.quality_score} "
        f"PING={row.ping_avg_ms} "
        f"DOWN={row.download_mbps} "
        f"UP={row.upload_mbps} "
        f"UPLOAD_VERIFIED="
        f"{quality_result.get('upload_verified')}"
    )

    # --------------------------------------------------------------
    # Stage 3: Reserve Admission Gate
    # --------------------------------------------------------------
    print()
    print(
        "[STAGE 3/3] "
        "RESERVE ADMISSION GATE"
    )

    mark_gate_testing(
        config_id=config_id,
        operator=operator,
    )

    try:
        gate_result = (
            validate_config_text(
                row.config_text,
                direct_ip=direct_ip,
            )
        )
        gate_result = dict(
            gate_result or {}
        )

    except Exception as exception:
        gate_result = {
            "ok": False,
            "error": (
                "gate_exception:"
                f"{repr(exception)}"
            ),
            "exit_ip": None,
            "download_mbps": None,
        }

    if "ok" not in gate_result:
        gate_result["ok"] = False
        gate_result["error"] = (
            gate_result.get("error")
            or "gate_result_missing_ok"
        )

    pool_item = upsert_gate_result(
        config_id=config_id,
        operator=operator,
        result=gate_result,
    )

    if not isinstance(pool_item, dict):
        raise RuntimeError(
            "upsert_gate_result returned "
            "an invalid pool item"
        )

    if not gate_result.get("ok"):
        print(
            "[PIPELINE STOP] "
            f"ID={config_id} "
            "STAGE=reserve_admission "
            f"ERROR={gate_result.get('error')}"
        )
        return "gate_rejected"

    if (
        pool_item.get("pool_status")
        != "reserve"
        or pool_item.get("gate_status")
        != "accepted"
    ):
        raise RuntimeError(
            "Gate accepted but Reserve state "
            "was not persisted correctly: "
            f"{pool_item}"
        )

    print(
        "[RESERVE ADDED] "
        f"ID={config_id} "
        f"EXIT_IP="
        f"{pool_item.get('gate_exit_ip')} "
        f"DOWNLOAD="
        f"{pool_item.get('gate_download_mbps')}"
    )

    return "reserve_added"


__all__ = [
    "process_config",
]