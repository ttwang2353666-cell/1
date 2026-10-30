# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""守护进程配置：替代 Windows 版的注册表 HKCU\\Software\\OmenSuperHub。

结构：
  current_preset   当前预设
  custom_names     自定义预设名称（None 表示使用默认的本地化名称）
  builtin          内置预设（极致性能 / GPU 优先 / 轻度使用）共用的字段，对应注册表主键
  presets          自定义预设 1~3 的完整字段，对应注册表子键 PresetCustomN
  settings         与预设无关的全局设置
  platform         平台参数覆盖（Windows 版从 HP 平台数据库读取，Linux 版自动探测或手动填写）
"""

import copy
import json
import logging
import os
import threading

from .undervolt import valid_offset

log = logging.getLogger(__name__)

BUILTIN_PRESETS = ("extreme", "gpu", "light")
CUSTOM_PRESETS = ("custom1", "custom2", "custom3")
PRESET_ORDER = BUILTIN_PRESETS + CUSTOM_PRESETS
PRESET_ALIASES = {
    "presetextreme": "extreme", "extreme": "extreme",
    "presetgpupriority": "gpu", "gpu": "gpu", "gpupriority": "gpu",
    "presetlightuse": "light", "light": "light", "lightuse": "light",
    "presetcustom1": "custom1", "custom1": "custom1", "1": "custom1",
    "presetcustom2": "custom2", "custom2": "custom2", "2": "custom2",
    "presetcustom3": "custom3", "custom3": "custom3", "3": "custom3",
}

# 预设字段（风扇配置、风扇控制、性能控制）
PRESET_FIELDS = {
    "fan_table": "cool",          # silent / cool / custom
    "fan_control": "auto",        # auto / max / 整数 RPM
    "temp_sensitivity": "high",   # realtime / high / medium / low
    "cpu_power": None,            # None 不设置 / 整数 W（PL1+PL2）
    "tpp": None,                  # None 不设置 / 整数 W（PPab 条件）
    "tgp": True,
    "ppab": True,
    "dstate": "normal",           # normal / low
    "iccmax": None,               # None / 整数 A
    "ac_loadline": None,          # None / 档位
    "gpu_core_oc": None,          # None 不设置 / MHz
    "gpu_mem_oc": None,           # None 不设置 / MHz
    "gpu_clock": 0,               # 0 无限制 / MHz
}
# 硬件监控字段：内置预设存于 builtin，自定义预设各自保存
MONITOR_FIELDS = {
    "monitor_cpu": True,
    "monitor_gpu": True,
    "monitor_fan": False,
    "refresh_rate": "low",        # low=1s / high=0.25s
    "temp_display": "smoothed",   # smoothed / raw
    "show_cpu_temp": True,
    "show_cpu_power": True,
    "show_cpu_freq": False,
    "show_gpu_temp": True,
    "show_gpu_power": True,
    "show_gpu_freq": False,
}
ALL_PRESET_FIELDS = {**PRESET_FIELDS, **MONITOR_FIELDS}

SETTINGS_DEFAULTS = {
    "auto_fan_protect": True,
    "thermal_mode": "unleash",    # OSH 固定为释放全部性能；system 表示不接管
    "omen_key": "default",        # default / overlay / preset / app / shortcut（应用由各用户的托盘程序配置）
    "omen_key_shortcut": "",      # 例如 Ctrl+Alt+T
    "omen_key_presets": [],       # 切换预设的候选，空表示全部可用预设
    "data_localize": False,
    "kb_protocol": "basic",       # basic / dojo
    "lb_protocol": "dojo",
    "light_anim": {"effect": 2, "speed": 1, "direction": 0, "theme": 0},
    "undervolt": None,            # CPU 降压：None 不设置 / 负整数 mV（核心与缓存相同）
}

PLATFORM_DEFAULTS = {
    "backend": "auto",            # auto / acpi_call / hp-wmi / mock
    "acpi_path": None,            # BIOS 方法路径，None 为自动探测
    "max_fan_rpm": None,          # 平台最高转速，None 为读取 BIOS 风扇表
    "cpu_temp_limit": None,       # CPU 温度墙，None 为读取 coretemp
    "gpu_temp_limit": None,
    "cpu_power_control": "auto",  # auto / on / off
    "tpp_support": "auto",
    "iccmax_support": False,      # 平台数据库缺失，默认隐藏
    "pl1_performance": 160,       # NbPL1UpperBoundPerformance
    "pl1_default": 55,            # NbPL1UpperBoundDefault
    "clean_creek_level": None,    # 反转除尘转速（100 RPM），None 为自动
    "clean_creek_seconds": 30,
    "sync_platform_profile": True,
    "allowed_groups": ["omenhub", "wheel", "sudo", "admin"],
}


def builtin_preset_fields(preset: str, pl1_performance: int, pl1_default: int) -> dict:
    """applyPresetLogic 中内置预设的默认值。"""
    fields = dict(PRESET_FIELDS)
    fields.update(fan_table="cool", fan_control="auto", temp_sensitivity="high",
                  tgp=True, ppab=True, dstate="normal",
                  gpu_core_oc=120, gpu_mem_oc=400 if preset == "extreme" else 0,
                  gpu_clock=0, iccmax=None, ac_loadline=None)
    if preset == "extreme":
        fields.update(cpu_power=pl1_performance, tpp=pl1_performance)
    elif preset == "gpu":
        fields.update(cpu_power=pl1_default, tpp=pl1_performance)
    elif preset == "light":
        fields.update(fan_table="silent", cpu_power=int(pl1_default * 0.6), tpp=None,
                      tgp=False, ppab=False)
    return fields


def normalize_preset(name) -> str:
    key = str(name or "").strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    if key in PRESET_ALIASES:
        return PRESET_ALIASES[key]
    raise ValueError(f"unknown preset: {name}")


def _coerce_value(key: str, value, default):
    """把磁盘上的值规整为预期类型，非法值回落到默认值。"""
    if key == "fan_control":
        if value in ("auto", "max"):
            return value
        try:
            rpm = int(str(value).replace("RPM", "").strip())
            return rpm if 0 <= rpm <= 12000 else default
        except ValueError:
            return default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if key == "undervolt":
        return value if valid_offset(value) else None
    if key in ("cpu_power", "tpp", "iccmax", "ac_loadline", "gpu_core_oc", "gpu_mem_oc"):
        if value is None:
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return value if isinstance(value, list) else default
    if isinstance(default, dict):
        return value if isinstance(value, dict) else default
    return value


def _merge(defaults: dict, stored) -> dict:
    stored = stored if isinstance(stored, dict) else {}
    result = {}
    for key, default in defaults.items():
        result[key] = _coerce_value(key, stored.get(key, default), copy.deepcopy(default))
    return result


class Config:
    """线程安全的 JSON 配置存储，原子写入。"""

    def __init__(self, path: str):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._defaults()
        self.exists = False

    @staticmethod
    def _defaults() -> dict:
        return {
            "version": 1,
            "current_preset": None,
            "custom_names": {name: None for name in CUSTOM_PRESETS},
            "builtin": dict(ALL_PRESET_FIELDS),
            "presets": {},
            "settings": copy.deepcopy(SETTINGS_DEFAULTS),
            "platform": copy.deepcopy(PLATFORM_DEFAULTS),
        }

    def load(self) -> "Config":
        with self.lock:
            raw = {}
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self.exists = True
            except FileNotFoundError:
                self.exists = False
            except (OSError, ValueError) as exc:
                log.error("cannot read %s (%s), using defaults", self.path, exc)
                self.exists = False
            data = self._defaults()
            if isinstance(raw, dict):
                current = raw.get("current_preset")
                data["current_preset"] = current if current in PRESET_ORDER else None
                names = raw.get("custom_names") if isinstance(raw.get("custom_names"), dict) else {}
                for name in CUSTOM_PRESETS:
                    value = names.get(name)
                    data["custom_names"][name] = value if isinstance(value, str) and value.strip() else None
                data["builtin"] = _merge(ALL_PRESET_FIELDS, raw.get("builtin"))
                presets = raw.get("presets") if isinstance(raw.get("presets"), dict) else {}
                for name in CUSTOM_PRESETS:
                    if isinstance(presets.get(name), dict):
                        data["presets"][name] = _merge(ALL_PRESET_FIELDS, presets[name])
                data["settings"] = _merge(SETTINGS_DEFAULTS, raw.get("settings"))
                data["platform"] = _merge(PLATFORM_DEFAULTS, raw.get("platform"))
            self.data = data
            return self

    def save(self) -> None:
        with self.lock:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.path)
            self.exists = True

    # ---------------------------------------------------------------- 便捷访问
    @property
    def settings(self) -> dict:
        return self.data["settings"]

    @property
    def platform(self) -> dict:
        return self.data["platform"]

    def preset_store(self, preset: str) -> dict:
        """当前预设的字段存储位置：内置预设共用 builtin，自定义预设各自独立。"""
        if preset in BUILTIN_PRESETS:
            return self.data["builtin"]
        return self.data["presets"].setdefault(preset, dict(self.data["builtin"]))

    def has_custom_preset(self, preset: str) -> bool:
        return preset in self.data["presets"]
