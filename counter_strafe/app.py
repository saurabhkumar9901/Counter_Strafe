"""Wiring: config -> engine -> hook -> scheduler -> log. No decisions here."""

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from typing import Iterable, Optional, Sequence

from . import config as config_mod
from . import win32
from .engine import (
    ARM,
    DISARM,
    Engine,
    Event,
    Injection,
    KeyEvent,
    MOUSE_DOWN,
    MOUSE_UP,
    MouseEvent,
    MouseInjection,
)
from .scheduler import TapScheduler

BANNER = """\
counter_strafe - A/D release tap
--------------------------------------------------------------------
RISK: this is input automation in an online game. Valve's August 2024
policy ("Side-stepping Skill") allows kicking players suspected of
automating input from a single game input on official servers, and
perfect A/D counter-strafing is detectably different from human play.
This tool contains no evasion. Use it at your own risk.

Starts DISARMED.  F8 = arm / disarm.  Ctrl+C here = quit and release keys.
--------------------------------------------------------------------
"""


class EventLog:
    """Console echo + JSONL audit trail. Called from the scheduler thread only."""

    def __init__(self, path: Optional[str], echo: bool = True) -> None:
        self._echo = echo
        self._fh = None
        if path:
            self._fh = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, ev: Event) -> None:
        if self._fh is not None:
            record = {
                "wall": datetime.now().isoformat(timespec="milliseconds"),
                "t": round(ev.ts, 6),
                "kind": ev.kind,
                "key": ev.key,
                "detail": ev.detail,
            }
            self._fh.write(json.dumps(record) + "\n")
        if self._echo:
            print(self._format(ev), flush=True)

    def write_many(self, events: Iterable[Event]) -> None:
        for ev in events:
            self.write(ev)

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @staticmethod
    def _format(ev: Event) -> str:
        line = "[%8.3f] %-12s %s" % (ev.ts, ev.kind, ev.key)
        if ev.detail:
            line += "  (%s)" % ev.detail
        return line


class App:
    def __init__(self, cfg: config_mod.Config, injector, echo: bool = True) -> None:
        self.cfg = cfg
        self.engine = Engine(
            tap_s=cfg.tap_ms / 1000.0,
            movement_keys=cfg.movement_keys,
            hotkey=cfg.hotkey,
            mouse_enabled=cfg.mouse_enabled,
            mouse_tap_threshold_s=cfg.mouse_tap_threshold_ms / 1000.0,
            mouse_burst_threshold_s=cfg.mouse_burst_threshold_ms / 1000.0,
            mouse_burst_count=cfg.mouse_burst_count,
            mouse_burst_interval_s=cfg.mouse_burst_interval_ms / 1000.0,
        )
        self.injector = injector
        self.log = EventLog(cfg.log_path, echo=echo)
        self.scheduler = TapScheduler(
            self.engine,
            self._inject,
            self._inject_mouse,
            self._publish,
            waiter=win32.PreciseWaiter(),
        )
        self.hook = None  # type: Optional[win32.KeyboardHook]
        self.mouse_hook = None  # type: Optional[win32.MouseHook]
        self._closed = False
        self._close_lock = threading.Lock()

    # ------------------------------------------------------------------- run

    def run(self) -> None:
        self.scheduler.start()
        try:
            self.hook = win32.KeyboardHook(self._on_key, self.cfg.swallow_hotkey)
            self.hook.install()
            self.mouse_hook = win32.MouseHook(self._on_mouse)
            self.mouse_hook.install()
            self.hook.run()  # one message pump services both hooks on this thread
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self.scheduler.stop()
        if self.hook is not None:
            try:
                self.hook.uninstall()
            except Exception:
                pass
        if self.mouse_hook is not None:
            try:
                self.mouse_hook.uninstall()
            except Exception:
                pass
        now = time.perf_counter()
        owed = self.engine.set_armed(False, now)
        owed = tuple(owed) + tuple(self.engine.flush(now))
        if owed:
            self._inject(owed)
        self._publish(self.engine.drain_events())
        self.log.close()

    def on_console_ctrl(self, ctrl_type: int) -> bool:
        # Flush owed key-ups, then let Windows terminate the process normally.
        try:
            self.shutdown()
        except Exception:
            pass
        return False

    # ------------------------------------------------------------- callbacks

    def _on_key(self, ev: KeyEvent) -> bool:
        result = self.engine.handle(ev)
        if result.injections:
            self._inject(result.injections)
        self.scheduler.wake()
        return bool(result.swallow)

    def _inject(self, injections: Sequence[Injection]) -> None:
        for injection in injections:
            try:
                self.injector.send(injection)
            except Exception as exc:  # pragma: no cover - platform failure
                self.log.write(Event(time.perf_counter(), "inject_error", injection.key, str(exc)))

    def _inject_mouse(self, mouse_injections: Sequence[MouseInjection]) -> None:
        for injection in mouse_injections:
            try:
                self.injector.send_mouse(injection.button, injection.kind == MOUSE_DOWN)
            except Exception as exc:  # pragma: no cover - platform failure
                self.log.write(
                    Event(time.perf_counter(), "inject_error", injection.button, str(exc))
                )

    def _on_mouse(self, ev: MouseEvent) -> bool:
        result = self.engine.handle_mouse(ev)
        if result.mouse_injections:
            self._inject_mouse(result.mouse_injections)
        self.scheduler.wake()
        return False  # never swallow mouse input

    def _publish(self, events: Iterable[Event]) -> None:
        events = tuple(events)
        if not events:
            return
        self.log.write_many(events)
        if self.cfg.beep_on_toggle:
            for ev in events:
                if ev.kind == ARM:
                    win32.beep(880)
                elif ev.kind == DISARM:
                    win32.beep(440)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="counter_strafe",
        description="Taps the opposite movement key on A/D release.",
    )
    parser.add_argument("--config", default=None, help="path to config.json (default: ./config.json)")
    parser.add_argument("--tap-ms", type=int, default=None, help="override tap_ms")
    parser.add_argument("--no-log", action="store_true", help="do not write the JSONL log")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run the hook and log, but inject nothing (detection test)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    overrides = {}
    if args.tap_ms is not None:
        overrides["tap_ms"] = args.tap_ms
    if args.no_log:
        overrides["log_path"] = None

    try:
        cfg = config_mod.load(args.config, overrides)
    except config_mod.ConfigError as exc:
        print("config error: %s" % exc, file=sys.stderr)
        return 2

    print(BANNER)
    print(
        "tap_ms=%d  hotkey=%s  movement_keys=%s  log=%s%s"
        % (
            cfg.tap_ms,
            cfg.hotkey,
            ",".join(cfg.movement_keys),
            cfg.log_path or "off",
            "  DRY-RUN" if args.dry_run else "",
        )
    )

    if not win32.is_elevated():
        print(
            "warning: not elevated. A low-integrity hook cannot see keys aimed at "
            "an elevated game; run as admin if the game is elevated.",
            flush=True,
        )

    injector = win32.DryRunInjector() if args.dry_run else win32.Injector()
    try:
        app = App(cfg, injector)
    except OSError as exc:
        print("cannot open log file: %s" % exc, file=sys.stderr)
        return 2
    handler = win32.install_console_handler(app.on_console_ctrl)

    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.shutdown()
        _ = handler  # keep the ctypes callback alive until now

    print("stopped.")
    return 0
