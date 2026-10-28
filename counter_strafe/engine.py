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

        self._tap_s = tap_s
        self._keys: FrozenSet[str] = frozenset(keys)
        self._hotkey = hotkey
        self._armed = bool(armed)
        self._physical: Set[str] = set()
        self._pending: Dict[str, float] = {}
        self._events: List[Event] = []
        self._lock = threading.RLock()

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
