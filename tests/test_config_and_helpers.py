import json

import pytest

from omenhub import config, display, i18n, omenkey
from omenhub.config import Config, builtin_preset_fields, normalize_preset


def test_config_defaults_and_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    cfg = Config(str(path)).load()
    assert not cfg.exists and cfg.data["current_preset"] is None
    cfg.data["current_preset"] = "custom2"
    cfg.preset_store("custom2")["cpu_power"] = 70
    cfg.settings["omen_key"] = "preset"
    cfg.save()
    loaded = Config(str(path)).load()
    assert loaded.exists and loaded.data["current_preset"] == "custom2"
    assert loaded.data["presets"]["custom2"]["cpu_power"] == 70
    assert loaded.settings["omen_key"] == "preset"


def test_config_coerces_bad_values(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "current_preset": "bogus",
        "builtin": {"fan_control": "3000 RPM", "cpu_power": "lots", "tgp": "yes", "monitor_fan": True},
        "presets": {"custom1": {"fan_control": 99999}, "evil": {}},
        "settings": {"omen_key_presets": "x", "auto_fan_protect": False},
    }))
    cfg = Config(str(path)).load()
    builtin = cfg.data["builtin"]
    assert cfg.data["current_preset"] is None
    assert builtin["fan_control"] == 3000          # OSH 旧格式 "3000 RPM"
    assert builtin["cpu_power"] is None and builtin["tgp"] is True and builtin["monitor_fan"] is True
    assert cfg.data["presets"]["custom1"]["fan_control"] == "auto"
    assert "evil" not in cfg.data["presets"]
    assert cfg.settings["omen_key_presets"] == [] and cfg.settings["auto_fan_protect"] is False


def test_corrupt_config_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    assert Config(str(path)).load().data["settings"] == config.SETTINGS_DEFAULTS


def test_builtin_preset_fields_match_osh():
    extreme = builtin_preset_fields("extreme", 160, 55)
    assert (extreme["cpu_power"], extreme["tpp"], extreme["gpu_core_oc"], extreme["gpu_mem_oc"]) == (160, 160, 120, 400)
    gpu = builtin_preset_fields("gpu", 160, 55)
    assert (gpu["cpu_power"], gpu["tpp"], gpu["gpu_mem_oc"]) == (55, 160, 0)
    light = builtin_preset_fields("light", 160, 55)
    assert (light["fan_table"], light["cpu_power"], light["tpp"], light["tgp"], light["ppab"]) == \
        ("silent", 33, None, False, False)


@pytest.mark.parametrize("name,expected", [("PresetExtreme", "extreme"), ("gpu", "gpu"), ("Light-Use", "light"),
                                           ("PresetCustom2", "custom2"), ("3", "custom3")])
def test_normalize_preset(name, expected):
    assert normalize_preset(name) == expected


def test_normalize_preset_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_preset("turbo")


@pytest.mark.parametrize("text,codes", [("Ctrl+Alt+T", [29, 56, 20]), ("win+shift+s", [125, 42, 31]),
                                        ("F12", [88]), ("Ctrl+PrintScreen", [29, 99]), ("Super+-", [125, 12])])
def test_parse_shortcut(text, codes):
    assert omenkey.parse_shortcut(text) == codes


@pytest.mark.parametrize("text", ["", "Ctrl", "Ctrl+A+B", "Ctrl+Nope"])
def test_parse_shortcut_invalid(text):
    with pytest.raises(ValueError):
        omenkey.parse_shortcut(text)


def test_find_hotkey_device(tmp_path, monkeypatch):
    devices = tmp_path / "devices"
    devices.write_text('I: Bus=0011 Vendor=0001 Product=0001 Version=ab83\nN: Name="AT Translated Set 2 keyboard"\n'
                       'H: Handlers=sysrq kbd event3 leds\n\n'
                       'I: Bus=0019 Vendor=0000 Product=0000 Version=0000\nN: Name="HP WMI hotkeys"\n'
                       'P: Phys=wmi/input0\nH: Handlers=kbd event12 \n\n')
    real_open = open
    monkeypatch.setattr("builtins.open", lambda path, *a, **k: real_open(
        devices if path == "/proc/bus/input/devices" else path, *a, **k))
    assert omenkey.find_hotkey_device() == "/dev/input/event12"


