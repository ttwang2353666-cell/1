# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""omenhubd：以 root 运行的后台服务。

对应 Windows 版 Program.cs / Program.Config.cs 中与界面无关的全部控制逻辑：
预设与配置恢复、软件风扇曲线、BIOS 心跳、交流电源 / 睡眠恢复后重新应用设置、
高温自动保护、反转除尘、Omen 键、数据本地化。图形界面与命令行通过 Unix 套接字控制它。
"""

import argparse
import asyncio
import json
import logging
import math
import os
import platform as py_platform
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import UPSTREAM_VERSION, __version__, nvidia, paths, sensors, undervolt
from .config import (BUILTIN_PRESETS, CUSTOM_PRESETS, MONITOR_FIELDS, PRESET_FIELDS, PRESET_ORDER, Config,
                     builtin_preset_fields, normalize_preset)
from .fancurve import FAN_TABLES, FanCurveProfile, InvalidFanCurve, create_default_profile, interpolate
from .hardware import (GFX_MODE_NAMES, GFX_SUPPORT_BITS, IFACE_BASIC, IFACE_DOJO, KB_NONE, LIGHT_KEYBOARD,
                       LIGHT_LIGHTBAR, OmenBios, validate_product)
from .ipc import (E_DENIED, E_FAILED, E_INVALID, E_UNKNOWN, E_UNSUPPORTED, READ_ONLY_COMMANDS, IpcError, encode,
                  is_authorized, peer_credentials)
from .omenkey import OmenKeyListener, VirtualKeyboard, parse_shortcut
from .wmi import MockBios, WmiError, create_backend

log = logging.getLogger("omenhubd")

RESPOND_SPEED = {"realtime": 1.0, "high": 0.4, "medium": 0.1, "low": 0.04}
OMEN_KEY_ACTIONS = ("default", "overlay", "preset", "app", "shortcut")
THERMAL_MODES = ("unleash", "performance", "balance", "default", "cool", "quiet", "eco", "system")
SHOW_KEYS = tuple(k for k in MONITOR_FIELDS if k.startswith("show_"))
# 只能通过 BIOS 生效的字段；没有可用 BIOS 接口时拒绝修改
BIOS_FIELDS = ("fan_control", "cpu_power", "tpp", "tgp", "ppab", "dstate", "iccmax", "ac_loadline")
_PROFILE_FOR_MODE = {"unleash": ("performance",), "performance": ("performance",), "extreme": ("performance",),
                     "balance": ("balanced",), "default": ("balanced",), "eco": ("low-power", "balanced"),
                     "cool": ("cool", "low-power", "quiet"), "quiet": ("quiet", "low-power", "cool")}
PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
_UV_FROM_SETTINGS = object()  # 让 _uv_apply_sync 在锁内读取当前的降压设置


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _resolve_flag(value, auto: bool) -> bool:
    if value in (True, "on", "true", "yes"):
        return True
    if value in (False, "off", "false", "no"):
        return False
    return auto


class Daemon:
    def __init__(self, config_path: str = None, socket_path: str = None, backend: str = None,
                 mock: bool = False):
        self.config_path = config_path or paths.CONFIG_PATH
        self.config = Config(self.config_path).load()
        self.state_dir = os.path.dirname(self.config_path) or "."
        self.socket_path = socket_path or paths.SOCKET_PATH
        self.runtime_dir = os.path.dirname(self.socket_path) or "."
        self.backend_kind = backend or self.config.platform["backend"]
        self.mock = mock or self.backend_kind == "mock"
        self.loop = None
        self.stop_event = None
        self.bios_pool = ThreadPoolExecutor(1, "bios")
        self.io_pool = ThreadPoolExecutor(2, "io")
        self.bios = None
        self.mock_bios = None
        self.backend_error = None
        self.cpu_sensor = None
        self.gpu_monitor = None
        self.subscribers = set()
        self.connections = set()
        self.tasks = []
        self.info = {}
        self.caps = {}
        self.fields = dict(self.config.data["builtin"])
        self.cpu_map, self.gpu_map = [], []
        # 运行时状态（对应 Program.cs 中的静态字段）
        self.cpu = {"temp": None, "raw_temp": None, "power": None, "freq": None, "ready": False}
        self.gpu = {"state": "none", "temp": None, "raw_temp": None, "power": None, "freq": None,
                    "ready": False, "fan_ready": False}
        self.gpu_sample = None
        self.smoothed_cpu, self.smoothed_gpu = 50.0, 40.0
        self.fitting_temp = None
        self.fan_levels = [-10, -10, 0]  # 首次进入自动模式时必定下发一次转速
        self.cleaning_until = 0.0
        self.cleaning_task = None
        self.heartbeat_count = 0
        self.restore_countdown = 0
        self.ac = sensors.ac_online()
        self.resume_detector = sensors.ResumeDetector()
        self.gpu_runtime_prev = None
        self.omen_key = None
        self.vkbd = None
        self.powerd = "missing"
        self.gpu_power_limits = None
        self.platform_max_rpm = None
        self.cpu_temp_limit = None
        self.gpu_temp_limit = None
        self._last_texts = {}
        # CPU 降压：本次开机的状态（见 _init_undervolt_state）、最近读到的偏移与错误
        self.uv_box = None
        self.uv_lock = threading.Lock()
        self.uv_state = {"boot_id": "", "baseline": None, "applied": False, "clean": True, "suspended": False}
        self.uv_current = None
        self.uv_error = None

    # ================================================================== 基础设施
    async def bios_call(self, func, *args):
        return await self.loop.run_in_executor(self.bios_pool, func, *args)

    async def io_call(self, func, *args):
        return await self.loop.run_in_executor(self.io_pool, func, *args)

    def spawn(self, coro):
        task = self.loop.create_task(coro)
        self.tasks.append(task)
        task.add_done_callback(lambda t: self.tasks.remove(t) if t in self.tasks else None)
        return task

    def broadcast(self, event: str, data) -> None:
        if not self.subscribers:
            return
        payload = encode({"event": event, "data": data})
        for writer in list(self.subscribers):
            try:
                if writer.transport.get_write_buffer_size() > 1 << 20:
                    raise ConnectionError("client too slow")
                writer.write(payload)
            except (ConnectionError, RuntimeError, AttributeError):
                self.subscribers.discard(writer)
                writer.close()

    def notify(self, kind: str, **data) -> None:
        self.broadcast("notify", {"kind": kind, **data})

    def broadcast_state(self) -> None:
        self.broadcast("state", self.state_payload())

    @property
    def settings(self) -> dict:
        return self.config.settings

    @property
    def current_preset(self) -> str:
        return self.config.data["current_preset"] or "custom1"

    # ================================================================== 初始化
    async def init_hardware(self) -> None:
        dmi = await self.io_call(sensors.read_dmi)
        feature_byte = await self.io_call(sensors.read_feature_byte)
        cpu = await self.io_call(sensors.cpu_info)
        platform_cfg = self.config.platform
        if self.mock:
            dmi.update(sys_vendor="HP", product_name="OMEN by HP Gaming Laptop 16-wf0xxx (mock)",
                       board_name="8BAD", bios_version="F.25")
            cpu["model"] = cpu.get("model") or "Mock CPU"
            self.mock_bios = MockBios()
            backend = self.mock_bios
        else:
            try:
                backend = await self.io_call(create_backend, self.backend_kind, platform_cfg["acpi_path"],
                                             (platform_cfg["max_fan_rpm"] or 6400) // 100)
            except WmiError as exc:
                backend = None
                self.backend_error = str(exc)
                log.error("BIOS backend unavailable: %s", exc)
        board = dmi.get("board_name", "")
        if backend is not None:
            self.bios = OmenBios(backend, board)

        if self.mock:
            self.cpu_sensor = sensors.MockSensors(self.mock_bios)
        else:
            self.cpu_sensor = sensors.CpuSensor()
            self.gpu_monitor = sensors.create_gpu_monitor()

        caps = await self.bios_call(self._probe_bios) if self.bios else {}
        gpu = await self._probe_gpu()

        validation = validate_product(dmi.get("product_name"), dmi.get("sys_vendor"), board, feature_byte)
        gaming = validation == "gaming"
        fan_range = caps.get("fan_table_range")
        max_rpm = platform_cfg["max_fan_rpm"] if _is_int(platform_cfg["max_fan_rpm"]) else None
        if not max_rpm and fan_range:
            max_rpm = fan_range[1] * 100
        self.platform_max_rpm = max_rpm
        tjmax = getattr(self.cpu_sensor, "tjmax", None)
        self.cpu_temp_limit = platform_cfg["cpu_temp_limit"] if _is_int(platform_cfg["cpu_temp_limit"]) else tjmax
        gpu_target = gpu.get("target_temp") if gpu else None
        self.gpu_temp_limit = platform_cfg["gpu_temp_limit"] if _is_int(platform_cfg["gpu_temp_limit"]) else gpu_target
        has_bios = self.bios is not None
        cpu_power = has_bios and _resolve_flag(platform_cfg["cpu_power_control"], gaming)
        clean_level = platform_cfg["clean_creek_level"]
        if not _is_int(clean_level):
            clean_level = min(fan_range[1], 50) if fan_range else 40
        caps.update({
            "bios": has_bios,
            "cpu_power_control": cpu_power,
            "tpp": has_bios and _resolve_flag(platform_cfg["tpp_support"], cpu_power and gpu is not None),
            "iccmax": has_bios and bool(platform_cfg["iccmax_support"]),
            "max_fan_rpm": max_rpm,
            "fan_slider_max": int(max_rpm * 1.1 / 100) * 100 if max_rpm else 6400,
            "cpu_temp_limit": self.cpu_temp_limit,
            "gpu_temp_limit": self.gpu_temp_limit,
            "cpu_sensor": bool(getattr(self.cpu_sensor, "available", False)),
            "gpu": gpu,
            "clean_creek_level": clean_level,
            "clean_creek_seconds": max(5, min(600, int(platform_cfg["clean_creek_seconds"] or 30))),
            "pl1_performance": int(platform_cfg["pl1_performance"] or 160),
            "pl1_default": int(platform_cfg["pl1_default"] or 55),
            "uinput": os.path.exists("/dev/uinput"),
        })
        uv_reason = None if self.mock else undervolt.unsupported_reason(cpu.get("vendor", ""))
        caps["undervolt"] = uv_reason is None
        caps["undervolt_reason"] = uv_reason
        await self.io_call(self._init_undervolt_state)
        caps.setdefault("fan_count", 2)
        caps.setdefault("perf_modes", [])
        self.caps = caps
        self.info = {
            "version": __version__,
            "upstream_version": UPSTREAM_VERSION,
            "mock": self.mock,
            "device": {
                "product_name": dmi.get("product_name", ""),
                "sys_vendor": dmi.get("sys_vendor", ""),
                "board": board,
                "sku": dmi.get("product_sku", ""),
                "bios_version": dmi.get("bios_version", ""),
                "bios_date": dmi.get("bios_date", ""),
                "cpu_model": cpu.get("model", ""),
                "cpu_vendor": cpu.get("vendor", ""),
                "kernel": py_platform.release(),
            },
            "validation": validation,
            "backend": {**(backend.info() if backend else {"name": "none"}), "error": self.backend_error},
            "caps": caps,
        }
        log.info("device: %s (board %s), validation=%s, backend=%s", dmi.get("product_name"), board,
                 validation, self.info["backend"].get("name"))

    def _probe_bios(self) -> dict:
        """启动时探测硬件能力（Program.Main 中并行执行的那些查询）。"""
        bios = self.bios
        caps = {}
        design = bios.parse_system_design_data()
        caps["design"] = design
        caps["adapter_power"] = bios.get_adapter_power()
        caps["thermal_policy"] = bios.get_thermal_policy_version()
        caps["perf_modes"] = bios.get_supported_performance_modes()
        types, _ = bios.get_fan_type()
        caps["fan_types"] = types
        caps["three_fan"] = bios.is_three_fan_supported()
        caps["fan_count"] = 3 if caps["three_fan"] else 2
        if bios.is_clean_creek_supported():
            caps["clean_creek"] = "new"
        elif bios.is_legacy_clean_creek_supported():
            caps["clean_creek"] = "legacy"
        else:
            caps["clean_creek"] = None
        caps["fan_table_range"] = bios.get_fan_table_range()
        caps["kb_type"] = bios.get_keyboard_type()
        caps["gfx_mode"] = bios.get_gfx_mode()
        caps["gfx_supported"] = bios.get_supported_gfx_modes()
        caps["loadline_levels"] = bios.get_loadline_levels() if bios.is_loadline_supported() else 0
        ir, ambient = bios.get_sensor_temperature(0), bios.get_sensor_temperature(1)
        caps["ambient_sensor"] = ambient > 1 and ir != ambient
        caps["lightbar"] = bios.is_lightbar_platform()
        caps["two_byte_pl4"] = bios.is_two_byte_pl4_supported()
        bios.get_fan_count()
        return caps

    async def _probe_gpu(self):
        if self.mock:
            self.gpu_power_limits = (115.0, 140.0)
            return {"kind": "nvidia", "name": self.cpu_sensor.name, "target_temp": 87, "max_clock": 2370,
                    "power_limits": list(self.gpu_power_limits), "pci": None}
        monitor = self.gpu_monitor
        if monitor is None:
            return None
        gpu = {"kind": monitor.kind, "name": "", "target_temp": None, "max_clock": 0, "power_limits": None,
               "pci": monitor.pci}
        if monitor.kind == "nvidia":
            gpu["name"] = await self.io_call(nvidia.gpu_name) or "NVIDIA GPU"
            gpu["target_temp"] = await self.io_call(nvidia.target_temperature)
            gpu["max_clock"] = await self.io_call(nvidia.max_graphics_clock)
            self.gpu_power_limits = await self.io_call(nvidia.power_limits)
            gpu["power_limits"] = list(self.gpu_power_limits) if self.gpu_power_limits else None
            self.powerd = await self.io_call(nvidia.powerd_status)
        else:
            gpu["name"] = sensors.read_file(os.path.join(monitor.pci, "product_name")) or "AMD Radeon GPU"
        monitor.name = gpu["name"]
        return gpu

    # ================================================================== 风扇曲线
    def fan_curve_path(self, table: str) -> str:
        return os.path.join(self.state_dir, f"{table}.txt")

    def default_fan_curve(self, table: str) -> FanCurveProfile:
        return create_default_profile(table == "silent", self.platform_max_rpm, self.cpu_temp_limit,
                                      self.gpu_temp_limit)

    def read_fan_curve(self, table: str) -> FanCurveProfile:
        """LoadFanConfig：文件缺失或损坏时生成默认曲线；旧 CSV 格式自动转换为新格式。"""
        path = self.fan_curve_path(table)
        if table == "custom" and not os.path.exists(path):
            # ApplyCustomFanConfig：custom.txt 不存在时复制 cool.txt
            self.read_fan_curve("cool").save(path)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
            profile = FanCurveProfile.loads(text)
            if "=" not in text:
                profile.save(path)
            return profile
        except FileNotFoundError:
            pass
        except (OSError, InvalidFanCurve) as exc:
            log.error("%s invalid (%s), regenerating", path, exc)
        profile = self.default_fan_curve(table)
        try:
            os.makedirs(self.state_dir, exist_ok=True)
            profile.save(path)
        except OSError as exc:
            log.error("cannot write %s: %s", path, exc)
        return profile

    def load_fan_curve(self, table: str) -> None:
        profile = self.read_fan_curve(table if table in FAN_TABLES else "cool")
        self.cpu_map, self.gpu_map = profile.cpu, profile.gpu

    def fan_speed_for_temperature(self) -> int:
        """GetFanSpeedForTemperature：CPU / GPU 曲线查表取较大值，单位 RPM；-100 表示暂不控制。"""
        if not self.cpu_map or not self.gpu_map:
            return -100
        result = -100
        if self.fields["monitor_cpu"] and self.cpu["ready"]:
            result = max(result, interpolate(self.cpu_map, self.smoothed_cpu))
        if self.fields["monitor_gpu"] and self.gpu["fan_ready"]:
            result = max(result, interpolate(self.gpu_map, self.smoothed_gpu))
        # 获取不到 CPU 温度时使用 BIOS 环境传感器拟合
        if self.fields["monitor_cpu"] and not self.cpu["ready"] and self.fitting_temp is not None:
            result = max(result, interpolate(self.cpu_map, self.fitting_temp))
        return result

    # ================================================================== 应用设置
    async def restore_config(self) -> None:
        """RestoreConfig：启动 / 睡眠恢复时把当前预设重新应用到硬件。"""
        current = self.config.data["current_preset"]
        # 与 OSH 启动时的 SetUnleashMode() 一样，先设置 BIOS 性能模式
        await self.apply_thermal_mode()
        if not self.config.exists or current is None:
            await self.apply_preset("extreme" if self.caps.get("cpu_power_control") else "custom1")
            return
        if current in BUILTIN_PRESETS and not self.caps.get("cpu_power_control"):
            await self.apply_preset("custom1")
            return
        store = self.config.data["builtin"] if current in BUILTIN_PRESETS else self.config.preset_store(current)
        self.fields = dict(store)
        await self.apply_settings()
        self.broadcast_state()

    async def apply_preset(self, preset: str) -> None:
        """applyPresetLogic：内置预设写入默认值，自定义预设读取保存的值，然后应用到硬件。"""
        if preset in BUILTIN_PRESETS and not self.caps.get("cpu_power_control"):
            raise IpcError("built-in presets need CPU power control", E_UNSUPPORTED)
        data = self.config.data
        data["current_preset"] = preset
        if preset in BUILTIN_PRESETS:
            self.fields.update(builtin_preset_fields(preset, self.caps["pl1_performance"], self.caps["pl1_default"]))
            for key in SHOW_KEYS:
                self.fields[key] = data["builtin"][key]
            data["builtin"].update(self.fields)
        elif self.config.has_custom_preset(preset):
            self.fields.update(data["presets"][preset])
        else:
            data["presets"][preset] = dict(self.fields)
        self.config.save()
        await self.apply_settings()
        self.broadcast_state()

    async def apply_settings(self) -> None:
        """ApplyPresetSettings：把当前字段应用到硬件。"""
        fields = dict(self.fields)
        self.load_fan_curve(fields["fan_table"])
        await self.apply_fan_control()
        if self.bios:
            await self.bios_call(self._apply_power_sync, fields)
        await self.apply_gpu_settings(fields)
        if self.bios and _is_int(fields["tpp"]):
            # TPP 延迟 1 秒应用，避免与其他设置冲突
            self.loop.call_later(1.0, lambda: self.spawn(self.bios_call(self._apply_tpp_sync, fields)))

    def _apply_power_sync(self, fields: dict) -> None:
        bios = self.bios
        if self.caps.get("cpu_power_control") and _is_int(fields["cpu_power"]) and 5 <= fields["cpu_power"] <= 254:
            bios.set_cpu_power_limit(fields["cpu_power"])
        bios.set_gpu_power_state(fields["tgp"], fields["ppab"], 1 if fields["dstate"] == "normal" else 2)
        if self.caps.get("iccmax") and _is_int(fields["iccmax"]) and 150 <= fields["iccmax"] <= 350:
            bios.set_iccmax(fields["iccmax"])
        if self.caps.get("loadline_levels") and _is_int(fields["ac_loadline"]) and fields["ac_loadline"] >= 1:
            bios.set_loadline(fields["ac_loadline"])

    def _apply_tpp_sync(self, fields: dict) -> None:
        if _is_int(fields["tpp"]) and 20 <= fields["tpp"] <= 254:
            self.bios.set_concurrent_tdp(fields["tpp"])

    async def apply_fan_control(self) -> None:
        """RestoreFanControl。"""
        if not self.bios or self.cleaning_until:
            return
        control = self.fields["fan_control"]
        if control == "max":
            await self.bios_call(self.bios.set_max_fan_speed, True)
            return
        await self.bios_call(self.bios.set_max_fan_speed, False)
        if control == "auto":
            self.fan_levels[0] = self.fan_levels[1] = -10
            return
        level = int(control) // 100
        await self.bios_call(self.bios.set_fan_level, level, level, self.caps.get("three_fan", False))
        if not self.fields["monitor_fan"]:
            self.fan_levels[0] = self.fan_levels[1] = level

    async def apply_thermal_mode(self) -> None:
        """SetUnleashMode：OSH 固定使用释放全部性能的 BIOS 模式；同时同步内核 platform_profile。"""
        mode = self.settings["thermal_mode"]
        if mode == "system" or not self.bios:
            return
        if self.config.platform["sync_platform_profile"] and self.info["backend"].get("name") == "acpi_call":
            await self.io_call(self._sync_platform_profile, mode)
        await self.bios_call(self.bios.set_performance_mode, mode)

    @staticmethod
    def _sync_platform_profile(mode: str) -> None:
        current = sensors.read_file(PLATFORM_PROFILE)
        choices = (sensors.read_file(PLATFORM_PROFILE + "_choices") or "").split()
        if current is None:
            return
        for name in _PROFILE_FOR_MODE.get(mode, ()):
            if name in choices:
                if name != current:
                    try:
                        with open(PLATFORM_PROFILE, "w", encoding="ascii") as f:
                            f.write(name)
                    except OSError as exc:
                        log.debug("cannot write platform_profile: %s", exc)
                return

    async def apply_gpu_settings(self, fields: dict = None) -> None:
        """GPU 频率偏移（NVML）与频率限制（nvidia-smi）。全为默认值时不触碰独显，避免把它唤醒。"""
        fields = fields or self.fields
        gpu = self.caps.get("gpu")
        if self.mock or not gpu or gpu.get("kind") != "nvidia":
            return
        core, mem, clock = fields["gpu_core_oc"], fields["gpu_mem_oc"], fields["gpu_clock"]
        if core is not None or mem is not None:
            await self.io_call(nvidia.set_clock_offsets, core, mem)
        if _is_int(clock) and clock > 0:
            await self.io_call(nvidia.lock_gpu_clock, clock)

    async def restore_power_config(self) -> None:
        """RestorePowerConfig：接通交流电源后重新应用性能模式与功率（BIOS 或内核可能已重置）。"""
        await self.apply_thermal_mode()
        await asyncio.sleep(1.0)
        if self.bios:
            fields = dict(self.fields)
            await self.bios_call(self._apply_power_sync, fields)
            await self.bios_call(self._apply_tpp_sync, fields)

    # ================================================================== CPU 降压
    @property
    def uv_state_path(self) -> str:
        return os.path.join(self.state_dir, "undervolt.json")

    def _init_undervolt_state(self) -> None:
        """读取上次的降压状态。

        同一次开机内（服务重启）沿用记录的开机原始值；新的一次开机时，如果上次开机应用了降压
        却没有正常停止服务（死机、强制关机），本次暂停自动应用，直到用户重新设置。
        """
        previous = undervolt.load_state(self.uv_state_path)
        boot = undervolt.boot_id()
        if previous and previous.get("boot_id") == boot:
            baseline = previous.get("baseline")
            if not (isinstance(baseline, dict) and all(isinstance(baseline.get(k), (int, float))
                                                        for k in undervolt.PLANES)):
                baseline = None
            self.uv_state = {"boot_id": boot, "baseline": baseline, "applied": bool(previous.get("applied")),
                             "clean": bool(previous.get("clean", True)),
                             "suspended": bool(previous.get("suspended"))}
            return
        crashed = bool(previous and previous.get("applied") and not previous.get("clean", True))
        suspended = bool(previous and previous.get("suspended")) or crashed
        self.uv_state = {"boot_id": boot, "baseline": None, "applied": False, "clean": True, "suspended": suspended}
        if crashed:
            log.warning("the previous boot did not shut down cleanly while undervolting; "
                        "the undervolt stays off until it is set again")
        if previous is not None:
            self._uv_save()

    def _uv_save(self) -> None:
        try:
            undervolt.save_state(self.uv_state_path, self.uv_state)
        except OSError as exc:
            log.error("cannot save %s: %s", self.uv_state_path, exc)

    def _uv_mailbox(self) -> undervolt.Mailbox:
        if self.uv_box is None:
            device = undervolt.FakeMsrDevice() if self.mock else undervolt.MsrDevice()
            if not device.ensure():
                raise undervolt.UndervoltError("cannot open /dev/cpu/0/msr (msr kernel module)", "msr_unavailable")
            self.uv_box = undervolt.Mailbox(device)
        return self.uv_box

    def _uv_read_sync(self) -> dict:
        with self.uv_lock:
            values = self._uv_mailbox().read_all()
            state = self.uv_state
            if state["baseline"] is None and not state["applied"]:
                # 本次开机还没写入过，读到的就是开机时的原始值
                state["baseline"] = values
                self._uv_save()
            self.uv_current = values
            return values

    def _uv_apply_sync(self, target, remember: bool = False) -> dict:
        """在锁内应用降压。

        target 为 mV 时应用到核心与缓存；为 None 时恢复开机时的原始值（本次开机没有写入过则不访问 MSR）；
        为 _UV_FROM_SETTINGS 时在锁内读取当前设置，避免与用户同时修改时用旧值覆盖新值。
        remember=True 时在成功后更新设置并解除暂停（由用户主动设置时使用）。
        """
        with self.uv_lock:
            state = self.uv_state
            if target is _UV_FROM_SETTINGS:
                target = self.settings["undervolt"]
                if target is None or state["suspended"]:
                    return self.uv_current
            actual = self.uv_current if target is None and not state["applied"] else self._uv_write_locked(target)
            if remember:
                self.settings["undervolt"] = target
                if state["suspended"]:
                    state["suspended"] = False
                    self._uv_save()
            return actual

    def _uv_write_locked(self, target) -> dict:
        state = self.uv_state
        box = self._uv_mailbox()
        if state["baseline"] is None:
            state["baseline"] = box.read_all()
        previous = self.settings["undervolt"] if state["applied"] else None
        if target is None:
            offsets = dict(state["baseline"])
        else:
            offsets = {name: target for name in undervolt.PLANES}
            # 先落盘再写入：写入后死机时，下次开机据此暂停自动应用
            state.update(applied=True, clean=False)
        self._uv_save()
        try:
            actual = box.write_all(offsets)
        except undervolt.UndervoltError:
            if target is not None:
                # 回到写入前的状态：之前已应用的降压值，或开机时的原始值；恢复也失败时按“仍在降压”处理
                try:
                    if previous is not None:
                        box.write_all({name: previous for name in undervolt.PLANES})
                    else:
                        box.write_all(state["baseline"])
                        state.update(applied=False, clean=True)
                except undervolt.UndervoltError as exc:
                    log.error("cannot restore the previous voltage offsets: %s", exc)
                self._uv_save()
            try:
                self.uv_current = box.read_all()
            except undervolt.UndervoltError:
                self.uv_current = None
            raise
        if target is None:
            state.update(applied=False, clean=True)
            self._uv_save()
        self.uv_current = actual
        return actual

    async def apply_undervolt(self) -> None:
        """开机与睡眠唤醒后按设置应用降压；没有设置降压时不访问 MSR。"""
        if self.settings["undervolt"] is None:
            return
        if not self.caps.get("undervolt"):
            log.warning("undervolt is set but unavailable: %s", self.caps.get("undervolt_reason"))
            return
        if self.uv_state["suspended"]:
            return
        try:
            await self.io_call(self._uv_apply_sync, _UV_FROM_SETTINGS)
            self.uv_error = None
        except undervolt.UndervoltError as exc:
            self.uv_error = exc.code
            log.error("applying the undervolt failed: %s", exc)
        self.broadcast_state()

    async def set_undervolt(self, value) -> None:
        if value is not None and not undervolt.valid_offset(value):
            raise IpcError(f"undervolt must be an integer from {undervolt.MIN_OFFSET} to 0 mV", E_INVALID)
        if not self.caps.get("undervolt"):
            raise IpcError(f"undervolt unavailable: {self.caps.get('undervolt_reason')}", E_UNSUPPORTED)
        try:
            await self.io_call(self._uv_apply_sync, value, True)
        except undervolt.UndervoltError as exc:
            self.uv_error = exc.code
            self.broadcast_state()
            raise IpcError(str(exc), f"undervolt_{exc.code}") from exc
        self.uv_error = None
        self.config.save()
        self.broadcast_state()

    def undervolt_payload(self) -> dict:
        state = self.uv_state
        return {"supported": bool(self.caps.get("undervolt")), "reason": self.caps.get("undervolt_reason"),
                "current": self.uv_current, "baseline": state["baseline"], "applied": state["applied"],
                "suspended": state["suspended"], "error": self.uv_error}

    # ================================================================== 修改单项设置
    def _validate_field(self, key: str, value):
        caps = self.caps
        if key == "fan_table":
            if value not in FAN_TABLES:
                raise IpcError("invalid fan table", E_INVALID)
        elif key == "fan_control":
            if value not in ("auto", "max"):
                if not _is_int(value) or not 0 <= value <= max(caps["fan_slider_max"], 1000):
                    raise IpcError("fan speed out of range", E_INVALID)
                value = int(round(value / 100.0)) * 100
        elif key == "temp_sensitivity":
            if value not in RESPOND_SPEED:
                raise IpcError("invalid sensitivity", E_INVALID)
        elif key in ("cpu_power", "tpp", "iccmax", "ac_loadline", "gpu_core_oc", "gpu_mem_oc"):
            limits = {"cpu_power": (10, 254), "tpp": (20, 254), "iccmax": (150, 350),
                      "ac_loadline": (1, max(1, caps.get("loadline_levels") or 1)),
                      "gpu_core_oc": (-300, 300), "gpu_mem_oc": (-1000, 2000)}[key]
            if value == "max":
                value = 254
            if value is not None and (not _is_int(value) or not limits[0] <= value <= limits[1]):
                raise IpcError(f"{key} out of range {limits}", E_INVALID)
            if key == "cpu_power" and not caps.get("cpu_power_control"):
                raise IpcError("CPU power control is not supported", E_UNSUPPORTED)
        elif key == "gpu_clock":
            if not _is_int(value) or not (value == 0 or 210 <= value <= 4000):
                raise IpcError("invalid GPU clock", E_INVALID)
        elif key in ("tgp", "ppab") or key.startswith(("monitor_", "show_")):
            if not isinstance(value, bool):
                raise IpcError(f"{key} must be a boolean", E_INVALID)
        elif key == "dstate":
            if value not in ("normal", "low"):
                raise IpcError("invalid dState", E_INVALID)
        elif key == "refresh_rate":
            if value not in ("low", "high"):
                raise IpcError("invalid refresh rate", E_INVALID)
        elif key == "temp_display":
            if value not in ("smoothed", "raw"):
                raise IpcError("invalid temperature display", E_INVALID)
        return value

    async def set_field(self, key: str, value) -> None:
        if key in BIOS_FIELDS and not self.bios:
            raise IpcError(self.backend_error or "BIOS interface unavailable", E_UNSUPPORTED)
        value = self._validate_field(key, value)
        fields = self.fields
        # 自动转速模式下禁止同时关闭 CPU 与 GPU 监控
        if key in ("monitor_cpu", "monitor_gpu") and not value and fields["fan_control"] == "auto":
            other = fields["monitor_gpu" if key == "monitor_cpu" else "monitor_cpu"]
            if not other:
                raise IpcError("auto fan control needs CPU or GPU monitoring", "auto_fan_needs_monitor")
        if key == "fan_control" and value == "auto" and not (fields["monitor_cpu"] or fields["monitor_gpu"]):
            fields["monitor_cpu"] = True
            self.config.preset_store(self.current_preset)["monitor_cpu"] = True
        fields[key] = value
        self.config.preset_store(self.current_preset)[key] = value
        self.config.save()
        await self._apply_field(key, value)
        self.broadcast_state()

    async def _apply_field(self, key: str, value) -> None:
        bios = self.bios
        fields = self.fields
        if key == "fan_table":
            self.load_fan_curve(value)
        elif key == "fan_control":
            await self.apply_fan_control()
        elif key == "cpu_power" and bios and _is_int(value):
            await self.bios_call(bios.set_cpu_power_limit, value)
        elif key == "tpp" and bios and _is_int(value):
            await self.bios_call(bios.set_concurrent_tdp, value)
        elif key in ("tgp", "ppab", "dstate") and bios:
            await self.bios_call(bios.set_gpu_power_state, fields["tgp"], fields["ppab"],
                                 1 if fields["dstate"] == "normal" else 2)
        elif key == "iccmax" and bios and _is_int(value):
            await self.bios_call(bios.set_iccmax, value)
        elif key == "ac_loadline" and bios and _is_int(value):
            await self.bios_call(bios.set_loadline, value)
        elif key in ("gpu_core_oc", "gpu_mem_oc") and not self.mock and self.caps.get("gpu"):
            # “不设置”时与 OSH 一样把偏移恢复为 0
            offset = value if _is_int(value) else 0
            error = await self.io_call(nvidia.set_clock_offsets, *((offset, None) if key == "gpu_core_oc"
                                                                     else (None, offset)))
            if error:
                raise IpcError(error, E_FAILED)
        elif key == "gpu_clock" and not self.mock and self.caps.get("gpu"):
            ok = await self.io_call(nvidia.lock_gpu_clock, value) if value else \
                await self.io_call(nvidia.reset_gpu_clock)
            if not ok:
                raise IpcError("nvidia-smi failed", E_FAILED)
        elif key == "monitor_cpu" and value:
            self.cpu["ready"] = False
        elif key == "monitor_gpu" and value:
            self.gpu["ready"] = self.gpu["fan_ready"] = False

    async def set_setting(self, key: str, value) -> None:
        settings = self.settings
        if key == "undervolt":
            # 先写入硬件，成功后才保存，避免保存一个被 BIOS 拒绝的值
            await self.set_undervolt(value)
            return
        if key == "auto_fan_protect" or key == "data_localize":
            if not isinstance(value, bool):
                raise IpcError(f"{key} must be a boolean", E_INVALID)
        elif key == "thermal_mode":
            if value not in THERMAL_MODES:
                raise IpcError("invalid thermal mode", E_INVALID)
        elif key == "omen_key":
            if value not in OMEN_KEY_ACTIONS:
                raise IpcError("invalid Omen key action", E_INVALID)
        elif key == "omen_key_shortcut":
            if not isinstance(value, str):
                raise IpcError("invalid shortcut", E_INVALID)
            if value:
                try:
                    parse_shortcut(value)
                except ValueError as exc:
                    raise IpcError(str(exc), E_INVALID) from exc
        elif key == "omen_key_presets":
            if not isinstance(value, list):
                raise IpcError("omen_key_presets must be a list", E_INVALID)
            value = [p for p in PRESET_ORDER if p in {normalize_preset(v) for v in value}]
            if not value:
                raise IpcError("at least one preset must be selected", E_INVALID)
        elif key in ("kb_protocol", "lb_protocol"):
            if value not in (IFACE_BASIC, IFACE_DOJO):
                raise IpcError("invalid lighting protocol", E_INVALID)
        elif key == "light_anim":
            if not isinstance(value, dict):
                raise IpcError("invalid animation", E_INVALID)
            value = {k: int(value.get(k, settings["light_anim"].get(k, 0))) for k in
                     ("effect", "speed", "direction", "theme")}
        else:
            raise IpcError(f"unknown setting {key}", E_INVALID)
        settings[key] = value
        self.config.save()
        if key == "thermal_mode":
            await self.apply_thermal_mode()
            if value != "system" and self.bios:
                # 切换 BIOS 模式后功率限制可能被重置，重新应用
                await self.bios_call(self._apply_power_sync, dict(self.fields))
        elif key == "data_localize" and not value:
            self._last_texts.clear()
        self.broadcast_state()

    # ================================================================== 循环任务
    async def monitor_loop(self) -> None:
        """UpdateTooltip + QueryHardware：按刷新频率读取传感器、平滑温度并推送状态。"""
        while True:
            await asyncio.sleep(0.25 if self.fields["refresh_rate"] == "high" else 1.0)
            try:
                await self.monitor_tick()
            except Exception:  # noqa: BLE001 - 循环任务不能因单次异常退出
                log.exception("monitor tick failed")

    async def monitor_tick(self) -> None:
        fields = self.fields
        respond = RESPOND_SPEED.get(fields["temp_sensitivity"], 0.4)
        raw_mode = fields["temp_display"] == "raw"
        cpu = self.cpu
        if fields["monitor_cpu"]:
            reader = self.cpu_sensor.read_cpu if self.mock else self.cpu_sensor.read
            data = reader()
            raw = data["temp"]
            if raw is not None:
                if not cpu["ready"]:
                    self.smoothed_cpu = raw
                    cpu["ready"] = True
                else:
                    self.smoothed_cpu = raw * respond + self.smoothed_cpu * (1 - respond)
            cpu.update(raw_temp=raw, power=data["power"], freq=data["freq"],
                       temp=(raw if raw_mode else self.smoothed_cpu) if cpu["ready"] else None)
            if raw is None and self.bios and self.caps.get("ambient_sensor"):
                self.fitting_temp = await self.bios_call(self.bios.get_fitting_temperature)
        else:
            cpu.update(temp=None, raw_temp=None, power=None, freq=None, ready=False)
            self.fitting_temp = None

        gpu = self.gpu
        sample = self.gpu_sample
        if fields["monitor_gpu"] and sample is not None:
            gpu["state"] = sample["state"]
            raw = sample.get("temp")
            if sample["state"] in ("active", "idle") and raw is not None:
                if not gpu["ready"]:
                    self.smoothed_gpu = raw
                    gpu["ready"] = True
                elif sample["state"] == "active":
                    self.smoothed_gpu = raw * respond + self.smoothed_gpu * (1 - respond)
                gpu["fan_ready"] = sample["state"] == "active"
                gpu.update(raw_temp=raw, power=sample.get("power"), freq=sample.get("freq"),
                           temp=raw if raw_mode else self.smoothed_gpu)
            else:
                gpu.update(temp=None, raw_temp=None, power=None, freq=None, ready=False, fan_ready=False)
        else:
            gpu.update(state="none" if not self.caps.get("gpu") else ("disabled" if not fields["monitor_gpu"]
                                                                      else "pending"),
                       temp=None, raw_temp=None, power=None, freq=None, ready=False, fan_ready=False)

        if fields["monitor_fan"] and self.bios:
            levels = await self.bios_call(self.bios.get_fan_level)
            if levels:
                self.fan_levels = list(levels)

        await self.check_auto_fan_protect()
        if self.settings["data_localize"]:
            await self.io_call(self.write_data_files)
        self.broadcast("status", self.status_payload())

    async def gpu_loop(self) -> None:
        """独显读取较慢（nvidia-smi），单独以不低于 1 秒的间隔进行。"""
        while True:
            await asyncio.sleep(1.0)
            if not self.fields["monitor_gpu"] or not self.caps.get("gpu"):
                self.gpu_sample = None
                continue
            try:
                if self.mock:
                    self.gpu_sample = self.cpu_sensor.read_gpu()
                elif self.gpu_monitor:
                    self.gpu_sample = await self.io_call(self.gpu_monitor.read)
            except Exception:  # noqa: BLE001
                log.exception("GPU read failed")

    async def fan_loop(self) -> None:
        """fanControlTimer：自动模式下每秒按曲线设置转速。"""
        while True:
            await asyncio.sleep(1.0)
            try:
                await self.fan_tick()
            except Exception:  # noqa: BLE001
                log.exception("fan control failed")

    async def fan_tick(self) -> None:
        """转速变化超过 100 RPM 才下发，避免频繁调用 BIOS。"""
        if not self.bios or self.cleaning_until or self.fields["fan_control"] != "auto":
            return
        speed = self.fan_speed_for_temperature()
        if speed < 0:
            return
        level = speed // 100
        if abs(level - self.fan_levels[0]) > 1 or abs(level - self.fan_levels[1]) > 1:
            ok = await self.bios_call(self.bios.set_fan_level, level, level, self.caps.get("three_fan", False))
            if ok and not self.fields["monitor_fan"]:
                self.fan_levels[0] = self.fan_levels[1] = level

    async def heartbeat_loop(self) -> None:
        """optimiseSchedule：每 30 秒与 BIOS 通信一次，避免 120 秒后回退到默认风扇 / 功耗状态。"""
        while True:
            if self.bios:
                try:
                    if self.heartbeat_count < 5 and not self.cleaning_until:
                        self.heartbeat_count += 1
                        control = self.fields["fan_control"]
                        if control == "max":
                            await self.bios_call(self.bios.set_max_fan_speed, True)
                        elif _is_int(control):
                            await self.bios_call(self.bios.set_max_fan_speed, False)
                            level = control // 100
                            await self.bios_call(self.bios.set_fan_level, level, level,
                                                 self.caps.get("three_fan", False))
                    ok, ocp, otp = await self.bios_call(self.bios.get_fan_count)
                    if not ok:
                        log.error("cannot read BIOS protection state")
                    elif ocp or otp:
                        log.info("BIOS protection - over-current: %s, over-temperature: %s", ocp, otp)
                except Exception:  # noqa: BLE001
                    log.exception("heartbeat failed")
            await asyncio.sleep(30)

    async def power_loop(self) -> None:
        """OnPowerChange：交流电源插拔与睡眠恢复；以及独显唤醒后重新应用 GPU 设置。"""
        while True:
            await asyncio.sleep(1.0)
            try:
                if self.resume_detector.check():
                    log.info("system resumed")
                    if self.bios:
                        await self.bios_call(self.bios.get_fan_count)
                    self.heartbeat_count = 0
                    self.restore_countdown = 3
                ac = sensors.ac_online()
                if ac is not None and ac != self.ac:
                    self.ac = ac
                    log.info("AC adapter %s", "connected" if ac else "disconnected")
                    if ac:
                        self.spawn(self.restore_power_config())
                if self.restore_countdown > 0:
                    self.restore_countdown -= 1
                    if self.restore_countdown == 0:
                        await self.restore_config()
                        await self.apply_undervolt()
                await self._check_gpu_wake()
            except Exception:  # noqa: BLE001
                log.exception("power loop failed")

    async def _check_gpu_wake(self) -> None:
        gpu = self.caps.get("gpu")
        if self.mock or not gpu or gpu.get("kind") != "nvidia" or not gpu.get("pci"):
            return
        status = sensors.runtime_status(gpu["pci"])
        previous, self.gpu_runtime_prev = self.gpu_runtime_prev, status
        fields = self.fields
        needs = fields["gpu_core_oc"] is not None or fields["gpu_mem_oc"] is not None or fields["gpu_clock"]
        if needs and previous in ("suspended", "suspending") and status == "active":
            # 独显从 D3 唤醒后驱动会重新初始化，频率偏移 / 锁频需要重新设置
            log.info("dGPU woke up, re-applying GPU clock settings")
            await asyncio.sleep(2.0)
            await self.apply_gpu_settings()

    async def check_auto_fan_protect(self) -> None:
        """固定转速下 CPU 温度接近温度墙时自动切回降温曲线 + 自动风扇。"""
        control = self.fields["fan_control"]
        if not self.settings["auto_fan_protect"] or not _is_int(control) or not self.bios:
            return
        if not self.cpu["ready"]:
            return
        limit = self.cpu_temp_limit or 97
        if self.smoothed_cpu <= limit - 2:
            return
        if self.platform_max_rpm:
            current_rpm = (max(0, self.fan_levels[0]) + max(0, self.fan_levels[1])) * 50
            if current_rpm >= self.platform_max_rpm * 0.8:
                return
        log.warning("CPU %.1f°C with fixed fan speed, switching to cool curve + auto", self.smoothed_cpu)
        store = self.config.preset_store(self.current_preset)
        self.fields["fan_table"] = store["fan_table"] = "cool"
        self.fields["fan_control"] = store["fan_control"] = "auto"
        self.config.save()
        self.load_fan_curve("cool")
        await self.apply_fan_control()
        self.notify("high_temp", limit=limit - 2, temp=round(self.smoothed_cpu, 1))
        self.broadcast_state()

    def write_data_files(self) -> None:
        """SyncDataToTxt：把温度与转速写入文本文件，供外部工具读取。"""
        values = {
            "cpu_temp.txt": str(int(round(self.cpu["temp"]))) if self.cpu["temp"] is not None else "0",
            "gpu_temp.txt": str(int(round(self.gpu["temp"]))) if self.gpu["temp"] is not None else "0",
            "fan_rpm.txt": str((max(0, self.fan_levels[0]) + max(0, self.fan_levels[1])) * 50),
        }
        for name, text in values.items():
            if self._last_texts.get(name) == text:
                continue
            path = os.path.join(self.runtime_dir, name)
            try:
                with open(path, "w", encoding="ascii") as f:
                    f.write(text)
                self._last_texts[name] = text
            except OSError as exc:
                log.error("cannot write %s: %s", path, exc)

    # ================================================================== 反转除尘
    async def start_cleaning(self) -> int:
        mode = self.caps.get("clean_creek")
        if not mode or not self.bios:
            raise IpcError("fan cleaning is not supported", E_UNSUPPORTED)
        seconds = self.caps["clean_creek_seconds"]
        if self.cleaning_until:
            return max(0, int(self.cleaning_until - time.monotonic()))
        await self.bios_call(self.bios.set_max_fan_speed, False)
        if mode == "new":
            level = self.caps["clean_creek_level"]
            ok = await self.bios_call(self.bios.set_fan_level, level, level, self.caps.get("three_fan", False), True)
        else:
            ok = await self.bios_call(self.bios.set_legacy_clean_creek, True)
        if not ok:
            raise IpcError("BIOS rejected the cleaning command", E_FAILED)
        self.cleaning_until = time.monotonic() + seconds
        self.cleaning_task = self.spawn(self._cleaning_timer(seconds))
        self.broadcast_state()
        return seconds

    async def _cleaning_timer(self, seconds: int) -> None:
        await asyncio.sleep(seconds)
        self.cleaning_task = None
        await self.stop_cleaning()

    async def stop_cleaning(self) -> None:
        if not self.cleaning_until:
            return
        if self.cleaning_task and self.cleaning_task is not asyncio.current_task():
            self.cleaning_task.cancel()
        self.cleaning_task = None
        if self.caps.get("clean_creek") == "legacy":
            await self.bios_call(self.bios.set_legacy_clean_creek, False)
        self.cleaning_until = 0.0
        self.fan_levels[0] = self.fan_levels[1] = -10
        await self.apply_fan_control()
        self.broadcast_state()

    # ================================================================== Omen 键
    def preset_available(self, preset: str) -> bool:
        return preset in CUSTOM_PRESETS or bool(self.caps.get("cpu_power_control"))

    def preset_candidates(self):
        """GetOmenKeyPresetCandidateKeys：未设置时为全部可用预设。"""
        available = [p for p in PRESET_ORDER if self.preset_available(p)]
        chosen = [p for p in available if p in self.settings["omen_key_presets"]]
        return chosen or available

    def on_omen_key(self) -> None:
        action = self.settings["omen_key"]
        log.debug("Omen key pressed, action=%s", action)
        if action == "overlay":
            self.broadcast("omen_key", {"action": "overlay"})
        elif action == "app":
            # 命令保存在各用户自己的托盘配置中，由托盘程序以该用户身份启动
            self.broadcast("omen_key", {"action": "app"})
        elif action == "preset":
            self.spawn(self.switch_preset_by_omen_key())
        elif action == "shortcut":
            self.spawn(self.send_shortcut())

    async def switch_preset_by_omen_key(self) -> None:
        candidates = self.preset_candidates()
        if not candidates:
            return
        current = self.current_preset
        index = candidates.index(current) if current in candidates else -1
        target = candidates[(index + 1) % len(candidates)]
        if target != current:
            await self.apply_preset(target)
        self.notify("preset_switched", preset=target)

    async def send_shortcut(self) -> None:
        try:
            codes = parse_shortcut(self.settings["omen_key_shortcut"])
        except ValueError:
            self.notify("shortcut_not_set")
            return
        try:
            await self.io_call(self._send_keys, codes)
        except OSError as exc:
            log.error("cannot send shortcut: %s", exc)
            self.notify("shortcut_failed", error=str(exc))

    def _send_keys(self, codes) -> None:
        if self.vkbd is None:
            self.vkbd = VirtualKeyboard()
        self.vkbd.send(codes)

    # ================================================================== 状态数据
    def status_payload(self) -> dict:
        count = self.caps.get("fan_count", 2)
        remaining = max(0, int(math.ceil(self.cleaning_until - time.monotonic()))) if self.cleaning_until else 0
        return {
            "preset": self.current_preset,
            "cpu": {key: self.cpu[key] for key in ("temp", "raw_temp", "power", "freq", "ready")},
            "gpu": {key: self.gpu[key] for key in ("state", "temp", "raw_temp", "power", "freq", "ready")},
            "fan": {"levels": [max(0, v) for v in self.fan_levels[:count]], "monitored": self.fields["monitor_fan"]},
            "fan_control": self.fields["fan_control"],
            "cleaning": remaining,
            "ac": self.ac,
        }

    def state_payload(self) -> dict:
        data = self.config.data
        remaining = max(0, int(math.ceil(self.cleaning_until - time.monotonic()))) if self.cleaning_until else 0
        return {
            "current_preset": self.current_preset,
            "custom_names": dict(data["custom_names"]),
            "fields": dict(self.fields),
            "settings": dict(self.settings),
            "presets_available": [p for p in PRESET_ORDER if self.preset_available(p)],
            "omen_key_candidates": self.preset_candidates(),
            "omen_key_device": bool(self.omen_key and self.omen_key.connected),
            "cleaning": remaining,
            "gfx_mode": self.caps.get("gfx_mode"),
            "nvidia_powerd": self.powerd,
            "undervolt": self.undervolt_payload(),
        }

    # ================================================================== IPC
    async def start_server(self):
        os.makedirs(self.runtime_dir, exist_ok=True)
        if os.path.exists(self.socket_path):
            try:
                _reader, writer = await asyncio.open_unix_connection(self.socket_path)
            except (ConnectionError, OSError):
                os.unlink(self.socket_path)
            else:
                writer.close()
                await writer.wait_closed()
                raise RuntimeError(f"another omenhubd is already running ({self.socket_path})")
        server = await asyncio.start_unix_server(self.handle_client, path=self.socket_path, limit=1 << 20)
        os.chmod(self.socket_path, 0o666)
        return server

    async def handle_client(self, reader, writer) -> None:
        uid = -1
        try:
            _pid, uid, _gid = peer_credentials(writer.get_extra_info("socket"))
        except OSError:
            pass
        authorized = is_authorized(uid, self.config.platform["allowed_groups"]) if uid >= 0 else False
        self.connections.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise ValueError("not an object")
                except ValueError:
                    writer.write(encode({"id": None, "ok": False, "error": "invalid JSON", "code": E_INVALID}))
                    continue
                reply = await self.dispatch(message, uid, authorized, writer)
                writer.write(encode(reply))
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
            pass
        finally:
            self.subscribers.discard(writer)
            self.connections.discard(writer)
            writer.close()

    async def dispatch(self, message: dict, uid: int, authorized: bool, writer) -> dict:
        msg_id = message.get("id")
        cmd = message.get("cmd")
        args = message.get("args") or {}
        handler = getattr(self, f"cmd_{cmd}", None) if isinstance(cmd, str) else None
        if handler is None or not isinstance(args, dict):
            return {"id": msg_id, "ok": False, "error": f"unknown command {cmd}", "code": E_UNKNOWN}
        if cmd not in READ_ONLY_COMMANDS and not authorized:
            return {"id": msg_id, "ok": False, "error": "permission denied", "code": E_DENIED}
        try:
            result = await handler(args, uid=uid, writer=writer)
        except IpcError as exc:
            return {"id": msg_id, "ok": False, "error": str(exc), "code": exc.code}
        except (ValueError, TypeError, KeyError) as exc:
            return {"id": msg_id, "ok": False, "error": f"invalid arguments: {exc}", "code": E_INVALID}
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s failed", cmd)
            return {"id": msg_id, "ok": False, "error": str(exc), "code": E_FAILED}
        return {"id": msg_id, "ok": True, "result": result}

    def _require_bios(self) -> OmenBios:
        if not self.bios:
            raise IpcError(self.backend_error or "BIOS interface unavailable", E_UNSUPPORTED)
        return self.bios

    # ---- 查询 ---------------------------------------------------------------
    async def cmd_ping(self, args, **_):
        return {"version": __version__}

    async def cmd_get_info(self, args, **_):
        return self.info

    async def cmd_get_state(self, args, **_):
        return self.state_payload()

    async def cmd_get_status(self, args, **_):
        return self.status_payload()

    async def cmd_subscribe(self, args, writer=None, **_):
        self.subscribers.add(writer)
        return {"info": self.info, "state": self.state_payload(), "status": self.status_payload()}

    async def cmd_bios_sensors(self, args, **_):
        bios = self._require_bios()
        return await self.bios_call(lambda: [bios.get_sensor_temperature(i) for i in range(4)])

    async def cmd_gpu_refresh(self, args, **_):
        """刷新 NVIDIA 功率限制；独显休眠时不查询，避免唤醒。"""
        gpu = self.caps.get("gpu")
        if gpu and gpu.get("kind") == "nvidia" and not self.mock and gpu.get("pci"):
            if sensors.runtime_status(gpu["pci"]) not in ("suspended", "suspending"):
                limits = await self.io_call(nvidia.power_limits)
                if limits:
                    gpu["power_limits"] = list(limits)
            self.powerd = await self.io_call(nvidia.powerd_status)
        return {"gpu": gpu, "nvidia_powerd": self.powerd}

    async def cmd_probe(self, args, **_):
        bios = self._require_bios()

        def work():
            table = bios.get_fan_table()
            return {"design": bios.parse_system_design_data(), "fan_type": bios.get_fan_type(),
                    "fan_table": table.hex() if table else None, "fan_level": bios.get_fan_level(),
                    "gpu_power_state": bios.get_gpu_power_state(), "gfx_mode": bios.get_gfx_mode(),
                    "kb_type": bios.get_keyboard_type(),
                    "sensors": [bios.get_sensor_temperature(i) for i in range(4)]}
        return {"info": self.info, "bios": await self.bios_call(work)}

    async def cmd_undervolt_read(self, args, **_):
        """从 CPU 读取当前的电压偏移（需要写入读取请求，所以不是只读命令）。"""
        if not self.caps.get("undervolt"):
            raise IpcError(f"undervolt unavailable: {self.caps.get('undervolt_reason')}", E_UNSUPPORTED)
        try:
            await self.io_call(self._uv_read_sync)
        except undervolt.UndervoltError as exc:
            self.uv_error = exc.code
            raise IpcError(str(exc), f"undervolt_{exc.code}") from exc
        self.broadcast_state()
        return self.undervolt_payload()

    # ---- 预设与设置 ---------------------------------------------------------
    async def cmd_apply_preset(self, args, **_):
        preset = normalize_preset(args["preset"])
        await self.apply_preset(preset)
        return self.state_payload()

    async def cmd_rename_preset(self, args, **_):
        preset = normalize_preset(args["preset"])
        name = str(args.get("name") or "").strip()
        if preset not in CUSTOM_PRESETS:
            raise IpcError("only custom presets can be renamed", E_INVALID)
        names = self.config.data["custom_names"]
        others = [names[p] for p in CUSTOM_PRESETS if p != preset and names[p]]
        if not name or name in others or len(name) > 64:
            raise IpcError("preset name cannot be empty and must be unique", E_INVALID)
        names[preset] = name
        self.config.save()
        self.broadcast_state()
        return self.state_payload()

    async def cmd_set(self, args, **_):
        key, value = args["key"], args.get("value")
        if key in PRESET_FIELDS or key in MONITOR_FIELDS:
            await self.set_field(key, value)
        else:
            await self.set_setting(key, value)
        return self.state_payload()

    # ---- 风扇 ---------------------------------------------------------------
    def _fan_limits(self) -> dict:
        max_rpm = self.platform_max_rpm or 5600
        return {"cpu_max": self.cpu_temp_limit or 100, "gpu_max": self.gpu_temp_limit or 90,
                "fan_max": max(1000, int(math.ceil(max_rpm * 1.1 / 100.0)) * 100)}

    async def cmd_fan_curve_get(self, args, **_):
        table = args.get("table", self.fields["fan_table"])
        if table not in FAN_TABLES:
            raise IpcError("invalid fan table", E_INVALID)
        if args.get("default"):
            profile = self.default_fan_curve(table)
        else:
            path = self.fan_curve_path(table)
            try:
                profile = FanCurveProfile.load(path)
            except (OSError, InvalidFanCurve):
                profile = self.default_fan_curve(table if table != "custom" else "cool")
        return {"table": table, "profile": profile.to_dict(), "limits": self._fan_limits()}

    async def cmd_fan_curve_set(self, args, **_):
        table = args["table"]
        if table not in FAN_TABLES:
            raise IpcError("invalid fan table", E_INVALID)
        try:
            profile = FanCurveProfile.from_dict(args["profile"])
        except InvalidFanCurve as exc:
            raise IpcError(f"invalid fan curve: {exc}", E_INVALID) from exc
        os.makedirs(self.state_dir, exist_ok=True)
        profile.save(self.fan_curve_path(table))
        if args.get("apply"):
            await self.set_field("fan_table", table)
        elif table == self.fields["fan_table"]:
            self.load_fan_curve(table)
        return {"table": table}

    async def cmd_fan_curve_reset(self, args, **_):
        table = args["table"]
        if table not in FAN_TABLES:
            raise IpcError("invalid fan table", E_INVALID)
        profile = self.default_fan_curve(table)
        profile.save(self.fan_curve_path(table))
        if table == self.fields["fan_table"]:
            self.load_fan_curve(table)
        return {"table": table, "profile": profile.to_dict()}

    async def cmd_fan_clean(self, args, **_):
        if args.get("action", "start") == "stop":
            await self.stop_cleaning()
            return {"seconds": 0}
        return {"seconds": await self.start_cleaning()}

    # ---- 性能 ---------------------------------------------------------------
    async def cmd_gfx_mode_set(self, args, **_):
        bios = self._require_bios()
        mode = int(args["mode"])
        if mode not in GFX_MODE_NAMES:
            raise IpcError("invalid graphics mode", E_INVALID)
        supported = self.caps.get("gfx_supported") or 0
        if not supported & GFX_SUPPORT_BITS[mode] and mode != self.caps.get("gfx_mode"):
            raise IpcError("graphics mode not supported", E_UNSUPPORTED)
        if not await self.bios_call(bios.set_gfx_mode, mode):
            current = self.caps.get("gfx_mode")
            if current is not None:
                await self.bios_call(bios.set_gfx_mode, current)
            raise IpcError("this model does not support in-OS graphics switching", E_UNSUPPORTED)
        self.caps["gfx_mode"] = await self.bios_call(bios.get_gfx_mode)
        self.broadcast_state()
        return {"mode": mode, "name": GFX_MODE_NAMES[mode]}

    async def cmd_gpu_processes(self, args, **_):
        if self.mock:
            return [{"pid": os.getpid(), "name": "mock-game", "uid": os.getuid(), "cmdline": "mock-game --fullscreen"}]
        return await self.io_call(nvidia.gpu_processes)

    async def cmd_kill_process(self, args, uid=-1, **_):
        if self.mock:
            return {"pid": int(args["pid"])}
        ok, error = await self.io_call(nvidia.kill_process, int(args["pid"]), uid)
        if not ok:
            raise IpcError(error, E_DENIED if error == "permission denied" else E_FAILED)
        return {"pid": int(args["pid"])}

    async def cmd_nvidia_powerd(self, args, **_):
        if self.powerd == "missing":
            raise IpcError("nvidia-powerd is not installed", E_UNSUPPORTED)
        if not await self.io_call(nvidia.powerd_set, bool(args["enable"])):
            raise IpcError("systemctl failed", E_FAILED)
        self.powerd = await self.io_call(nvidia.powerd_status)
        self.broadcast_state()
        return {"nvidia_powerd": self.powerd}

    # ---- 灯光 ---------------------------------------------------------------
    def _iface(self, device: str) -> str:
        return self.settings["lb_protocol" if device == LIGHT_LIGHTBAR else "kb_protocol"]

    @staticmethod
    def _device(args) -> str:
        device = args.get("device", LIGHT_KEYBOARD)
        if device not in (LIGHT_KEYBOARD, LIGHT_LIGHTBAR):
            raise IpcError("invalid lighting device", E_INVALID)
        return device

    async def cmd_lighting_get(self, args, **_):
        bios = self._require_bios()
        if self.caps.get("kb_type", KB_NONE) < 0:
            raise IpcError("keyboard lighting not supported", E_UNSUPPORTED)

        def work():
            colors = bios.get_zone_static_color()
            return {"brightness": bios.get_zone_brightness(), "animation": bios.get_current_animation_effect(),
                    "colors": [list(c) for c in colors] if colors else None}
        return await self.bios_call(work)

    async def cmd_lighting_brightness(self, args, **_):
        bios = self._require_bios()
        device = self._device(args)
        value = int(args["value"])
        if not 0 <= value <= 255:
            raise IpcError("brightness out of range", E_INVALID)
        if not await self.bios_call(bios.set_zone_brightness, device, value, self._iface(device)):
            raise IpcError("BIOS rejected the command", E_FAILED)
        return {"brightness": value}

    async def cmd_lighting_backlight(self, args, **_):
        bios = self._require_bios()
        on = bool(args.get("on", True))
        if not await self.bios_call(bios.set_zone_brightness, LIGHT_KEYBOARD, 228 if on else 100, IFACE_BASIC):
            raise IpcError("BIOS rejected the command", E_FAILED)
        return {"on": on}

    async def cmd_lighting_color(self, args, **_):
        bios = self._require_bios()
        device = self._device(args)
        zone = int(args.get("zone", -1))
        color = tuple(int(c) for c in args["color"])
        if len(color) != 3 or not all(0 <= c <= 255 for c in color) or not -1 <= zone <= 3:
            raise IpcError("invalid color", E_INVALID)
        iface = self._iface(device)

        def work():
            colors = bios.get_zone_static_color() or [(0, 0, 0)] * 4
            colors = [color] * 4 if zone < 0 else [color if i == zone else c for i, c in enumerate(colors)]
            return bios.set_zone_static_color(device, colors, bios.get_zone_brightness(), iface)
        if not await self.bios_call(work):
            raise IpcError("BIOS rejected the command", E_FAILED)
        return {"zone": zone, "color": list(color)}

    async def cmd_lighting_animation(self, args, **_):
        bios = self._require_bios()
        device = self._device(args)
        anim = dict(self.settings["light_anim"])
        for key in ("effect", "speed", "direction", "theme"):
            if key in args:
                anim[key] = int(args[key])
        self.settings["light_anim"] = anim
        self.config.save()
        iface = self._iface(device)

        def work():
            custom = bios.get_zone_static_color() if anim["theme"] == 4 else None
            return bios.set_zone_animation(device, anim["effect"], anim["speed"], anim["direction"], anim["theme"],
                                           custom, bios.get_zone_brightness(), iface)
        ok = await self.bios_call(work)
        self.broadcast_state()
        if not ok:
            raise IpcError("animation not supported by this protocol", E_UNSUPPORTED)
        return anim

    # ================================================================== 运行
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(sig, self.stop_event.set)
        await self.init_hardware()
        server = await self.start_server()
        try:
            await self.restore_config()
        except Exception:  # noqa: BLE001 - 恢复失败时仍保持服务可用，便于用户修正设置
            log.exception("restoring configuration failed")
        await self.apply_undervolt()
        for loop_coro in (self.monitor_loop(), self.gpu_loop(), self.fan_loop(), self.heartbeat_loop(),
                          self.power_loop()):
            self.spawn(loop_coro)
        if not self.mock:
            self.omen_key = OmenKeyListener(self.loop, self.on_omen_key)
            self.omen_key.start()
        log.info("omenhubd %s ready on %s", __version__, self.socket_path)
        try:
            await self.stop_event.wait()
        finally:
            log.info("shutting down")
            server.close()
            for writer in list(self.connections):
                writer.close()
            for task in list(self.tasks):
                task.cancel()
            await self.shutdown()
            try:
                await asyncio.wait_for(server.wait_closed(), 2)
            except asyncio.TimeoutError:
                pass
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    async def shutdown(self) -> None:
        if self.omen_key:
            self.omen_key.stop()
        if self.cleaning_until and self.caps.get("clean_creek") == "legacy" and self.bios:
            await self.bios_call(self.bios.set_legacy_clean_creek, False)
        if self.uv_state["applied"]:
            # 降压只在服务运行期间生效：停止时恢复开机时的原始值，并标记为正常停止
            try:
                self._uv_apply_sync(None)
            except undervolt.UndervoltError as exc:
                log.error("cannot restore the original voltage offsets: %s", exc)
                self.uv_state.update(applied=False, clean=True)
                self._uv_save()
        # 与 OSH 退出时一样不再下发转速：部分机型上 0 表示停转而非自动，BIOS 会在 120 秒无心跳后自行回退
        if self.vkbd:
            self.vkbd.close()
        self.bios_pool.shutdown(wait=True)
        self.io_pool.shutdown(wait=False)


def probe(backend: str, acpi_path=None) -> dict:
    """omenhubd --probe：不启动服务，直接打印 BIOS 信息，便于反馈问题。"""
    dmi = sensors.read_dmi()
    result = {"dmi": dmi, "feature_byte": sensors.read_feature_byte(), "cpu": sensors.cpu_info(),
              "validation": None, "backend": None}
    result["validation"] = validate_product(dmi.get("product_name"), dmi.get("sys_vendor"),
                                            dmi.get("board_name"), result["feature_byte"])
    try:
        be = MockBios() if backend == "mock" else create_backend(backend, acpi_path)
    except WmiError as exc:
        result["backend"] = {"name": "none", "error": str(exc)}
        return result
    result["backend"] = be.info()
    bios = OmenBios(be, dmi.get("board_name", ""))
    table = bios.get_fan_table()
    result.update({
        "design": bios.parse_system_design_data(),
        "thermal_policy": bios.get_thermal_policy_version(),
        "perf_modes": bios.get_supported_performance_modes(),
        "fan_type": bios.get_fan_type(),
        "fan_table": table.hex() if table else None,
        "fan_table_range": bios.get_fan_table_range(),
        "fan_level": bios.get_fan_level(),
        "clean_creek": bios.is_clean_creek_supported(),
        "legacy_clean_creek": bios.is_legacy_clean_creek_supported(),
        "gpu_power_state": bios.get_gpu_power_state(),
        "gfx_mode": bios.get_gfx_mode(),
        "gfx_supported": bios.get_supported_gfx_modes(),
        "kb_type": bios.get_keyboard_type(),
        "lightbar": bios.is_lightbar_platform(),
        "sensors": [bios.get_sensor_temperature(i) for i in range(4)],
        "loadline": bios.get_loadline() if bios.is_loadline_supported() else None,
        "gpus": [kind for kind, _ in sensors.detect_gpus()],
    })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omenhubd", description="OmenSuperHub for Linux daemon")
    parser.add_argument("--config", default=paths.CONFIG_PATH, help="configuration file")
    parser.add_argument("--socket", default=paths.SOCKET_PATH, help="control socket path")
    parser.add_argument("--backend", choices=("auto", "acpi_call", "hp-wmi", "mock"), help="BIOS backend")
    parser.add_argument("--mock", action="store_true", help="simulate the hardware (testing / demo)")
    parser.add_argument("--probe", action="store_true", help="print BIOS capabilities as JSON and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s" if os.environ.get("INVOCATION_ID")
                        else "%(asctime)s %(levelname)s %(name)s: %(message)s")
    backend = "mock" if args.mock else args.backend
    if os.geteuid() != 0 and backend != "mock":
        print("omenhubd must run as root (or use --mock)", file=sys.stderr)
        return 1
    if args.probe:
        config = Config(args.config).load()
        print(json.dumps(probe(backend or config.platform["backend"], config.platform["acpi_path"]),
                         ensure_ascii=False, indent=2, default=str))
        return 0
    daemon = Daemon(args.config, args.socket, backend, mock=args.mock)
    try:
        asyncio.run(daemon.run())
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
