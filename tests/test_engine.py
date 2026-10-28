import pytest

from counter_strafe import engine as E

TAP_S = 0.05


def make(armed=True, **kwargs):
    kwargs.setdefault("movement_keys", ("a", "d"))
    kwargs.setdefault("hotkey", "f8")
    return E.Engine(TAP_S, armed=armed, **kwargs)


def down(engine, key, ts, injected=False, repeat=False):
    return engine.handle(E.KeyEvent(E.DOWN, key, ts, injected=injected, repeat=repeat))


def up(engine, key, ts, injected=False):
    return engine.handle(E.KeyEvent(E.UP, key, ts, injected=injected))


def kinds(engine):
    return [(e.kind, e.key) for e in engine.drain_events()]


def due_at(engine, ts):
    """Fire deadlines at `ts`, tolerating the float error in ts + tap_s."""
    return engine.due(ts + 1e-9)


# --------------------------------------------------------------- basic taps


@pytest.mark.parametrize("released,tapped", [("d", "a"), ("a", "d")])
def test_release_taps_opposite_direction(released, tapped):
    engine = make()
    down(engine, released, 0.0)
    result = up(engine, released, 0.10)

    assert result.injections == (E.Injection(E.DOWN, tapped),)
    assert engine.next_deadline == pytest.approx(0.15)

    assert engine.due(0.149) == ()
    assert due_at(engine, 0.15) == (E.Injection(E.UP, tapped),)
    assert engine.next_deadline is None
    assert engine.pending == {}


def test_no_tap_while_disarmed():
    engine = make(armed=False)
    down(engine, "d", 0.0)
    result = up(engine, "d", 0.10)

    assert result.injections == ()
    assert engine.next_deadline is None
    assert kinds(engine) == [(E.RELEASE, "d")]


def test_each_release_fires_independently():
    engine = make()
    engine.handle(E.KeyEvent(E.DOWN, "d", 0.0))
    engine.handle(E.KeyEvent(E.UP, "d", 0.10))
    due_at(engine, 0.15)
    engine.handle(E.KeyEvent(E.DOWN, "a", 0.20))
    result = engine.handle(E.KeyEvent(E.UP, "a", 0.30))

    assert result.injections == (E.Injection(E.DOWN, "d"),)


# ------------------------------------------------- collision / key stealing


def test_skip_when_opposite_key_already_held():
    engine = make()
    down(engine, "a", 0.0)
    down(engine, "d", 0.05)
    result = up(engine, "d", 0.10)

    assert result.injections == ()
    assert (E.SKIP, "d") in kinds(engine)


def test_physical_press_during_tap_is_not_eaten():
    """Release D -> we tap A; the finger then presses A inside the window."""
    engine = make()
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)  # owed: A up at 0.06
    down(engine, "a", 0.03)  # real press takes over

    assert due_at(engine, 0.06) == ()
    assert engine.pending == {}
    assert (E.SKIP, "a") in kinds(engine)


def test_physical_release_cancels_owed_up_and_taps_back():
    """A/D churn inside one window must not leave a key stuck down."""
    engine = make()
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)  # owed: A up at 0.06
    down(engine, "a", 0.02)
    result = up(engine, "a", 0.03)  # real A up + tap D

    assert result.injections == (E.Injection(E.DOWN, "d"),)
    assert (E.CANCEL_UP, "a") in kinds(engine)

    assert due_at(engine, 0.06) == ()  # the A up was cancelled
    assert due_at(engine, 0.08) == (E.Injection(E.UP, "d"),)


def test_repeat_release_extends_running_tap():
    engine = make()
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)  # owed: A up at 0.06
    down(engine, "d", 0.02)
    result = up(engine, "d", 0.03)  # extend to 0.08, no second down

    assert result.injections == ()
    assert engine.due(0.07) == ()
    assert due_at(engine, 0.08) == (E.Injection(E.UP, "a"),)


# ------------------------------------------------------- arm / disarm / flush


def test_disarm_flushes_owed_key_up():
    engine = make()
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)

    assert engine.set_armed(False, 0.02) == (E.Injection(E.UP, "a"),)
    assert engine.armed is False
    assert engine.pending == {}


def test_flush_does_not_steal_a_held_key():
    engine = make()
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)  # owed: A up
    down(engine, "a", 0.02)  # finger now holds A

    assert engine.set_armed(False, 0.03) == ()
    assert (E.SKIP, "a") in kinds(engine)


def test_hotkey_is_swallowed_and_ignores_repeat():
    engine = make(armed=False)

    first = down(engine, "f8", 0.0)
    assert first.swallow is True
    assert engine.armed is True

    repeat = down(engine, "f8", 0.1, repeat=True)
    assert repeat.swallow is True
    assert engine.armed is True

    released = up(engine, "f8", 0.2)
    assert released.swallow is True
    assert engine.armed is True  # key-up must not toggle

    second = down(engine, "f8", 0.3)
    assert second.swallow is True
    assert engine.armed is False


def test_hotkey_disarm_releases_owed_tap():
    engine = make(armed=True)
    down(engine, "d", 0.0)
    up(engine, "d", 0.01)

    result = down(engine, "f8", 0.02)
    assert result.injections == (E.Injection(E.UP, "a"),)
    assert engine.pending == {}


# ------------------------------------------------------------- input filtering


def test_injected_events_are_ignored():
    engine = make()

    assert up(engine, "d", 0.0, injected=True).injections == ()
    assert kinds(engine) == []

    # An injected A must not count as a physically held A.
    down(engine, "a", 0.01, injected=True)
    down(engine, "d", 0.02)
    result = up(engine, "d", 0.03)

    assert result.injections == (E.Injection(E.DOWN, "a"),)


def test_untracked_key_is_ignored():
    engine = make(movement_keys=("a", "d"))
    down(engine, "w", 0.0)
    result = up(engine, "w", 0.10)

    assert result.injections == ()
    assert kinds(engine) == []


def test_w_and_s_supported_when_configured():
    engine = make(movement_keys=("w", "s"))
    down(engine, "w", 0.0)
    result = up(engine, "w", 0.10)

    assert result.injections == (E.Injection(E.DOWN, "s"),)


# -------------------------------------------------------------- construction


@pytest.mark.parametrize(
    "kwargs",
    [
        {"movement_keys": ("a", "q")},
        {"movement_keys": ()},
        {"movement_keys": ("a", "d"), "hotkey": "a"},
    ],
)
def test_invalid_construction(kwargs):
    with pytest.raises(ValueError):
        E.Engine(TAP_S, **kwargs)


def test_tap_must_be_positive():
    with pytest.raises(ValueError):
        E.Engine(0.0)
