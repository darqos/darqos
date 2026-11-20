Shutdown
========

Shutting down, or rebooting, is the p-kernel's to coordinate: it asks
each process to finish, rather than killing it, so a program can save
what it must, or ask the operator, first.  A process that does nothing
about it finishes at once, as it always has.

The exchange
------------

These are p-calls, between the p-kernel and ``pyloader``
(``darq._runtime.pcall``):

``ShutdownRequest(reason, wait_limit)``
  From the p-kernel: the system's shutting down, or rebooting
  (``ShutdownReason``), and the seconds a process may wait.

``ShutdownStatus(state, note)``
  The process's answer: ``OK``, and it exits; or ``WAIT``, with a note
  of what it's doing, as "asking whether to save the session", said
  again every few seconds till it's ``OK``.

``ShutdownNow(grace)``
  From the p-kernel: it's killed in ``grace`` seconds.  It should save
  what it can, at once, say ``OK``, and exit.

A process that's silent for two intervals (10 seconds), or has waited 60
seconds, is told ``ShutdownNow``; one that's had its grace (3 seconds),
after that or after saying ``OK``, has its host process killed.  A
process that's asked, and ends, is ``KILLED``, as by ``kill_process``,
unless it reports its exit.  Nothing new is spawned once a shutdown's
begun.

The order
---------

Services outlive what uses them:

1. Every process that isn't a service is asked at once: lenses, tools,
   the REPL and its evaluator, programs run from items, and type
   implementations.  Each waits for what it needs itself: the REPL's
   evaluator waits for its REPL, which saves its session with it.
2. Once they've all finished, the services, one at a time, in reverse of
   the order they started: the Terminal first, which started last, so
   it's there for a lens's dialog till then; then the boot services, in
   reverse of ``BOOT_PROCESSES``, which lists them in the order they
   depend on each other, so Storage stops last.

In a program
------------

``pyloader`` speaks the exchange: a program needn't.  Without a handler,
it's ``OK`` at once, and the program ends, as it would be killed.  A
program that needs time registers one, on its own event loop::

    def finish(reboot: bool, now: bool) -> darq.ShutdownLater | None:
        if now:
            save_quickly()
            return None
        later = darq.ShutdownLater("saving")
        start_saving(then=later.done)
        return later

    darq.on_shutdown(finish, loop)

It returns ``None`` when it's finished, or a ``darq.ShutdownLater``,
whose ``done()`` it calls when it is, from any thread, and whose
``note()`` says what it's doing meanwhile: ``pyloader`` says ``WAIT``
with it every 4 seconds.  Told ``ShutdownNow``, it's called again, with
``now`` true, and the program then ends.

What it's waiting for
---------------------

A system process (one the p-kernel spawned) may ask what a shutdown's
waiting for, with ``darq.shutdown_state()``: whether one's running, and
each process asked and not yet gone, with its note; and hurry it, with
``darq.shutdown_now()``, which tells them all ``ShutdownNow`` at once.

The Terminal does both: shutting down, or rebooting, from Sys-b, it
shows what's being waited for, and why, each second, and *n* shuts down
now.
