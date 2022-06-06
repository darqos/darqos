#

import os
import signal
import sys
import time


BOOT_ERR = f"""Error: unable to find DarqOS system files in %s.
You must either start from the DarqOS installation directory, or set 
the DARQ_ROOT environment variable to point to it.
"""


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
        #self.storage_pid = self._start_service("Storage", "storage/main.py", root)
        #self.type_id = self._start_service("Type", "type/main.py", root)
        #self.history_pid = self._start_service("History", "history/main.py", root)

        # index-service
        # metadata-service
        # security-service

        self.terminal_pid = self._start_service("Terminal", "terminal/main.py", root)

        print("System up.")
        return

    def _start_service(self, name, file_name, root):
        """(Internal) Start a service process."""

        args = ["python", f"{root}/services/{file_name}"]
        pid = os.spawnvp(os.P_NOWAIT, "python", args)
        print(f"Started {name} from {args[1]} ... pid {pid}")
        return pid

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
        os.kill(self.terminal_pid, signal.SIGTERM)
        # security
        # metadata
        # index
        # history
        # type
        #os.kill(self.storage_pid, signal.SIGTERM)

        print("System halted.")
        return


if __name__ == "__main__":
    bootstrap = Bootstrap()
    bootstrap.boot()
    bootstrap.wait()
    bootstrap.shutdown()


