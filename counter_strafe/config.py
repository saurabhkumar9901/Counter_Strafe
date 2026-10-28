"""config.json loading, validation, and CLI override merging."""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from . import keymap

DEFAULT_PATH = "config.json"

DEFAULTS: Dict[str, Any] = {
    "tap_ms": 50,
    "hotkey": "f8",
    "movement_keys": ["a", "d"],
    "swallow_hotkey": True,
    "beep_on_toggle": False,
    "log_path": "counter_strafe.jsonl",
}

TAP_MS_MIN = 5
TAP_MS_MAX = 1000

# Keys with a defined opposite direction, from the pure engine's table.
STRAFE_KEYS = ("a", "d", "w", "s")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    tap_ms: int
    hotkey: str
    movement_keys: Tuple[str, ...]
    swallow_hotkey: bool
    beep_on_toggle: bool
    log_path: Optional[str]


def load(path: Optional[str] = None, overrides: Optional[Mapping[str, Any]] = None) -> Config:
    """Merge DEFAULTS <- config file <- overrides, then validate."""
    merged: Dict[str, Any] = dict(DEFAULTS)
    merged["movement_keys"] = list(DEFAULTS["movement_keys"])

    path = DEFAULT_PATH if path is None else path
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as exc:
                raise ConfigError("%s: %s" % (path, exc))
        if not isinstance(data, dict):
            raise ConfigError("%s: top level must be a JSON object" % (path,))
        _check_known(data, path)
        merged.update(data)

    if overrides:
        _check_known(overrides, "command line")
        # Explicit on purpose: an override of None is meaningful (log_path = None
        # means "no log"), so callers only put keys in the dict they mean to set.
        merged.update(overrides)

    return _validate(merged)


def _check_known(data: Mapping[str, Any], source: str) -> None:
    unknown = sorted(set(data) - set(DEFAULTS))
    if unknown:
        raise ConfigError(
            "%s: unknown setting(s) %s; valid settings are %s"
            % (source, ", ".join(unknown), ", ".join(sorted(DEFAULTS)))
        )


def _validate(data: Mapping[str, Any]) -> Config:
    tap_ms = data["tap_ms"]
    if isinstance(tap_ms, bool) or not isinstance(tap_ms, int):
        raise ConfigError("tap_ms must be an integer number of milliseconds")
    if not TAP_MS_MIN <= tap_ms <= TAP_MS_MAX:
        raise ConfigError(
            "tap_ms must be between %d and %d (a 1 ms tap is below one frame "
            "at 60 fps; the game will not see it)" % (TAP_MS_MIN, TAP_MS_MAX)
        )

    hotkey = data["hotkey"]
    if not isinstance(hotkey, str) or hotkey.lower() not in keymap.KEYS:
        raise ConfigError(
            "hotkey must be one of: %s" % ", ".join(sorted(keymap.KEYS))
        )
    hotkey = hotkey.lower()

    movement_keys = data["movement_keys"]
    if not isinstance(movement_keys, (list, tuple)) or not movement_keys:
        raise ConfigError("movement_keys must be a non-empty list")
    keys = []
    for raw in movement_keys:
        if not isinstance(raw, str):
            raise ConfigError("movement_keys entries must be strings")
        key = raw.lower()
        if key not in STRAFE_KEYS:
            raise ConfigError(
                "unsupported movement key %r; use any of: %s"
                % (raw, ", ".join(STRAFE_KEYS))
            )
        if key in keys:
            raise ConfigError("duplicate movement key: %s" % key)
        keys.append(key)
    if hotkey in keys:
        raise ConfigError("hotkey %r must not also be a movement key" % hotkey)

    for flag in ("swallow_hotkey", "beep_on_toggle"):
        if not isinstance(data[flag], bool):
            raise ConfigError("%s must be true or false" % flag)

    log_path = data["log_path"]
    if log_path is not None and (not isinstance(log_path, str) or not log_path):
        raise ConfigError("log_path must be a non-empty string, or null to disable")

    return Config(
        tap_ms=tap_ms,
        hotkey=hotkey,
        movement_keys=tuple(keys),
        swallow_hotkey=data["swallow_hotkey"],
        beep_on_toggle=data["beep_on_toggle"],
        log_path=log_path,
    )
