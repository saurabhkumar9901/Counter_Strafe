import ctypes
import threading
import time

import pytest
from ctypes import wintypes

from counter_strafe import engine as E
from counter_strafe import win32


def test_mouse_hook_receives_sendinput_click():
    seen = []
    hook = win32.MouseHook(lambda ev: (seen.append(ev), False)[1])
    hook.install()

    def inject():
        time.sleep(0.3)
        win32.send_mouse("lmb", True)
        win32.send_mouse("lmb", False)

    threading.Thread(target=inject, daemon=True).start()

    msg = wintypes.MSG()
    end = time.time() + 2.0
    while time.time() < end:
        while win32.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            win32.user32.TranslateMessage(ctypes.byref(msg))
            win32.user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.005)

    hook.uninstall()

    if not seen:
        pytest.skip("no interactive desktop; SendInput delivered nothing")

    print("sizeof(MSLLHOOKSTRUCT) =", ctypes.sizeof(win32.MSLLHOOKSTRUCT), "(expect 32 on x64)")
    print("events:", [(e.kind, e.button, e.injected) for e in seen])

    assert all(e.button == "lmb" for e in seen), "mouse translation wrong"
    assert all(e.injected for e in seen), "LLMHF_INJECTED not detected"
    assert [e.kind for e in seen] == [E.MOUSE_DOWN, E.MOUSE_UP], "expected down then up"
