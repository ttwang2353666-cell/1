# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""BIOS 命令层：移植自 OmenHardware.cs 与 App/OmenLighting.cs。

所有命令与 Windows 版保持一致（CommandType、数据布局、输出大小），只是底层
SendOmenBiosWmi 换成了 wmi.Backend。
"""

import logging
import threading
import time

from .wmi import (CMD_GAMING, CMD_LIGHTING, CMD_READ, CMD_WRITE, RET_UNKNOWN_CMDTYPE,
                  RET_UNKNOWN_COMMAND, RETURN_CODE_NAMES, Backend, WmiError)

log = logging.getLogger(__name__)

# 显卡模式（GraphicsSwitcherMode），数值与内核 hp-wmi 的 mux_bitmask_map 一致
GFX_HYBRID = 0
GFX_DISCRETE = 1
GFX_OPTIMUS = 2
GFX_UMA = 3
GFX_MODE_NAMES = {GFX_HYBRID: "Hybrid", GFX_DISCRETE: "Discrete",
                  GFX_OPTIMUS: "NVIDIA Advanced Optimus", GFX_UMA: "UMA"}
# 系统设计数据 byte[7] 中的支持位
GFX_SUPPORT_BITS = {GFX_UMA: 0x01, GFX_HYBRID: 0x02, GFX_DISCRETE: 0x04, GFX_OPTIMUS: 0x08}

# 风扇类型（FanType）
FAN_TYPE_NAMES = {0: "Unsupported", 1: "Cpu", 2: "Gpu", 3: "Exhaust", 4: "Pump",
                  5: "Intake", 6: "Vrm", 100: "LightingBoard"}

# 键盘灯光类型（NbKeyboardLightingType），-1 表示获取失败
KB_NONE = -1
KB_NORMAL = 0
KB_FOUR_ZONE_NUMPAD = 1
KB_FOUR_ZONE_NO_NUMPAD = 2
KB_PER_KEY_RGB = 3
KB_ONE_ZONE_NUMPAD = 4
KB_ONE_ZONE_NO_NUMPAD = 5
KB_FOUR_ZONE_TYPES = (KB_FOUR_ZONE_NUMPAD, KB_FOUR_ZONE_NO_NUMPAD)
KB_ONE_ZONE_TYPES = (KB_ONE_ZONE_NUMPAD, KB_ONE_ZONE_NO_NUMPAD)

# 性能模式（PerformanceMode）
PERF_DEFAULT = 0
PERF_PERFORMANCE = 1
PERF_COOL = 2
PERF_QUIET = 3
PERF_EXTREME = 4
PERF_L2 = 0x30   # V1 平衡
PERF_L7 = 0x31   # V1 狂暴 / 释放全部性能
PERF_L4 = 0x50   # V1 酷冷

# UI 层性能模式（PerformanceModeOnUI）
UI_MODES = ("default", "performance", "cool", "quiet", "extreme", "balance", "eco", "unleash")
_UI_MODE_V0_VALUE = {"default": 0, "performance": 1, "cool": 2, "quiet": 3, "extreme": 4,
                     "balance": 0, "eco": 0, "unleash": 1}

# OGH 强制使用 V0 热策略的主板（与内核 omen_thermal_profile_force_v0_boards 相同）
FORCE_V0_BOARDS = ("8607", "8746", "8747", "8748", "8749", "874A")

# 灯光
LIGHT_KEYBOARD = "keyboard"
LIGHT_LIGHTBAR = "lightbar"
IFACE_BASIC = "basic"   # WMI CommandType 2/3/4/5/7
IFACE_DOJO = "dojo"     # WMI CommandType 11
_LIGHT_TARGET = {LIGHT_LIGHTBAR: 0, LIGHT_KEYBOARD: 1}


class OmenBios:
    """OmenHardware 的 Python 版本，线程安全。"""

    def __init__(self, backend: Backend, board: str = ""):
        self.backend = backend
        self.board = (board or "").upper()
        self._lock = threading.RLock()
        self._design = None
        self._fan_type = None
        self._last_colors = {}
        self._log_times = {}

    # ------------------------------------------------------------------ 基础调用
    def call(self, command_type: int, data=None, outsize: int = 0, command: int = CMD_GAMING):
        """返回 WmiResult；传输失败时返回 None。"""
        try:
            with self._lock:
                return self.backend.call(command, command_type, data, outsize)
        except WmiError as exc:
            self._log_error(command_type, -1, str(exc))
            return None

    def send(self, command_type: int, data=None, outsize: int = 0, command: int = CMD_GAMING):
        """SendOmenBiosWmi：成功返回数据（输出大小为 0 时返回 b""），失败返回 None。"""
        res = self.call(command_type, data, outsize, command)
        if res is None:
            return None
        if res.ok:
            return res.data
        self._log_error(command_type, res.return_code, RETURN_CODE_NAMES.get(res.return_code, ""))
        return None

    def _log_error(self, command_type: int, code: int, detail: str) -> None:
        # 与 OSH 的 Logger 一样做节流：同一错误 60 秒内只记录一次
        key = (command_type, code)
        now = time.monotonic()
        if now - self._log_times.get(key, -1e9) < 60:
            return
        self._log_times[key] = now
        level = logging.DEBUG if code in (RET_UNKNOWN_COMMAND, RET_UNKNOWN_CMDTYPE) else logging.WARNING
        log.log(level, "SendOmenBiosWmi(CommandType=0x%02X) failed: %s %s", command_type,
                f"code={code}" if code >= 0 else "", detail)

    # ------------------------------------------------------------------ 系统设计数据
    def get_system_design_data(self, refresh: bool = False):
        """128 字节系统设计数据（硬件能力、传感器、热策略等），结果缓存。"""
        if self._design is None or refresh:
            data = self.send(0x28, bytes(4), 128)
            if data is not None:
                self._design = bytes(data)
        return self._design

    def get_adapter_power(self) -> int:
        data = self.get_system_design_data()
        if not data or len(data) < 2:
            return -1
        return data[0] | (data[1] << 8)

    def parse_system_design_data(self) -> dict:
        """PrintSystemDesignData 的结构化版本。"""
        data = self.get_system_design_data()
        if not data or len(data) < 12:
            return {}
        b4, b6, b9, b10, b11 = data[4], data[6], data[9], data[10], data[11]
        adapter = data[0] | (data[1] << 8)
        return {
            "raw": data.hex(),
            "adapter_power": adapter,
            "bios_performance_mode_support": adapter >= 200,
            "tgp_ppab_support": adapter >= 280,
            "thermal_policy_version": data[3],
            "sw_fan_control": bool(b4 & 0x01),
            "turbo_mode_support": bool(b4 & 0x02),
            "extreme_unlocked": bool(b4 & 0x04),
            "bios_thermal_control": bool(b4 & 0x08),
            "two_byte_pl4": bool(b4 & 0x10),
            "pl4_default": data[5],
            "amd_bios_defined_oc": bool(b6 & 0x01),
            "gfx_supported_modes": data[7],
            "default_concurrent_tdp": data[8],
            "loadline_levels": b9 & 0x0F,
            "default_loadline": (b9 >> 4) & 0x0F,
            "ir_sensor": bool(b10 & 0x01),
            "ambient_sensor": bool(b10 & 0x02),
            "pch_sensor": bool(b10 & 0x04),
            "vr_sensor": bool(b10 & 0x08),
            "hotkey_fn_p": bool(b11 & 0x01),
            "hotkey_fn_f1": bool(b11 & 0x02),
        }

    # ------------------------------------------------------------------ 显卡模式（MUX）
    def get_gfx_mode(self):
        data = self.send(82, bytes(4), 4, CMD_READ)
        if data:
            mode = data[0] & 0x7F  # 最高位可能是状态位
            if 0 <= mode <= 3:
                return mode
        return None

    def set_gfx_mode(self, mode: int, dynamic_switch: bool = False) -> bool:
        value = mode | 0x80 if dynamic_switch else mode
        return self.send(82, bytes([value, 0, 0, 0]), 0, CMD_WRITE) is not None

    def get_supported_gfx_modes(self) -> int:
        """优先取系统设计数据 byte[7]，否则按旧版逻辑：MUX 查询可用即支持 Hybrid + Discrete。"""
        design = self.get_system_design_data()
        if design and len(design) > 7 and design[7] != 0:
            return design[7]
        res = self.call(82, None, 4, CMD_READ)
        if res is not None and res.ok:
            return 0x06
        return 0

    # ------------------------------------------------------------------ 负载线 / IccMax
    def is_loadline_supported(self) -> bool:
        data = self.get_system_design_data()
        if not data or len(data) < 10:
            return False
        return (data[9] & 0x0F) > 0 and ((data[9] >> 4) & 0x0F) > 0

    def get_loadline_levels(self) -> int:
        data = self.get_system_design_data()
        return data[9] & 0x0F if data and len(data) >= 10 else 0

    def set_loadline(self, level: int) -> bool:
        data = bytearray(128)
        data[1] = 13
        data[2] = level & 0xFF
        return self.send(0x37, data, 0) is not None

    def get_loadline(self) -> int:
        data = self.send(0x37, bytes([0, 13, 0, 0]), 4)
        return data[2] if data and len(data) > 2 else -1

    def set_iccmax(self, ampere: int) -> bool:
        data = bytearray(128)
        data[1] = 15
        data[2] = ampere & 0xFF
        data[3] = (ampere >> 8) & 0xFF
        return self.send(0x37, data, 0) is not None

    # ------------------------------------------------------------------ BIOS 温度传感器
    def get_sensor_temperature(self, index: int) -> int:
        """0 IR / 1 环境 / 2 PCH / 3 VR；失败返回 -1。"""
        data = self.send(0x23, bytes([index, 0, 0, 0]), 4)
        return data[0] if data else -1

    def get_fitting_temperature(self) -> float:
        """用环境传感器温度估算 CPU 温度。"""
        temp = float(self.get_sensor_temperature(1))
        return temp if temp < 25 else temp * 1.2 - 5

    # ------------------------------------------------------------------ 风扇
    def get_fan_count(self):
        """心跳命令：维持用户定义的风扇/性能状态（BIOS 120 秒无通信会回退）。

        返回 (成功, 过流保护, 过温保护)。
        """
        data = self.send(0x10, bytes(4), 4)
        if not data or len(data) < 2:
            return False, False, False
        return True, bool(data[1] & 0x01), bool(data[1] & 0x02)

    def get_fan_level(self):
        """当前风扇转速（单位 100 RPM）。"""
        data = self.send(0x2D, bytes(4), 128)
        if data and len(data) >= 3:
            return [data[0], data[1], data[2]]
        return None

    def get_fan_table(self):
        return self.send(0x2F, bytes(4), 128)

    def get_fan_table_range(self):
        """解析 BIOS 风扇表（头 2 字节 + 每项 cpu/gpu/噪声），返回 (最小, 最大) 转速级别。"""
        data = self.get_fan_table()
        if not data or len(data) < 5 or data[0] == 0:
            return None
        levels = []
        for offset in range(2, len(data) - 2, 3):
            cpu, gpu, noise = data[offset], data[offset + 1], data[offset + 2]
            if cpu == 0 and gpu == 0 and noise == 0:
                break
            levels.extend(v for v in (cpu, gpu) if v)
        if not levels or max(levels) > 120:
            return None
        return min(levels), max(levels)

    def get_fan_type(self):
        """返回 (风扇类型列表, 反转除尘能力位列表)，与 OGH 解析方式一致。"""
        if self._fan_type is None:
            data = self.send(44, bytes(4), 128)
            types, caps = [], []
            if data:
                for i in range(4):
                    types.append(data[i] & 0x0F)
                    types.append((data[i] & 0xF0) >> 4)
                types.pop()
                for bit in range(16):
                    index = 8 + bit // 8
                    if index >= len(data):
                        break
                    caps.append(bool((data[index] >> (bit % 8)) & 1))
            self._fan_type = (types, caps)
        return self._fan_type

    def is_three_fan_supported(self) -> bool:
        types, _ = self.get_fan_type()
        return len(types) > 2 and types[2] != 0

    def is_clean_creek_supported(self) -> bool:
        types, caps = self.get_fan_type()
        return any(caps[:len(types)])

    def is_legacy_clean_creek_supported(self) -> bool:
        if self.is_clean_creek_supported():
            return False
        data = self.send(44, None, 4, CMD_READ)
        return bool(data) and bool(data[0] & 0x20)

    def set_legacy_clean_creek(self, enable: bool) -> bool:
        state = self.send(44, None, 4, CMD_READ)
        if not state or len(state) < 4:
            return False
        state = bytearray(state)
        state[3] = state[3] | 0x80 if enable else state[3] & 0x7F
        return self.send(44, state, 0, CMD_WRITE) is not None

    def set_fan_level(self, fan1: int, fan2: int, fan3: bool = False, clean: bool = False) -> bool:
        fan1 = max(0, min(127, int(fan1)))
        fan2 = max(0, min(127, int(fan2)))
        levels = [fan1, fan2]
        if fan3:
            levels.append((fan1 + fan2) // 2)
        if clean:
            types, caps = self.get_fan_type()
            caps = caps[:len(types)] + [False] * 3
            levels = [lv + 128 if caps[i] else lv for i, lv in enumerate(levels)]
        return self.send(0x2E, bytes(levels), 0) is not None

    def set_max_fan_speed(self, on: bool) -> bool:
        return self.send(0x27, bytes([1 if on else 0]), 0) is not None

    # ------------------------------------------------------------------ 性能模式
    def get_thermal_policy_version(self) -> int:
        if self.board in FORCE_V0_BOARDS:
            return 0
        data = self.get_system_design_data()
        return data[3] if data and len(data) > 3 else 0

    def is_sw_fan_control_supported(self) -> bool:
        data = self.get_system_design_data()
        return bool(data) and bool(data[4] & 0x01)

    def get_supported_performance_modes(self):
        data = self.get_system_design_data()
        if not data or len(data) < 5:
            return []
        sw_fan = bool(data[4] & 0x01)
        turbo = bool(data[4] & 0x02)
        if self.get_thermal_policy_version() == 1:
            modes = ["eco", "balance"]
            if sw_fan:
                modes.append("performance")
                if turbo:
                    modes.append("unleash")
        else:
            modes = ["eco", "default", "cool"]
            if turbo:
                modes.append("performance")
        return modes

    def performance_mode_value(self, ui_mode: str) -> int:
        """UI 模式 -> 写入 BIOS 的值（SetFanMode 的映射）。

        与 OSH 不同之处：OSH 启动时无条件写入 L7(0x31)；这里对 V0 机型改用 0x01，
        与 OGH/内核对 V0 热策略的处理一致。
        """
        if self.get_thermal_policy_version() == 1:
            if ui_mode in ("performance", "extreme", "unleash"):
                return PERF_L7
            if ui_mode == "cool":
                return PERF_L4
            return PERF_L2
        return _UI_MODE_V0_VALUE.get(ui_mode, PERF_DEFAULT)

    def set_performance_mode(self, ui_mode: str) -> bool:
        return self.set_performance_mode_raw(self.performance_mode_value(ui_mode))

    def set_performance_mode_raw(self, value: int) -> bool:
        return self.send(0x1A, bytes([0xFF, value & 0xFF]), 0) is not None

    # ------------------------------------------------------------------ 功率
    def get_gpu_power_state(self):
        """返回 (ctgp, ppab, dstate, gpu_slowdown_temp)，失败返回 None。"""
        data = self.send(0x21, bytes(4), 4)
        if not data or len(data) < 4:
            return None
        return bool(data[0]), bool(data[1]), data[2], data[3]

    def set_gpu_power_state(self, tgp: bool, ppab: bool, dstate: int = 1, gps=None) -> bool:
        """Tgp / Ppab / dState（1 标准，2 低功耗）。

        第 4 字节默认保留 BIOS 当前值（与内核 victus_s_gpu_thermal_profile_set 相同），
        读取失败时与 OSH 一样写 0。
        """
        if gps is None:
            current = self.get_gpu_power_state()
            gps = current[3] if current else 0
        data = bytes([1 if tgp else 0, 1 if ppab else 0, dstate & 0xFF, gps & 0xFF])
        return self.send(0x22, data, 0) is not None

    def set_concurrent_tdp(self, value: int) -> bool:
        """PPab 条件（Tpp）。"""
        return self.send(0x29, bytes([0xFF, 0xFF, 0xFF, value & 0xFF]), 0) is not None

    def set_cpu_power_limit(self, value: int) -> bool:
        """同时设置 PL1 与 PL2（单位 W）。"""
        value &= 0xFF
        return self.send(0x29, bytes([value, value, 0xFF, 0xFF]), 0) is not None

    def set_cpu_power_limit4(self, value: int) -> bool:
        return self.send(0x29, bytes([0xFF, 0xFF, value & 0xFF, 0xFF]), 0) is not None

    def is_two_byte_pl4_supported(self) -> bool:
        data = self.get_system_design_data()
        return bool(data) and bool(data[4] & 0x10)

    def set_pl4_double_byte(self, value: int) -> bool:
        data = bytearray(128)
        data[0] = 0x20
        data[2] = value & 0xFF
        data[3] = (value >> 8) & 0xFF
        data[6] = data[7] = data[10] = data[11] = 0xFF
        return self.send(0x37, data, 0) is not None

    # ------------------------------------------------------------------ 键盘背光 / 灯光
    def backlight(self, on: bool) -> bool:
        return self.send(0x05, bytes([0xE4 if on else 0x64]), 0, CMD_LIGHTING) is not None

    def get_keyboard_type(self) -> int:
        data = self.send(43, b"", 4)
        return data[0] if data else KB_NONE

    def is_lightbar_platform(self) -> bool:
        data = self.send(1, None, 4)
        return bool(data) and bool((data[0] >> 1) & 1)

    def get_lighting_supported(self) -> int:
        data = self.send(1, None, 128, CMD_LIGHTING)
        return data[0] & 0x01 if data else -1

    def get_zone_static_color(self):
        """四个分区的 (R, G, B)。"""
        data = self.send(2, bytes([0]), 128, CMD_LIGHTING)
        if not data or len(data) < 37:
            return None
        return [tuple(data[25 + i * 3:28 + i * 3]) for i in range(4)]

    def get_zone_brightness(self) -> int:
        data = self.send(4, bytes([0]), 128, CMD_LIGHTING)
        return data[0] if data else 0

    def get_current_animation_effect(self) -> int:
        data = self.send(12, bytes(4), 4, CMD_LIGHTING)
        return data[0] if data else -1

    def set_zone_static_color(self, device: str, colors, brightness: int, iface: str) -> bool:
        colors = [tuple(int(c) & 0xFF for c in color) for color in colors]
        if len(colors) != 4:
            raise ValueError("4 colors are required")
        self._last_colors[device] = list(colors)
        if iface == IFACE_DOJO:
            data = bytearray(128)
            data[0] = _LIGHT_TARGET[device]
            data[1] = 0  # 静态模式
            data[3] = brightness & 0xFF
            data[6] = 4
            for i, (r, g, b) in enumerate(colors):
                data[7 + i * 3:10 + i * 3] = bytes((r, g, b))
            return self.send(11, data, 0, CMD_LIGHTING) is not None
        table = self.send(2, bytes([0]), 128, CMD_LIGHTING)
        if not table or len(table) < 37:
            return False
        table = bytearray(table)
        for i, (r, g, b) in enumerate(colors):
            table[25 + i * 3:28 + i * 3] = bytes((r, g, b))
        return self.send(3, table, 0, CMD_LIGHTING) is not None

    def set_zone_animation(self, device: str, effect: int, speed: int, direction: int, theme: int,
                           custom_colors, brightness: int, iface: str) -> bool:
        if iface == IFACE_DOJO:
            data = bytearray(128)
            data[0] = _LIGHT_TARGET[device]
            data[1] = effect & 0xFF
            flags = speed & 0x03
            flags = (flags & 0xF3) | (0x08 if direction == 1 else 0x04)
            flags = (flags & 0x0F) | {0: 0x10, 1: 0x20, 2: 0x30, 3: 0x40, 4: 0x50}.get(theme, 0)
            data[2] = flags
            data[3] = brightness & 0xFF
            if theme == 4 and custom_colors:
                count = min(len(custom_colors), 4)
                data[6] = count
                for i in range(count):
                    data[7 + i * 3:10 + i * 3] = bytes(int(c) & 0xFF for c in custom_colors[i])
            return self.send(11, data, 0, CMD_LIGHTING) is not None
        # 四分区协议只支持色彩循环(2)与呼吸(4)
        drax = {2: 2, 4: 1}.get(effect)
        if drax is None:
            return False
        interval = {0: 10, 1: 5}.get(speed, 2)
        if theme == 4 and custom_colors:
            colors = [tuple(c) for c in custom_colors]
        elif self._last_colors.get(device):
            colors = [self._last_colors[device][0]]
        else:
            colors = [(255, 255, 255)]
        data = bytearray([0, drax, interval, brightness & 0xFF, len(colors)])
        for color in colors:
            data += bytes(int(c) & 0xFF for c in color)
        return self.send(7, data, 0, CMD_LIGHTING) is not None

    def set_zone_brightness(self, device: str, brightness: int, iface: str = IFACE_BASIC) -> bool:
        if iface == IFACE_DOJO:
            data = bytearray(128)
            data[0] = _LIGHT_TARGET[device]
            data[3] = brightness & 0xFF
            return self.send(11, data, 0, CMD_LIGHTING) is not None
        return self.send(5, bytes([brightness & 0xFF, 0, 0, 0]), 0, CMD_LIGHTING) is not None


# 2018~2020 年的 OMEN 主板（暗影精灵 6 及以前），OSH 不支持
_OLD_OMEN_BOARD_PREFIXES = ("84", "85", "86")


def validate_product(product_name: str, sys_vendor: str, board: str, feature_byte: str = "") -> str:
    """OmenHardware.Validation：gaming / old_omen / unsupported_hp / unsupported。

    feature_byte 为 SMBIOS OEM 字符串中的 FBYTE（OSH 的 DeviceModel.FeatureByte）。
    """
    name = (product_name or "").upper()
    vendor = (sys_vendor or "").upper()
    board = (board or "").upper()
    if "OMEN" in name:
        return "old_omen" if board.startswith(_OLD_OMEN_BOARD_PREFIXES) else "gaming"
    if "7K" in feature_byte and "fd" in feature_byte:
        if "PAVILION" in name or "VICTUS" in name:
            return "gaming"
    elif "VICTUS" in name:
        return "gaming"
    if vendor.startswith("HP") or "HEWLETT" in vendor:
        return "unsupported_hp"
    return "unsupported"


__all__ = ["OmenBios", "validate_product"]
