"""Virtual-key and scan-code tables. Pure data, no ctypes, no OS calls.

Scan codes are the physical keys, which is what we inject: DirectInput-era
games frequently ignore virtual-key input and only see scan codes. Virtual keys
are only used as a fallback when translating incoming hook events.
"""

from typing import Dict, NamedTuple, Optional


class Key(NamedTuple):
    name: str
    vk: int
    scan: int


# name: (scan, vk)
_LETTERS = {
    "a": (0x1E, 0x41),
    "b": (0x30, 0x42),
    "c": (0x2E, 0x43),
    "d": (0x20, 0x44),
    "e": (0x12, 0x45),
    "f": (0x21, 0x46),
    "g": (0x22, 0x47),
    "h": (0x23, 0x48),
    "i": (0x17, 0x49),
    "j": (0x24, 0x4A),
    "k": (0x25, 0x4B),
    "l": (0x26, 0x4C),
    "m": (0x32, 0x4D),
    "n": (0x31, 0x4E),
    "o": (0x18, 0x4F),
    "p": (0x19, 0x50),
    "q": (0x10, 0x51),
    "r": (0x13, 0x52),
    "s": (0x1F, 0x53),
    "t": (0x14, 0x54),
    "u": (0x16, 0x55),
    "v": (0x2F, 0x56),
    "w": (0x11, 0x57),
    "x": (0x2D, 0x58),
    "y": (0x15, 0x59),
    "z": (0x2C, 0x5A),
}

# F-keys do not follow a linear scan-code pattern (F11/F12 differ).
_FKEYS = {
    "f1": (0x3B, 0x70),
    "f2": (0x3C, 0x71),
    "f3": (0x3D, 0x72),
    "f4": (0x3E, 0x73),
    "f5": (0x3F, 0x74),
    "f6": (0x40, 0x75),
    "f7": (0x41, 0x76),
    "f8": (0x42, 0x77),
    "f9": (0x43, 0x78),
    "f10": (0x44, 0x79),
    "f11": (0x57, 0x7A),
    "f12": (0x58, 0x7B),
}

KEYS: Dict[str, Key] = {}

for _name, (_scan, _vk) in _LETTERS.items():
    KEYS[_name] = Key(_name, _vk, _scan)
for _name, (_scan, _vk) in _FKEYS.items():
    KEYS[_name] = Key(_name, _vk, _scan)

KEY_BY_SCAN: Dict[int, Key] = {}
KEY_BY_VK: Dict[int, Key] = {}
for _key in KEYS.values():
    KEY_BY_SCAN.setdefault(_key.scan, _key)
    KEY_BY_VK.setdefault(_key.vk, _key)

del _name, _scan, _vk, _key


def key_name(scan: int, vk: int) -> Optional[str]:
    """Best-effort mapping of an incoming hook event to a key name."""
    key = KEY_BY_SCAN.get(scan & 0xFF)
    if key is not None:
        return key.name
    key = KEY_BY_VK.get(vk & 0xFF)
    if key is not None:
        return key.name
    return None
