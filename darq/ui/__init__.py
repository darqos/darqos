# darqos
# Copyright (C) 2026 David Arnold

"""What lenses and tools share, of their user interface, in Qt: their
look (darq.ui.style, darq.ui.lens), dialogs, items' names, and the
launcher that creates and opens items without blocking (darq.ui.launcher).
Each is imported by name: this package itself is empty, so importing one
brings only what it needs.  The Terminal's own panels are its own, in
services/terminal."""
