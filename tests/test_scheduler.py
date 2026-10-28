import threading
import time

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

    scheduler = TapScheduler(engine, inject, lambda events: None)
    scheduler.start()

    def on_key(ev):
        result = engine.handle(ev)
        if result.injections:
            inject(result.injections)
        scheduler.wake()

    try:
        on_key(E.KeyEvent(E.DOWN, "d", time.monotonic()))
        on_key(E.KeyEvent(E.UP, "d", time.monotonic()))

        assert seen.wait(timeout=2.0), "scheduler never sent the key-up"
        assert fired[0].kind == E.DOWN
        assert fired[-1] == E.Injection(E.UP, "a")
        assert engine.pending == {}
    finally:
        scheduler.stop()


def test_scheduler_stop_is_clean():
    engine = E.Engine(0.05)
    scheduler = TapScheduler(engine, lambda i: None, lambda e: None)
    scheduler.start()
    scheduler.wake()
    scheduler.stop()
    assert engine.next_deadline is None
