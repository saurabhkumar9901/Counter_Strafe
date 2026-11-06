import threading
import time

import pytest

from counter_strafe import engine as E
from counter_strafe.scheduler import TapScheduler


def test_scheduler_fires_key_up_without_hand_holding():
    engine = E.Engine(0.01, armed=True)
    fired = []
    seen = threading.Event()

    def inject(injections):
        fired.extend(injections)
        if any(i.kind == E.UP for i in injections):
            seen.set()

    scheduler = TapScheduler(engine, inject, lambda i: None, lambda events: None)
    scheduler.start()

    def on_key(ev):
        result = engine.handle(ev)
        if result.injections:
            inject(result.injections)
        scheduler.wake()

    try:
        on_key(E.KeyEvent(E.DOWN, "d", time.perf_counter()))
        on_key(E.KeyEvent(E.UP, "d", time.perf_counter()))

        assert seen.wait(timeout=2.0), "scheduler never sent the key-up"
        assert fired[0].kind == E.DOWN
        assert fired[-1] == E.Injection(E.UP, "a")
        assert engine.pending == {}
    finally:
        scheduler.stop()


def test_scheduler_stop_is_clean():
    engine = E.Engine(0.05)
    scheduler = TapScheduler(engine, lambda i: None, lambda m: None, lambda e: None)
    scheduler.start()
    scheduler.wake()
    scheduler.stop()
    assert engine.next_deadline is None


def test_precise_waiter_keeps_tap_length_within_a_few_ms():
    """Regression guard for the ~15 ms timer tick that used to stretch taps."""
    from counter_strafe import win32

    waiter = win32.PreciseWaiter()
    if not waiter.usable:
        pytest.skip("high-resolution waitable timer unavailable")

    engine = E.Engine(0.02, armed=True)
    fired = []
    seen = threading.Event()

    def inject(injections):
        for injection in injections:
            fired.append((time.perf_counter(), injection))
            if injection.kind == E.UP:
                seen.set()

    scheduler = TapScheduler(engine, inject, lambda i: None, lambda events: None, waiter=waiter)
    scheduler.start()
    try:
        engine.handle(E.KeyEvent(E.DOWN, "d", time.perf_counter()))

        release = time.perf_counter()
        result = engine.handle(E.KeyEvent(E.UP, "d", release))
        down_sent = time.perf_counter()
        inject(result.injections)
        scheduler.wake()

        assert seen.wait(timeout=1.0), "scheduler never sent the key-up"
        up_sent = [ts for ts, injection in fired if injection.kind == E.UP][0]

        # The deadline is release + 20 ms; the timer may only add a millisecond.
        assert 18.0 <= (up_sent - down_sent) * 1000.0 < 26.0
    finally:
        scheduler.stop()


def test_scheduler_fires_mouse_burst_via_inject_mouse():
    from counter_strafe import win32

    waiter = win32.PreciseWaiter()
    if not waiter.usable:
        pytest.skip("high-resolution waitable timer unavailable")

    engine = E.Engine(
        0.01,
        armed=True,
        mouse_enabled=True,
        mouse_tap_threshold_s=0.005,
        mouse_burst_threshold_s=0.01,
        mouse_burst_count=2,
        mouse_burst_interval_s=0.01,
    )
    fired_mouse = []

    def inject(injections):
        pass

    def inject_mouse(mouse_injections):
        fired_mouse.extend(mouse_injections)

    def publish(events):
        pass

    scheduler = TapScheduler(engine, inject, inject_mouse, publish, waiter=waiter)
    scheduler.start()
    try:
        engine.handle_mouse(E.MouseEvent(E.MOUSE_DOWN, "lmb", time.perf_counter()))
        scheduler.wake()

        deadline = time.perf_counter() + 1.0
        while len(fired_mouse) < 4 and time.perf_counter() < deadline:
            time.sleep(0.005)
    finally:
        scheduler.stop()

    assert len(fired_mouse) == 4
    assert [i.kind for i in fired_mouse] == [
        E.MOUSE_DOWN,
        E.MOUSE_UP,
        E.MOUSE_DOWN,
        E.MOUSE_UP,
    ]
