"""Thin ctypes layer. Everything OS-specific lives here.

* global low-level keyboard hook (WH_KEYBOARD_LL)
* scan-code injection via SendInput
* elevation probe
* console control handler (flush on shutdown)
* beep

No game is touched, no process is opened, nothing is injected into cs2.exe.
"""

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Callable, Optional

from . import engine as engine_mod
from . import keymap

if sys.platform != "win32":  # pragma: no cover
    raise ImportError("counter_strafe is Windows-only")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

ULONG_PTR = wintypes.WPARAM
LRESULT = ctypes.c_ssize_t

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

WH_KEYBOARD_LL = 13
HC_ACTION = 0

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

LLKHF_INJECTED = 0x10

# Identifies our own synthetic events in KBDLLHOOKSTRUCT.dwExtraInfo. Belt and
# braces next to LLKHF_INJECTED: other programs' injections are also skipped,
# which is the behaviour we want anyway.
SENTINEL = 0x43534645  # "CSFE"

TOKEN_QUERY = 0x0008
TokenElevation = 20


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT

user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int,
    LowLevelKeyboardProc,
    wintypes.HINSTANCE,
    wintypes.DWORD,
)
user32.SetWindowsHookExW.restype = wintypes.HHOOK

user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT

user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.restype = ctypes.c_int

user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
user32.DispatchMessageW.restype = LRESULT

kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

kernel32.GetCurrentProcess.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.SetConsoleCtrlHandler.argtypes = (PHANDLER_ROUTINE, wintypes.BOOL)
kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL

advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
advapi32.OpenProcessToken.restype = wintypes.BOOL

advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
advapi32.GetTokenInformation.restype = wintypes.BOOL


def send_key(key: str, down: bool) -> bool:
    """Inject one scan-code keyboard event. Returns True if Windows took it."""
    info = keymap.KEYS[key]
    flags = KEYEVENTF_SCANCODE
    if not down:
        flags |= KEYEVENTF_KEYUP
    ki = KEYBDINPUT(0, info.scan, flags, 0, SENTINEL)
    inp = INPUT(type=INPUT_KEYBOARD, u=_INPUTUNION(ki=ki))
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    return sent == 1


class Injector:
    """Sends real input."""

    def send(self, injection: engine_mod.Injection) -> bool:
        return send_key(injection.key, injection.kind == engine_mod.DOWN)


class DryRunInjector:
    """Logs intent, injects nothing (--dry-run)."""

    def send(self, injection: engine_mod.Injection) -> bool:
        return True


class KeyboardHook:
    """WH_KEYBOARD_LL hook plus its message pump.

    The hook must be installed and pumped from the same thread, so run() owns
    the main thread. The callback must return quickly; it does.
    """

    def __init__(self, dispatch: Callable[[engine_mod.KeyEvent], bool], swallow_hotkey: bool = True) -> None:
        self._dispatch = dispatch
        self._swallow_hotkey = swallow_hotkey
        self._held = set()
        self._proc = LowLevelKeyboardProc(self._on_event)
        self._hook = None  # type: Optional[int]

    def install(self) -> "KeyboardHook":
        module = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)
        if not self._hook:
            raise OSError(ctypes.get_last_error(), "SetWindowsHookExW failed")
        return self

    def uninstall(self) -> None:
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def run(self) -> int:
        msg = wintypes.MSG()
        while True:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result == 0:
                return 0  # WM_QUIT
            if result == -1:
                raise OSError(ctypes.get_last_error(), "GetMessageW failed")
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------------------------------------------ hook

    def _on_event(self, ncode, wparam, lparam):
        if ncode == HC_ACTION:
            try:
                kb = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                ev = self._translate(kb, wparam)
                if ev is not None and self._dispatch(ev) and self._swallow_hotkey:
                    return 1  # swallow: the game / other apps never see it
            except Exception:
                # Never break the hook chain or Windows drops the hook.
                pass
        return user32.CallNextHookEx(None, ncode, wparam, lparam)

    def _translate(self, kb, wparam) -> Optional[engine_mod.KeyEvent]:
        name = keymap.key_name(kb.scanCode, kb.vkCode)
        if name is None:
            return None

        injected = bool(kb.flags & LLKHF_INJECTED)

        if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kind = engine_mod.DOWN
        elif wparam in (WM_KEYUP, WM_SYSKEYUP):
            kind = engine_mod.UP
        else:
            return None

        repeat = False
        if injected:
            return engine_mod.KeyEvent(kind, name, time.monotonic(), injected=True)
        if kind == engine_mod.DOWN:
            repeat = name in self._held
            self._held.add(name)
        else:
            self._held.discard(name)

        return engine_mod.KeyEvent(
            kind, name, time.monotonic(), injected=False, repeat=repeat
        )


def is_elevated() -> bool:
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        return False
    try:
        elevation = wintypes.DWORD()
        size = wintypes.DWORD()
        if not advapi32.GetTokenInformation(
            token,
            TokenElevation,
            ctypes.byref(elevation),
            ctypes.sizeof(elevation),
            ctypes.byref(size),
        ):
            return False
        return bool(elevation.value)
    finally:
        kernel32.CloseHandle(token)


def beep(frequency: int, duration_ms: int = 60) -> None:
    try:
        import winsound

        winsound.Beep(frequency, duration_ms)
    except Exception:
        pass


def install_console_handler(callback: Callable[[int], bool]):
    """Register a handler for Ctrl+C / window close. Keep the return value alive."""
    proc = PHANDLER_ROUTINE(callback)
    if not kernel32.SetConsoleCtrlHandler(proc, True):
        return None
    return proc
