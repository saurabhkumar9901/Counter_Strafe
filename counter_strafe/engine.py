"""Pure counter-strafe state machine.

Nothing in this module touches the OS: no clock reads, no I/O, no ctypes.
Every entry point takes an explicit monotonic timestamp, so the whole thing can
be driven by a fake clock in tests.

Vocabulary
----------
physical : the user's real keyboard, as reported by the low-level hook.
injected : keystrokes this program synthesises. Never acted upon, never
           recorded as physical state.
pending  : a key we injected *down* and still owe an *up* for.
"""

import threading
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Sequence, Set, Tuple

DOWN = "down"
UP = "up"
MOUSE_DOWN = "mouse_down"
MOUSE_UP = "mouse_up"

# Only keys with a defined opposite can be counter-strafed. w/s is wired in
# already so enabling it later is a config change, not a code change.
OPPOSITE = {"a": "d", "d": "a", "w": "s", "s": "w"}

# audit/log event kinds
ARM = "arm"
DISARM = "disarm"
RELEASE = "release"
INJECT_DOWN = "inject_down"
INJECT_UP = "inject_up"
CANCEL_UP = "cancel_up"
EXTEND = "extend"
SKIP = "skip"


@dataclass(frozen=True)
class Injection:
    """One synthetic key event the caller must send to the OS."""

    kind: str
    key: str


@dataclass(frozen=True)
class MouseInjection:
    """One synthetic mouse event the caller must send to the OS."""

    kind: str
    button: str


@dataclass(frozen=True)
class MouseEvent:
    """One mouse event as reported by the platform layer."""

    kind: str
    button: str
    ts: float
    injected: bool = False


@dataclass(frozen=True)
class KeyEvent:
    """One key event as reported by the platform layer."""

    kind: str
    key: str
    ts: float
    injected: bool = False
    repeat: bool = False


@dataclass(frozen=True)
class Result:
    """What the caller should do about a KeyEvent."""

    injections: Tuple[Injection, ...] = ()
    mouse_injections: Tuple[MouseInjection, ...] = ()
    swallow: bool = False


@dataclass(frozen=True)
class Event:
    """Audit record. Written to the console/JSONL by the app layer."""

    ts: float
    kind: str
    key: str = ""
    detail: str = ""


