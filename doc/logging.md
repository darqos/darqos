# Logging

DarqOS components currently log diagnostic messages via the
standard _syslog_ protocol, using the `LOCAL*` facility
codes, and all standard severity levels.

The choice of using only the `LOCAL` facilities is to make
it easy to distinguish between messages from the host OS and
whatever daemons it's running in its minimalist configuration,
and the messages logged by Darq.

Facilities are allocated as follows:
- LOCAL0
  - kernel /  p-kernel
- LOCAL1
  - OS runtime support libraries
- LOCAL2
  - services
- LOCAL3
  - types
- LOCAL4
  - lenses
- LOCAL5
- LOVAL6
- LOVAL7
  - as yet unused.

It's likely that this will evolve in future to use an
IPC-based protocol, rather than the TCP-based _syslog_
standard.