def test_monitor_lines_formats():
    i18n.set_language("zh-CN")
    status = {"cpu": {"temp": 65.24, "power": 45.6, "freq": 3900.0, "ready": True},
              "gpu": {"state": "active", "temp": 55.0, "power": 80.04, "freq": 1800.0, "ready": True},
              "fan": {"levels": [30, 32]}}
    fields = {"monitor_cpu": True, "monitor_gpu": True, "monitor_fan": True, "show_cpu_temp": True,
              "show_cpu_power": True, "show_cpu_freq": True, "show_gpu_temp": True, "show_gpu_power": True,
              "show_gpu_freq": False}
    caps = {"gpu": {"kind": "nvidia"}}
    assert display.monitor_lines(status, fields, caps) == ["CPU: 65.2°C, 45.6W, 3.9GHz", "GPU: 55.0°C, 80.0W",
                                                          "Fan:  3000, 3200"]
    assert display.monitor_lines(status, fields, caps, compact=True) == ["CPU 65°C 46W 3.9G", "GPU 55°C 80W",
                                                                        "Fan 3000 3200"]
    status["gpu"] = {"state": "off", "ready": False}
    assert display.monitor_lines(status, fields, caps)[1] == "GPU: 节能"
    off = dict(fields, monitor_cpu=False, monitor_gpu=False, monitor_fan=False)
    assert display.monitor_lines(status, off, caps) == ["监控已关闭"]


def test_i18n_languages_and_formatting():
    for lang in i18n.LANGUAGES:
        i18n.set_language(lang)
        assert i18n.tr("high_temp_text", limit=98, temp=99.26)
        assert "{" not in i18n.tr("gfx_switched_to", mode="UMA")
    i18n.set_language("en")
    assert i18n.tr("clean_creek_progress", 12) == "Cleaning in progress, 12 seconds remaining..."
    assert i18n.tr("no_such_key") == "no_such_key"
    i18n.set_language("zh-CN")


def test_i18n_entries_have_three_languages():
    for key, entry in i18n._S.items():
        assert len(entry) == 3 and all(entry), key


@pytest.mark.parametrize("env,expected", [("zh_CN.UTF-8", "zh-CN"), ("zh_TW.UTF-8", "zh-TW"), ("zh_HK", "zh-TW"),
                                          ("en_US.UTF-8", "en"), ("de_DE.UTF-8", "en")])
def test_detect_system_language(monkeypatch, env, expected):
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LANG", env)
    assert i18n.detect_system_language() == expected


def test_undervolt_lines():
    i18n.set_language("en")
    assert display.undervolt_lines({"supported": False, "reason": "lockdown"}) == [i18n.tr("undervolt_reason_lockdown")]
    payload = {"supported": True, "current": {"core": -110.35, "cache": -110.35},
               "baseline": {"core": 0.0, "cache": 0.0}, "suspended": True, "error": None}
    assert display.undervolt_lines(payload) == [i18n.tr("undervolt_suspended"), "Current: core -110 mV, cache -110 mV",
                                                "At boot: core 0 mV, cache 0 mV"]
    assert display.undervolt_lines({"supported": True, "error": "rejected"}) == [i18n.tr("undervolt_rejected"),
                                                                                "Current offsets not read yet"]
    i18n.set_language("zh-CN")


def test_config_coerces_undervolt(tmp_path):
    path = tmp_path / "config.json"
    for stored, expected in ((-80, -80), (-500, None), (20, None), ("-80", None), (None, None)):
        path.write_text(json.dumps({"settings": {"undervolt": stored}}))
        assert Config(str(path)).load().settings["undervolt"] == expected
