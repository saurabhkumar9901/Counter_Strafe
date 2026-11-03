"""Tap key-up timer.

The low-level hook has a hard deadline (Windows silently removes hooks that
block for ~300 ms, and every millisecond spent in the callback is added to
every keystroke the user makes). So owed key-ups are fired from this thread,
never from the hook.

`time.perf_counter()` is the clock everywhere in this program: on Windows,
`time.monotonic()` only advances on the ~15.6 ms system tick, which quantised
every tap to a different length (a 50 ms tap held the key for 50-75 ms).
"""

import threading
import time
from typing import TYPE_CHECKING, Callable, Optional, Sequence

from .engine import Event, Injection

if TYPE_CHECKING:  # pragma: no cover
    from .engine import Engine


class _CoarseWaiter:
    """Fallback waiter built on threading.Condition.

    Accurate only to the system timer tick, so it is used by the unit tests
    (which pass fake deadlines) and when a high-resolution timer is
    unavailable. The real app injects win32.PreciseWaiter instead.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()

    def wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def wait(self, deadline: Optional[float]) -> None:
        with self._cond:
            timeout = None if deadline is None else max(0.0, deadline - time.perf_counter())
            self._cond.wait(timeout)


class TapScheduler:
    def __init__(
        self,
        engine: "Engine",
        inject: Callable[[Sequence[Injection]], None],
        publish: Callable[[Sequence[Event]], None],
        waiter=None,
    ) -> None:
        self._engine = engine
        self._inject = inject
        self._publish = publish
        self._waiter = waiter if waiter is not None else _CoarseWaiter()
        self._stop = False
        self._thread = None  # type: threading.Thread | None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tap-scheduler", daemon=True)
        self._thread.start()

    def wake(self) -> None:
        """Ask the scheduler to re-check its deadline (after any hook event)."""
        self._waiter.wake()

    def stop(self) -> None:
        self._stop = True
        self._waiter.wake()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while True:
            if self._stop:
                return
            # A deadline may have moved or vanished while we were injecting and
            # logging, so read it fresh on every pass.
            self._waiter.wait(self._engine.next_deadline)
            if self._stop:
                return
            self._tick()

    def _tick(self) -> None:
        injections = self._engine.due(time.perf_counter())
        if injections:
            self._inject(injections)
        events = self._engine.drain_events()
        if events:
            self._publish(events)
