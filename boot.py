#! /usr/bin/env python3
################################################################
# darqos
# Copyright (C) 2022 David Arnold
################################################################

import os
import signal
import sys
import time

from dataclasses import dataclass


BOOT_ERR = f"""Error: unable to find DarqOS system files in %s.
You must either start from the DarqOS installation directory, or set 
the DARQ_ROOT environment variable to point to it.
"""

@dataclass
class ServiceInfo:
    name: str = ''
    filename: str = ''
    order: int = 0
    pid: int = 0


SERVICES = [
    #ServiceInfo("Storage", "storage/main.py", 100, 0),
    #ServiceInfo("Type", "type/main.py", 200, 0),

    # index-service
    # metadata-service
    # security-service

    ServiceInfo("Terminal", "terminal/main.py", 900, 0),
]


class Bootstrap:
    def boot(self):
        """Boot the M0 interim implementation."""

        print("Booting ...")

        # Look for the system files.
        root = os.getenv("DARQ_ROOT")
        if root is None:
            root = os.getcwd()

        if not os.path.exists(f"{root}/darq/rt/type.py"):
            sys.stderr.write(BOOT_ERR % root)
            sys.exit(1)

        print(f"Using system root: {root}")

        # Make sure system root is first in Python's import path.
        python_path = os.getenv("PYTHONPATH")
        if python_path is None:
            os.environ["PYTHONPATH"] = root
        else:
            os.environ["PYTHONPATH"] = f"{root}:{os.getenv('PYTHONPATH')}"

        # Start services.
        # FIXME: no dependencies for now: just use fixed order.
        for service_info in SERVICES:
            self._start_service(service_info, root)

        print("System up.")
        return

    def _start_service(self, service_info: ServiceInfo, root: str):
        """(Internal) Start a service process."""

        args = ["python", f"{root}/services/{service_info.filename}"]
        pid = os.spawnvp(os.P_NOWAIT, "python", args)
        print(f"Started {service_info.name} from {args[1]} ... pid {pid}")
        service_info.pid = pid

    def wait(self):
        try:
            while True:
                time.sleep(1)

        except KeyboardInterrupt:
            pass

    def shutdown(self):
        """Perform an orderly shutdown of the M0 implementation."""

        print("Shutting down ...")

        # reverse order shutdown of services.
        shutdown = SERVICES[:]
        shutdown.reverse()
        for service_info in shutdown:
            os.kill(service_info.pid, signal.SIGINT)
            print(f"Requested {service_info.name} Service shut down.")

        time.sleep(1)

        for service_info in shutdown:
            os.kill(service_info.pid, signal.SIGTERM)
            print(f"Forced {service_info.name} Service to shut down.")

        print("System halted.")
        return


if __name__ == "__main__":
    bootstrap = Bootstrap()
    bootstrap.boot()
    bootstrap.wait()
    bootstrap.shutdown()


