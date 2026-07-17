import os
import time
from contextlib import contextmanager
from pathlib import Path
from functools import wraps

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / ".runtime"


def normalize_operator(operator):
    value = str(operator or "").strip().lower()

    if not value:
        raise ValueError("operator is required")

    return value


@contextmanager
def pool_maintenance_lock(
    operator,
    poll_seconds=1.0,
):
    """
    Serialize Reserve Health and Hot Manager operations for one
    operator.

    The lock waits until the other pool-maintenance process finishes.
    The operating system releases it automatically if the process
    crashes.
    """
    operator = normalize_operator(operator)
    poll_seconds = max(float(poll_seconds), 0.1)

    RUNTIME_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    lock_path = (
        RUNTIME_DIR
        / f"pool_maintenance_{operator}.lock"
    )

    lock_file = open(
        lock_path,
        "a+b",
    )

    lock_acquired = False
    backend = None

    try:
        lock_file.seek(0, os.SEEK_END)

        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()

        lock_file.seek(0)

        if os.name == "nt":
            import msvcrt

            backend = "windows"
            waiting_logged = False

            while True:
                try:
                    msvcrt.locking(
                        lock_file.fileno(),
                        msvcrt.LK_NBLCK,
                        1,
                    )
                    break

                except OSError:
                    if not waiting_logged:
                        print(
                            "[POOL MAINTENANCE LOCK WAITING] "
                            f"OPERATOR={operator} "
                            f"PATH={lock_path}"
                        )
                        waiting_logged = True

                    time.sleep(poll_seconds)

        else:
            import fcntl

            backend = "posix"

            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX,
            )

        lock_acquired = True
        yield lock_path

    finally:
        if lock_acquired:
            try:
                lock_file.seek(0)

                if backend == "windows":
                    import msvcrt

                    msvcrt.locking(
                        lock_file.fileno(),
                        msvcrt.LK_UNLCK,
                        1,
                    )

                elif backend == "posix":
                    import fcntl

                    fcntl.flock(
                        lock_file.fileno(),
                        fcntl.LOCK_UN,
                    )

            except OSError:
                pass

        lock_file.close()

def pool_maintenance_serialized(function):
    """
    Serialize a pool-maintenance function by operator.

    The decorated function must receive operator either as its first
    positional argument or as the keyword argument "operator".
    """

    @wraps(function)
    def wrapper(*args, **kwargs):
        operator = kwargs.get("operator")

        if operator is None and args:
            operator = args[0]

        operator = normalize_operator(operator)

        with pool_maintenance_lock(
            operator
        ) as lock_path:
            print(
                "POOL MAINTENANCE LOCK ACQUIRED:",
                lock_path,
            )

            return function(
                *args,
                **kwargs,
            )

    return wrapper