class Engine:
    """Decides when to inject an opposite-direction tap.

    Thread safety: every public method takes the internal lock, so the hook
    thread and the scheduler thread can call in concurrently. Injection is
    never performed while the lock is held by *this* module; callers send the
    returned injections after the call returns.
    """

    def __init__(
        self,
        tap_s: float,
        movement_keys: Sequence[str] = ("a", "d"),
        hotkey: str = "f8",
        armed: bool = False,
        mouse_enabled: bool = False,
        mouse_tap_threshold_s: float = 0.2,
        mouse_burst_threshold_s: float = 2.0,
        mouse_burst_count: int = 3,
        mouse_burst_interval_s: float = 0.1,
    ) -> None:
        tap_s = float(tap_s)
        if tap_s <= 0:
            raise ValueError("tap_s must be > 0")

        keys: List[str] = []
        for raw in movement_keys:
            key = str(raw).lower()
            if key not in OPPOSITE:
                raise ValueError("unsupported movement key: %r" % (raw,))
            if key not in keys:
                keys.append(key)
        if not keys:
            raise ValueError("movement_keys must not be empty")

        hotkey = str(hotkey).lower()
        if hotkey in keys:
            raise ValueError("hotkey must not also be a movement key")

        if mouse_tap_threshold_s <= 0:
            raise ValueError("mouse_tap_threshold_s must be > 0")
        if mouse_burst_threshold_s <= mouse_tap_threshold_s:
            raise ValueError("mouse_burst_threshold_s must exceed mouse_tap_threshold_s")
        if mouse_burst_count < 1:
            raise ValueError("mouse_burst_count must be >= 1")
        if mouse_burst_interval_s <= 0:
            raise ValueError("mouse_burst_interval_s must be > 0")

        self._tap_s = tap_s
        self._keys: FrozenSet[str] = frozenset(keys)
        self._hotkey = hotkey
        self._armed = bool(armed)
        self._physical: Set[str] = set()
        self._pending: Dict[str, float] = {}
        self._events: List[Event] = []
        self._lock = threading.RLock()

        self._mouse_enabled = bool(mouse_enabled)
        self._mouse_tap_threshold_s = float(mouse_tap_threshold_s)
        self._mouse_burst_threshold_s = float(mouse_burst_threshold_s)
        self._mouse_burst_count = int(mouse_burst_count)
        self._mouse_burst_interval_s = float(mouse_burst_interval_s)
        self._mouse_down_ts: Optional[float] = None
        self._mouse_shots_fired: int = 0

    # ------------------------------------------------------------------ state

    @property
    def armed(self) -> bool:
        with self._lock:
            return self._armed

    @property
    def tap_s(self) -> float:
        return self._tap_s

    @property
    def pending(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._pending)

    @property
    def next_deadline(self) -> Optional[float]:
        """Earliest moment an owed key-up must be sent, or None."""
        with self._lock:
            if not self._pending:
                return None
            return min(self._pending.values())

    @property
    def next_mouse_deadline(self) -> Optional[float]:
        """Earliest moment an owed mouse burst shot must be sent, or None."""
        with self._lock:
            if self._mouse_down_ts is None:
                return None
            if self._mouse_shots_fired >= self._mouse_burst_count:
                return None
            return (
                self._mouse_down_ts
                + self._mouse_burst_threshold_s
                + self._mouse_shots_fired * self._mouse_burst_interval_s
            )

    # -------------------------------------------------------------- decisions

    def handle(self, ev: KeyEvent) -> Result:
        """Consume one platform key event."""
        with self._lock:
            if ev.injected:
                # Our own output (or another program's). Never physical state.
                return Result()

            key = ev.key.lower()

            if key == self._hotkey:
                if ev.kind != DOWN or ev.repeat:
                    # Swallow both edges so the toggle key never reaches the
                    # game, and ignore auto-repeat.
                    return Result(swallow=True)
                injections = tuple(self._set_armed(not self._armed, ev.ts))
                return Result(injections, swallow=True)

            if key not in self._keys:
                return Result()

            if ev.kind == DOWN:
                self._physical.add(key)
                return Result()

            if ev.kind != UP:
                return Result()

            self._physical.discard(key)
            return Result(tuple(self._on_release(key, ev.ts)))

    def handle_mouse(self, ev: MouseEvent) -> Result:
        """Consume one platform mouse event."""
        with self._lock:
            if ev.injected or not self._mouse_enabled:
                return Result()

            button = ev.button.lower()
            if button != "lmb":
                return Result()

            if ev.kind == MOUSE_DOWN:
                if not self._armed:
                    return Result()
                self._mouse_down_ts = ev.ts
                self._mouse_shots_fired = 0
                return Result()

            if ev.kind != MOUSE_UP:
                return Result()

            down_ts = self._mouse_down_ts
            self._mouse_down_ts = None
            self._mouse_shots_fired = 0
            if down_ts is None:
                return Result()

            hold = ev.ts - down_ts
            if hold < self._mouse_tap_threshold_s:
                self._log(ev.ts, RELEASE, "", "mouse tap %s" % button)
                return Result(
                    mouse_injections=(
                        MouseInjection(MOUSE_DOWN, button),
                        MouseInjection(MOUSE_UP, button),
                    )
                )
            return Result()

    def due_mouse(self, ts: float) -> Tuple[MouseInjection, ...]:
        """Fire burst shots whose deadline has passed while LMB is held."""
        with self._lock:
            if self._mouse_down_ts is None:
                return ()
            out: List[MouseInjection] = []
            while self._mouse_shots_fired < self._mouse_burst_count:
                deadline = (
                    self._mouse_down_ts
                    + self._mouse_burst_threshold_s
                    + self._mouse_shots_fired * self._mouse_burst_interval_s
                )
                if deadline > ts:
                    break
                out.append(MouseInjection(MOUSE_DOWN, "lmb"))
                out.append(MouseInjection(MOUSE_UP, "lmb"))
                self._mouse_shots_fired += 1
            return tuple(out)

    def flush_mouse(self, ts: float) -> Tuple[MouseInjection, ...]:
        """Cancel any pending burst on disarm/shutdown; no stuck shots."""
        with self._lock:
            self._mouse_down_ts = None
            self._mouse_shots_fired = 0
            return ()

    def _on_release(self, key: str, ts: float) -> List[Injection]:
        self._log(ts, RELEASE, key)

        if not self._armed:
            return []

        opposite = OPPOSITE[key]

        if opposite in self._physical:
            # You are already holding the key we would have tapped.
            self._log(ts, SKIP, key, "opposite key already held")
            return []

        if opposite in self._pending:
            # A tap for that direction is still running; extend it instead of
            # injecting a second down for a key the game already thinks is down.
            deadline = max(self._pending[opposite], ts + self._tap_s)
            self._pending[opposite] = deadline
            self._log(ts, EXTEND, opposite, "tap already running")
            return []

        if key in self._pending:
            # We owed an up for this key, but the physical release just
            # delivered one. Drop ours so we do not eat the next press.
            del self._pending[key]
            self._log(ts, CANCEL_UP, key, "physical release supplied the up")

        self._pending[opposite] = ts + self._tap_s
        self._log(ts, INJECT_DOWN, opposite)
        return [Injection(DOWN, opposite)]

    def due(self, ts: float) -> Tuple[Injection, ...]:
        """Send every key-up whose deadline has passed."""
        with self._lock:
            return tuple(self._drain_due(ts))

    def flush(self, ts: float) -> Tuple[Injection, ...]:
        """Send every owed key-up right now (disarm/shutdown)."""
        with self._lock:
            return tuple(self._drain_due(ts, force=True))

    def set_armed(self, armed: bool, ts: float) -> Tuple[Injection, ...]:
        with self._lock:
            return tuple(self._set_armed(armed, ts))

    def _drain_due(self, ts: float, force: bool = False) -> List[Injection]:
        ripe = [k for k, deadline in self._pending.items() if force or deadline <= ts]
        out: List[Injection] = []
        for key in ripe:
            del self._pending[key]
            if key in self._physical:
                # Your finger is holding it: our synthetic up would cancel your
                # real press. Let the real release do the work.
                self._log(ts, SKIP, key, "held physically; real release carries it")
                continue
            self._log(ts, INJECT_UP, key)
            out.append(Injection(UP, key))
        return out

    def _set_armed(self, armed: bool, ts: float) -> List[Injection]:
        if armed == self._armed:
            return []
        if armed:
            self._armed = True
            self._log(ts, ARM, "", "armed")
            return []
        injections = self._drain_due(ts, force=True)
        self._armed = False
        self._log(ts, DISARM, "", "disarmed")
        return injections

    # ------------------------------------------------------------------- audit

    def _log(self, ts: float, kind: str, key: str = "", detail: str = "") -> None:
        self._events.append(Event(ts=ts, kind=kind, key=key, detail=detail))

    def drain_events(self) -> Tuple[Event, ...]:
        with self._lock:
            events = tuple(self._events)
            self._events.clear()
            return events
