# counter_strafe

A standalone Windows program that taps the **opposite** movement key when you
release A or D, so your character stops faster than releasing alone.

It is not an injector: it opens no game process, reads no game memory, and
ships no DLL. It installs a global low-level keyboard hook (`WH_KEYBOARD_LL`)
and sends scan-code keystrokes through the normal Windows input path
(`SendInput`).

---

## Risk, up front

This is input automation in an online game.

Valve's August 2024 post *[Side-stepping Skill](https://store.steampowered.com/news/app/730/view/6500469346429600836)*
says they no longer allow "automation (via scripting or hardware)" and that, on
Valve official servers, players suspected of "automating multiple player
actions from a single game input may be kicked from their match". Perfect
counter-strafing is also behaviourally distinguishable from human play
(humans show A+D overlap ticks; automation often does not).

Expected downside: **a match kick**, not a VAC ban. VAC is signature-based;
nothing here is installed into the game or hidden from it. Third-party
platforms (FACEIT, ESEA) are a separate and stricter story.

There is **no evasion in this tool**. No timing jitter to imitate human
overlap, no process hiding, no anti-cheat interaction. If you want those, this
is the wrong project.

---

## Requirements

- Windows
- Python 3.9+ (developed and tested on 3.9). No runtime dependencies.
- `pytest` only if you want to run the tests.

Run it from a normal console window. If the game runs elevated, this must run
elevated too — a lower-integrity hook cannot see keys aimed at a
higher-integrity process. The program warns you at startup if it is not
elevated.

---

## Run

```bat
cd D:\Counter-Strafe
python -m counter_strafe
```

Starts **disarmed**. Press **F8** to arm, **F8** again to disarm. Ctrl+C in the
console (or closing the window) quits and releases any key it was holding down.

Options:

```bat
python -m counter_strafe --dry-run      # see every event, inject nothing
python -m counter_strafe --tap-ms 30    # override tap_ms
python -m counter_strafe --no-log       # console only
python -m counter_strafe --config my.json
```

`--dry-run` is the first thing to run: it proves the hook sees your keys before
you let the tool send anything.

---

## Behaviour

| Situation | Result |
|---|---|
| Release `D` while armed | inject `A` down, hold `tap_ms`, inject `A` up |
| Release `A` while armed | inject `D` down, hold `tap_ms`, inject `D` up |
| The opposite key is already physically down | no tap (`skip`) |
| You press the tapped key during the window | the synthetic key-up is skipped, so your real press is never eaten (`skip`) |
| You press and release the tapped key inside the window | the owed key-up is cancelled, and the tap flips direction (`cancel_up`) |
| You release again inside the window | the running tap is extended, not restarted (`extend`) |
| Disarm, Ctrl+C, or exit | every owed key-up is flushed immediately — no stuck keys |
| Any event flagged `LLKHF_INJECTED` | ignored entirely, so the tool never reacts to its own output |

Only keys listed in `movement_keys` are watched. `w`/`s` are already supported
by the engine (opposite mapping included) but are off by default: an automatic
tap on W/S fights strafe-jumping instead of helping it.

---

## config.json

```json
{
  "tap_ms": 50,
  "hotkey": "f8",
  "movement_keys": ["a", "d"],
  "swallow_hotkey": true,
  "beep_on_toggle": false,
  "log_path": "counter_strafe.jsonl"
}
```

- `tap_ms` — how long the opposite key is held. Integer, 5–1000. Below 5 ms the
  game will not see the tap. 50 is a starting point, not gospel.
- `hotkey` — a letter or `f1`–`f12`.
- `swallow_hotkey` — keep the hotkey from reaching other applications.
- `log_path` — JSONL audit trail; `null` disables it.

Unknown keys are rejected rather than ignored, so a typo cannot silently do
nothing.

`config.json` is optional; missing values fall back to the defaults above.

---

## Manual verification

Unit tests prove the state machine. They cannot prove Windows behaviour. Do
this once, in an **offline bot match**, and do it again after any change to
`win32.py`.

1. `python -m counter_strafe --dry-run` — press and release A/D, confirm the
   console shows `release` lines. If nothing appears, the hook is not seeing
   your keys (usually an elevation mismatch) and nothing else matters.
2. `python -m counter_strafe` — press F8, confirm `arm` appears.
3. Strafe left, release A. You should stop almost immediately instead of
   sliding. Compare against F8-disarmed: the disarmed release should slide.
4. Collision case: hold D, release it, then immediately press A. Your A press
   must keep working — you should keep moving left. If you instead stop dead,
   the key-stealing guard is broken.
5. F8 to disarm, then release A. No tap should occur.
6. Tune: `--tap-ms 30`, `50`, `70`. Ask which one *you* would keep.

`counter_strafe.jsonl` has one record per event (`wall`, `t`, `kind`, `key`,
`detail`) if you want to confirm after the fact whether the tool was armed.

---

## Tests

```bat
python -m pytest
```

Covers the tap in both directions, the opposite-key-held skip, the
press-inside-the-window guard, the cancel/flip case, tap extension, flush on
disarm, F8 repeat suppression, injected-event filtering, config precedence and
validation, and the scheduler thread firing owed key-ups.

The pure/impure seam is the point: `engine.py` makes every decision with
timestamps passed in, so all of the above runs without a keyboard, a clock, or
Windows.

---

## Known limits

- **Other software's synthetic input is ignored.** Anything flagged
  `LLKHF_INJECTED` (AutoHotkey, remappers like Razer/Synapse, on-screen
  keyboards) is treated as non-physical. If your keyboard software remaps A/D
  by injecting, this tool will not see them.
- **No velocity knowledge.** The tool reacts to releases, not to whether you
  were actually moving. Stationary A/D fidgeting will produce taps.
- **50 ms is a guess, not a measurement.** Valve has never published the stop
  time or the accuracy threshold, and community figures disagree (roughly
  50–80 ms for time-to-accurate).
- **Not a driver.** If a game ignores `SendInput` scan codes entirely, no
  amount of Python will fix that; the legitimate options are a hardware
  remapper or an Interception-style driver, both of which change the risk
  profile completely.
- **Python 3.9 is end-of-life.** The code runs there, but upgrading is worth
  doing.

## Non-goals

No tray icon, no GUI, no auto-start, no memory reads, no config hot-reload, no
W/S by default, no randomised timing.

## Layout

```
counter_strafe/
  engine.py      pure state machine (no OS, no clock, no I/O)
  keymap.py      virtual-key + scan-code tables
  config.py      config.json loading and validation
  scheduler.py   tap key-up timer thread
  win32.py       ctypes: hook, SendInput, elevation, beep, console handler
  app.py         wiring and the JSONL/console log
tests/           45 tests, no game required
```
