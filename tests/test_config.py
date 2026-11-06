import json

import pytest

from counter_strafe import config


def write_cfg(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_defaults_when_file_absent(tmp_path):
    cfg = config.load(str(tmp_path / "missing.json"))

    assert cfg.tap_ms == 50
    assert cfg.hotkey == "f8"
    assert cfg.movement_keys == ("a", "d")
    assert cfg.swallow_hotkey is True
    assert cfg.beep_on_toggle is False
    assert cfg.log_path == "counter_strafe.jsonl"


def test_file_values_apply(tmp_path):
    path = write_cfg(
        tmp_path,
        {"tap_ms": 70, "hotkey": "F9", "movement_keys": ["A", "D"], "beep_on_toggle": True},
    )
    cfg = config.load(path)

    assert cfg.tap_ms == 70
    assert cfg.hotkey == "f9"
    assert cfg.movement_keys == ("a", "d")
    assert cfg.beep_on_toggle is True


def test_overrides_beat_file(tmp_path):
    path = write_cfg(tmp_path, {"tap_ms": 70, "hotkey": "f9"})
    cfg = config.load(path, {"tap_ms": 30})

    assert cfg.tap_ms == 30
    assert cfg.hotkey == "f9"


def test_override_disables_logging(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # do not pick up a real config.json from the repo
    cfg = config.load(None, {"log_path": None})
    assert cfg.log_path is None
    assert cfg.tap_ms == 50


def test_logging_can_be_disabled(tmp_path):
    path = write_cfg(tmp_path, {"log_path": None})
    assert config.load(path).log_path is None


def test_unknown_setting_is_an_error(tmp_path):
    path = write_cfg(tmp_path, {"tap_ms": 50, "tap_msx": 20})
    with pytest.raises(config.ConfigError, match="unknown setting"):
        config.load(path)


def test_unknown_override_is_an_error():
    with pytest.raises(config.ConfigError, match="unknown setting"):
        config.load(None, {"nope": 1})


@pytest.mark.parametrize("tap_ms", [1, 4, 1001, 0, -50, True, "50", 50.0, None])
def test_bad_tap_ms(tmp_path, tap_ms):
    path = write_cfg(tmp_path, {"tap_ms": tap_ms})
    with pytest.raises(config.ConfigError, match="tap_ms"):
        config.load(path)


def test_bad_movement_key(tmp_path):
    path = write_cfg(tmp_path, {"movement_keys": ["a", "q"]})
    with pytest.raises(config.ConfigError, match="unsupported movement key"):
        config.load(path)


def test_empty_movement_keys(tmp_path):
    path = write_cfg(tmp_path, {"movement_keys": []})
    with pytest.raises(config.ConfigError, match="non-empty"):
        config.load(path)


def test_duplicate_movement_key(tmp_path):
    path = write_cfg(tmp_path, {"movement_keys": ["a", "a"]})
    with pytest.raises(config.ConfigError, match="duplicate"):
        config.load(path)


def test_hotkey_must_be_known(tmp_path):
    path = write_cfg(tmp_path, {"hotkey": "hyper"})
    with pytest.raises(config.ConfigError, match="hotkey must be one of"):
        config.load(path)


def test_hotkey_cannot_be_movement_key(tmp_path):
    path = write_cfg(tmp_path, {"hotkey": "a"})
    with pytest.raises(config.ConfigError, match="movement key"):
        config.load(path)


def test_bad_bool(tmp_path):
    path = write_cfg(tmp_path, {"swallow_hotkey": "yes"})
    with pytest.raises(config.ConfigError, match="swallow_hotkey"):
        config.load(path)


def test_bad_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(config.ConfigError):
        config.load(str(path))


def test_bad_log_path(tmp_path):
    path = write_cfg(tmp_path, {"log_path": ""})
    with pytest.raises(config.ConfigError, match="log_path"):
        config.load(path)


def test_mouse_defaults_disabled(tmp_path):
    path = write_cfg(tmp_path, {"tap_ms": 50})
    cfg = config.load(path)

    assert cfg.mouse_enabled is False
    assert cfg.mouse_tap_threshold_ms == 200
    assert cfg.mouse_burst_threshold_ms == 2000
    assert cfg.mouse_burst_count == 3
    assert cfg.mouse_burst_interval_ms == 100


def test_mouse_settings_apply_from_file(tmp_path):
    path = write_cfg(
        tmp_path,
        {
            "mouse_enabled": True,
            "mouse_tap_threshold_ms": 100,
            "mouse_burst_threshold_ms": 1500,
            "mouse_burst_count": 5,
            "mouse_burst_interval_ms": 80,
        },
    )
    cfg = config.load(path)

    assert cfg.mouse_enabled is True
    assert cfg.mouse_tap_threshold_ms == 100
    assert cfg.mouse_burst_threshold_ms == 1500
    assert cfg.mouse_burst_count == 5
    assert cfg.mouse_burst_interval_ms == 80


def test_mouse_burst_threshold_must_exceed_tap_threshold(tmp_path):
    path = write_cfg(
        tmp_path,
        {
            "mouse_tap_threshold_ms": 500,
            "mouse_burst_threshold_ms": 500,
        },
    )
    with pytest.raises(config.ConfigError, match="exceed"):
        config.load(path)


def test_mouse_bad_bool(tmp_path):
    path = write_cfg(tmp_path, {"mouse_enabled": "yes"})
    with pytest.raises(config.ConfigError, match="mouse_enabled"):
        config.load(path)


def test_mouse_bad_thresholds(tmp_path):
    for key in (
        "mouse_tap_threshold_ms",
        "mouse_burst_threshold_ms",
        "mouse_burst_count",
        "mouse_burst_interval_ms",
    ):
        path = write_cfg(tmp_path, {key: "50"})
        with pytest.raises(config.ConfigError, match="integer"):
            config.load(path)
