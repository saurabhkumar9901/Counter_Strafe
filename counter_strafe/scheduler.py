"""Tap key-up timer.

The low-level hook has a hard deadline (Windows silently removes hooks that
block for ~300 ms, and every millisecond spent in the callback is added to
every keystroke the user makes). So owed key-ups are fired from this thread,
never from the hook.
"""

import threading
import time
from typing import TYPE_CHECKING, Callable, Sequence

from .engine import Event, Injection

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine


class TapScheduler:
    def __init__(
        self,
        engine: "Engine",
        inject: Callable[[Sequence[Injection]], None],
        publish: Callable[[Sequence[Event]], None],
    ) -> None:
        self._engine = engine
        self._inject = inject
        self._publish = publish
        self._cond = threading.Condition()
        self._stop = False
        self._thread = None  # type: threading.Thread | None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tap-scheduler", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        """Ask the scheduler to re-check its deadline (after any hook event)."""
        with self._cond:
            self._cond.notify_all()

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while True:
            with self._cond:
                if self._stop:
                    return
                deadline = self._engine.next_deadline
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - time.monotonic())
                self._cond.wait(timeout)
            if self._stop:
                return
            self._tick()

    def _tick(self) -> None:
        injections = self._engine.due(time.monotonic())
        if injections:
            self._inject(injections)
        events = self._engine.drain_events()
        if events:
            self._publish(events)
