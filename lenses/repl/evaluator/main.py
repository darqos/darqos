# darqos
# Copyright (C) 2026 David Arnold

# The REPL's evaluator: the process that holds a session's namespace, and
# runs its cells, on its main thread, as a program's code runs, so what's
# written in the REPL runs as a program does: its own QApplication, event
# loops, and windows.  The REPL (lenses/repl) starts it, as its child,
# and asks it, over its protocol (lib/didl/evaluator.didl), on another
# thread.  It's darq.repl's: see Service, and doc/repl.rst.

import darq.repl


def darq_main():
    return darq.repl.evaluator_main()
