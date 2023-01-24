# darqos
# Copyright (C) 2023, David Arnold

# Note that with systemd's journald, UDP to port 514 doesn't work, because
# it doesn't even listen there.
#
# priority: int = (facility.value * 8) + level.value
# now = time.time()
# timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.", time.gmtime(now))
# timestamp += "%06uZ" % int((now % 1) * 1e6)
# hostname = socket.gethostname()
# appname = os.path.basename(sys.argv[0])
# procid = os.getpid()
# s = f"<{priority}> 1 {timestamp} {hostname} {appname} {procid} {message}"
# sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# sock.sendto(s.encode(), ("127.0.0.1", 514))


import enum
import syslog


class Facility(enum.IntEnum):
    """Logging facilities."""

    # Kernel / p-Kernel
    KERN = 16     # LOCAL0

    # System runtime libraries.
    LIB = 17      # LOCAL1

    # Services.
    SERVICE = 18  # LOCAL2

    # Type implementations.
    TYPE = 19     # LOCAL3

    # Lenses.
    LENS = 20     # LOCAL4

    # LOCAL5
    # LOCAL6

    # System scripts, etc.
    SYS = 23      # LOCAL7


class Level(enum.IntEnum):
    """Logging severity levels."""

    # System is unusable.
    EMERG = 0

    # Action must be taken immediately.
    ALERT = 1

    # System has a problem that cannot be resolved without intervention.
    CRIT = 2
    CRITICAL = 2

    # System has experienced a failure, but remains functioning.
    ERR = 3
    ERROR = 3

    # An anomalous condition was detected, but system is functioning.
    WARN = 4
    WARNING = 4

    # A normal but significant condition was detected.
    NOTICE = 5

    # Informational message.
    INFO = 6

    # Debugging information only.
    DEBUG = 7


def log(facility: Facility, level: Level, message: str):
    """Log a message.

    :param facility: Source of the message.
    :param level: Severity of the message.
    :param message: Text of the message."""

    syslog.syslog(facility.value * 8 + level.value, message)
    return


if __name__ == "__main__":
    log(Facility.SYS, Level.EMERG, "Test log message")
