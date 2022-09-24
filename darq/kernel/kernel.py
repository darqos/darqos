#


class Kernel:
    """Kernel API wrapper for Python."""

    def __init__(self):
        return


    def register_process(self, pid):
        """Register an externally-created process.

        For now, all darq processes are actually Unix processes, and in
        some cases, they're created outside of darq's 'system calls'.
        In order to enable darq to manage these processes, they need to
        be registered, using this function."""

        # FIXME!
        pass


