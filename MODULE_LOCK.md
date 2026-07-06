# MN Proxy Module Lock

Version: 2026-07-06

## Rules

Before adding a new module:
1. The previous related module must be classified.
2. A module can be ACTIVE, TEMPORARY, DEPRECATED, or REMOVED.
3. No two active modules should own the same responsibility.
4. After each phase, this file must be updated.

---

## Runtime Core

### main.py
Status: ACTIVE
Role: Main CLI entrypoint for update/test/summary workflows.

### auto_cycle.py
Status: ACTIVE
Role: Main automation cycle runner.
Notes: Must call safe_export_public_configs.py, not export_public_configs.py directly.

### export_public_configs.py
Status: ACTIVE_INTERNAL
Role: Raw public export generator.
Notes: Should normally be called only by safe_export_public_configs.py.

### safe_export_public_configs.py
Status: ACTIVE
Role: Safe export wrapper with candidate/last_good protection.
Computer usage: public_configs.json only.
Laptop usage: public_configs_mci.json only through MCI environment.

### check_public_export.py
Status: ACTIVE
Role: Public export validator.

---

## MCI Worker

### run_mci_cycle.py
Status: ACTIVE
Role: MCI environment wrapper. Uses configs_mci.db and public_configs_mci.json.

### run_mci_worker_console.bat
Status: ACTIVE_LOCAL
Role: Laptop scheduled runner for MCI worker.
Notes: Local runtime file. Usually not committed.

### send_mci_artifact_to_publisher.ps1
Status: ACTIVE_LOCAL
Role: Sends public_configs_mci.json candidate from laptop to central publisher.
Notes: Local runtime file. Usually not committed.

---

## Central Publisher

### publish_received_mci_artifact.py
Status: ACTIVE
Role: Computer-side MCI candidate validator and publisher.

### run_publish_received_mci.bat
Status: ACTIVE_LOCAL
Role: Computer scheduled runner for MCI publisher.
Notes: Local runtime file. Usually not committed.

### auto_publish.py
Status: ACTIVE
Role: Default/Irancell publisher for public_configs.json only.
Notes: Must not publish MCI from laptop.

---

## Smart Batch Testing

### smart_batch_plan.py
Status: TEMPORARY
Role: Preview-only prototype for smart batch selection.
Decision: Replace with smart_batch_selector.py, then remove or deprecate this file.

### smart_batch_selector.py
Status: PLANNED
Role: Final selector for preview and smart_batch_ids.json generation.

---

## Disabled / Deprecated

### run_mci_full_auto.bat
Status: DISABLED
Role: Old MCI runner.
Reason: Laptop must not publish or run old full auto path.