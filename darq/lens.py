# DarqOS
# Copyright (C) 2022 David Arnold

# A Lens implementation provides a standard API via the system IPC
# mechanism to enable it to be managed by the Terminal Service.  This
# means it must service both the UI and the network at runtime.


class Lens:
    """Base class for Lens implementations."""

    def __init__(self):
        """Constructor."""
        pass

    @staticmethod
    def register():
        """Register this Lens.

        Registration records take the form:
        - type
        - action
        - context

        Registration is persistent: it is run once when the Lens is
        installed or updated, but need not be run on boot."""
        pass

    @staticmethod
    def deregister():
        """Deregister this Lens.

        Remove all registration record for this Lens as part of
        uninstalling this application."""
        pass

    def initialise(self):
        """Initialise."""
        pass

    def finalise(self):
        """Finalisation."""
        pass

    def close(self, lens_id: str):
        """Close the specified Lens instance.

        :param lens_id: Specific Lens to close."""
        pass

    def shutdown(self):
        """Shutdown the Lens implementation process.

        All active views should be closed.  If the Lens is implemented
        using multiple processes, all of them should be killed."""
        pass

    def ping(self):
        """Health check."""
        pass

    def perform_action(self, context, action: str, oid: str):
        """Perform an action, on the specified object, within a context.

        :param context: Context for the action.
        :param action: Specific action to perform.
        :param oid: Object on which to perform the action.

        Example actions are: view (read-only), edit (read-write), print.
        """
        pass

