"""A background lane for work that must not run inside a kanban hook.

``kanban_task_claimed`` fires inside the dispatcher's board lock and every hook callback has a
timeout, so a hook only hands a job to this thread and returns. The thread starts on first use,
never in ``register()``.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("github_issues_sync")


class Runtime:
    def __init__(self, name: str = "github-issues-sync", log: logging.Logger = logger):
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._name = name
        self._log = log

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self._queue.put((fn, args, kwargs))
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
                self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                fn, args, kwargs = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                fn(*args, **kwargs)
            except Exception:  # one failed job must not take the lane down
                self._log.exception("github-issues-sync: background job failed")
            finally:
                self._queue.task_done()

    def drain(self, timeout: float = 30) -> bool:
        """Wait for queued jobs (tests and shutdown)."""
        deadline = time.monotonic() + timeout
        while not self._queue.empty() or self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.02)
        return True

    def shutdown(self, timeout: float = 10) -> None:
        self.drain(timeout)
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)


class Throttle:
    """At most one ``allow()`` per interval. The interval is read each time, so a config edit
    applies without a restart."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._last: float | None = None
        self._lock = threading.Lock()

    def allow(self, interval_seconds: float) -> bool:
        now = self._clock()
        with self._lock:
            if self._last is not None and now - self._last < interval_seconds:
                return False
            self._last = now
            return True
