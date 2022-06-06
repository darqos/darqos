#

import os
import sys

BOOT_ERR = f"""Error: unable to find DarqOS system files in %s.
You must either start from the DarqOS installation directory, or set 
the DARQ_ROOT environment variable to point to it.
"""


def boot():
    """Boot the M0 interim implementation."""

    # Look for the system files.
    root = os.getenv("DARQ_ROOT")
    if root is None:
        root = os.getcwd()

    if not os.path.exists(f"{root}/darq/rt/type.py"):
        sys.stderr.write(BOOT_ERR % root)
        sys.exit(1)

    # Make sure system root is first in Python's import path.
    python_path = os.getenv("PYTHONPATH")
    if python_path is None:
        os.environ["PYTHONPATH"] = root
    else:
        os.environ["PYTHONPATH"] = f"{root}:{os.getenv('PYTHONPATH')}"

    # FIXME: no dependencies for now: just use fixed order.

    storage_pid = os.spawnv(os.P_NOWAIT, "python", [f"{root}/services/storage/main.py"])
    terminal_pid = os.spawnv(os.P_NOWAIT, "python", [f"{root}/services/terminal/main.py"])

    return


def shutdown():
    """Perform an orderly shutdown of the M0 implementation."""

    # reverse order shutdown of services.
    return
