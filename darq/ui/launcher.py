# darqos
# Copyright (C) 2026 David Arnold

# Creating and using items without blocking the user interface.
#
# darq.launch is synchronous: activating a type's implementation,
# creating or opening an item, and starting a lens (which waits for its
# Ready) can take a second or more.  So each launch runs on a thread of
# its own, where its p-calls can wait (each thread has its own reply
# port), and its result is posted back to the UI's thread, to the host.

import logging
import threading
import typing

import darq
import darq.history

from . import Host


# Control exports
__all__ = [
    "Launcher",
    "start_thread",
]


def start_thread(job: typing.Callable[[], None]):
    """Run a job on a daemon thread: one blocked in a p-call doesn't stop
    the process from exiting."""
    threading.Thread(target=job, name="launch", daemon=True).start()


class Launcher:
    """Creates and opens items, and starts lenses for them, off the UI's
    thread.

    The host is told how each went, on its own thread, with
    host.launched(context, item_id, error): context is whatever started
    it, and error is None if a lens is using the item."""

    def __init__(self, host: Host, source: str, post: typing.Callable[[typing.Callable[[], None]], None],
                 run: typing.Callable[[typing.Callable[[], None]], None] = start_thread):
        """Constructor.

        :param source: The History source for the created events it records.
        :param post: Calls a function on the UI's thread, from any thread
                     (such as QtEventLoop.post).
        :param run: Runs a job: on a thread, unless tests say otherwise."""
        self.host = host
        self.source = source
        self.post = post
        self.run = run

    def create(self, type_id: str, name: str, context: object = None,
               done: typing.Callable[[], None] | None = None):
        """Create an item of a type, record that it was, and use it.

        :param done: Called on the UI's thread when it's finished, before
                     the host's told, whether or not it worked."""
        self.host.show_status(f"Creating a {name} ...")
        services = self.host.services

        def job() -> tuple[str | None, str | None]:
            try:
                item_id, item = services.launch.create_item(type_id)
            except (darq.DarqError, TimeoutError) as e:
                return None, f"Can't create a {name}: {e}"
            try:
                services.history.add_event(item_id=item_id, event=darq.history.CREATED, source=self.source)
            except darq.DarqError as e:
                logging.warning(f"Can't record created for {item_id}: {e!r}")
            return item_id, self.use_item(item_id, item)

        self.start(job, context, done)

    def use(self, item_id: str, context: object = None):
        """Open an item, and use it."""
        self.host.show_status(f"Starting a lens for {item_id} ...")
        self.start(lambda: (item_id, self.use_item(item_id, None)), context)

    def use_item(self, item_id: str, item: int | None) -> str | None:
        """Start a lens for an item, on the launch's thread.  Returns what
        went wrong, if anything."""
        try:
            self.host.services.launch.use_item(item_id, item)
        except (darq.DarqError, TimeoutError) as e:
            return str(e)
        return None

    def start(self, job: typing.Callable[[], tuple[str | None, str | None]], context: object,
              done: typing.Callable[[], None] | None = None):
        def launch():
            try:
                item_id, error = job()
            except Exception as e:
                logging.exception("Launch failed")
                item_id, error = None, f"Launch failed: {e!r}"
            if self.run is start_thread:
                # Its thread's done with its calls.
                darq.release_reply_port()
            try:
                self.post(lambda: self.finish(context, item_id, error, done))
            except RuntimeError:
                # The UI's loop is closed: it's exiting.
                logging.info(f"Launch of {item_id} finished after the UI: {error or 'OK'}")

        self.run(launch)

    def finish(self, context: object, item_id: str | None, error: str | None,
               done: typing.Callable[[], None] | None):
        """Tell the host, on its thread.  Nothing's raised into its event
        loop: PyQt would abort the process."""
        try:
            if done is not None:
                done()
            self.host.launched(context, item_id, error)
        except Exception:
            logging.exception("Failed to report a launch")
