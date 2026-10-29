"""HP WMI BIOS 接口传输层。

Windows 版 OSH 通过 WMI 类 ``root\\wmi:hpqBIntM`` 的 ``hpqBIOSInt{0,4,128,1024,4096}``
方法与 BIOS 通信（见 OmenHardware.SendOmenBiosWmi）。在 Linux 下，同一个 BIOS 方法
就是 ACPI 方法 ``\\_SB.WMID.WMAA(instance, method_id, buffer)``，内核 hp-wmi 驱动也是
这样调用的（hp_wmi_perform_query）。用户态可以借助 ``acpi_call`` 内核模块直接调用它。

提供三种后端：
  * AcpiCallBackend    —— 通过 /proc/acpi/call 调用，功能与 Windows 版完全一致；
  * HpWmiSysfsBackend  —— 无 acpi_call 时的降级方案，把少量命令翻译为 hp-wmi 驱动的 sysfs 接口；
  * MockBios           —— 模拟 BIOS，用于测试与演示。
"""

import logging
import os
import struct
import subprocess
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

HP_BIOS_GUID = "5FB7F034-2C63-45E9-BE91-3D44E2C707E4"
SIGNATURE = b"SECU"  # 0x55434553

# BIOS 命令（Command 字段）
CMD_READ = 0x01
CMD_WRITE = 0x02
CMD_GAMING = 0x20008    # 游戏本命令集，OSH 的默认值
CMD_LIGHTING = 0x20009  # 灯光命令集

# BIOS 返回码
RET_OK = 0x00
RET_WRONG_SIGNATURE = 0x02
RET_UNKNOWN_COMMAND = 0x03
RET_UNKNOWN_CMDTYPE = 0x04
RET_INVALID_PARAMETERS = 0x05

RETURN_CODE_NAMES = {
    RET_WRONG_SIGNATURE: "Wrong Signature",
    RET_UNKNOWN_COMMAND: "Command Not Available",
    RET_UNKNOWN_CMDTYPE: "Unknown Command Type",
    RET_INVALID_PARAMETERS: "Input or Output Size Too Small",
}

ACPI_CALL_PROC = "/proc/acpi/call"
DEFAULT_METHOD_PATH = "\\_SB.WMID.WMAA"


class WmiError(Exception):
    """传输层失败（无法调用到 BIOS），区别于 BIOS 返回的错误码。"""


@dataclass
class WmiResult:
    return_code: int
    data: bytes

    @property
    def ok(self) -> bool:
        return self.return_code == RET_OK


def method_id_for_outsize(outsize: int) -> int:
    """输出大小 -> WMI 方法号（hpqBIOSInt0/4/128/1024/4096 依次为 1~5）。"""
    if outsize < 0 or outsize > 4096:
        raise ValueError(f"invalid output size {outsize}")
    if outsize > 1024:
        return 5
    if outsize > 128:
        return 4
    if outsize > 4:
        return 3
    if outsize > 0:
        return 2
    return 1


def build_bios_args(command: int, command_type: int, data) -> bytes:
    """构造 hpqBDataIn：Sign, Command, CommandType, Size, Data。

    与内核一致，数据区至少填充到 128 字节，Size 仍为实际长度。
    """
    payload = bytes(data) if data else b""
    size = len(payload)
    if size < 128:
        payload += bytes(128 - size)
    return struct.pack("<4sIII", SIGNATURE, command, command_type, size) + payload


def parse_bios_return(raw: bytes, outsize: int) -> WmiResult:
    """解析 hpqBDataOut：sigpass(u32), rwReturnCode(u32), Data[outsize]。"""
    if len(raw) < 8:
        raise WmiError(f"BIOS returned a too short buffer ({len(raw)} bytes)")
    _sigpass, code = struct.unpack_from("<II", raw, 0)
    if code != RET_OK or outsize == 0:
        return WmiResult(code, b"")
    body = raw[8:8 + outsize]
    return WmiResult(code, body + bytes(outsize - len(body)))


def parse_acpi_call_output(text: str) -> bytes:
    """解析 acpi_call 的输出，如 ``{0x53, 0x45, ...}``。"""
    text = text.strip("\x00 \r\n\t")
    if not text or text == "not called":
        raise WmiError("acpi_call returned nothing")
    if text.startswith("Error"):
        raise WmiError(f"acpi_call: {text}")
    if not text.startswith("{"):
        raise WmiError(f"unexpected acpi_call result: {text[:80]}")
    body = text[1:]
    if not body.endswith("}"):
        log.debug("acpi_call output truncated")
    body = body.rstrip("},")
    try:
        return bytes(int(tok, 16) for tok in body.split(",") if tok.strip())
    except ValueError as exc:
        raise WmiError(f"cannot parse acpi_call result: {text[:80]}") from exc


def _read_text(path: str):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _write_text(path: str, value: str) -> None:
    with open(path, "w", encoding="ascii") as f:
        f.write(value)


def find_wmi_method_path(guid: str = HP_BIOS_GUID, sysfs: str = "/sys/bus/wmi/devices"):
    """从 sysfs 推导 BIOS 方法的 ACPI 路径，例如 ``\\_SB_.WMID.WMAA``。

    WMI 设备挂在 PNP0C14 平台设备之下，后者的 firmware_node/path 给出 ACPI 路径，
    方法名为 ``WM`` + object_id（内核 get_acpi_method_name）。
    """
    try:
        names = sorted(os.listdir(sysfs))
    except OSError:
        return None
    for name in names:
        dev = os.path.join(sysfs, name)
        dev_guid = _read_text(os.path.join(dev, "guid")) or name[:36]
        if dev_guid.upper() != guid.upper():
            continue
        object_id = _read_text(os.path.join(dev, "object_id"))
        if not object_id or len(object_id) != 2:
            continue
        parent = os.path.dirname(os.path.realpath(dev))
        for _ in range(8):
            acpi_path = _read_text(os.path.join(parent, "firmware_node", "path"))
            if acpi_path:
                return f"{acpi_path}.WM{object_id}"
            parent = os.path.dirname(parent)
    return None


class Backend:
    """BIOS 调用后端基类。"""

    name = "none"

    def call(self, command: int, command_type: int, data, outsize: int) -> WmiResult:
        raise NotImplementedError

    def info(self) -> dict:
        return {"name": self.name}

    def close(self) -> None:
        pass


class AcpiCallBackend(Backend):
    """通过 acpi_call 内核模块调用 BIOS 的 WMI 方法。"""

    name = "acpi_call"

    def __init__(self, method_path=None, proc_path: str = ACPI_CALL_PROC):
        self.proc_path = proc_path
        self.method_path = method_path or find_wmi_method_path() or DEFAULT_METHOD_PATH
        # acpi_call 只有一个全局结果缓冲区，写入与读取必须成对串行
        self._lock = threading.Lock()

    @staticmethod
    def ensure_module(proc_path: str = ACPI_CALL_PROC) -> bool:
        if os.path.exists(proc_path):
            return True
        try:
            subprocess.run(["modprobe", "acpi_call"], capture_output=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("modprobe acpi_call failed: %s", exc)
        return os.path.exists(proc_path)

    def raw_call(self, line: str) -> str:
        with self._lock:
            fd = os.open(self.proc_path, os.O_WRONLY)
            try:
                os.write(fd, line.encode("ascii"))
            finally:
                os.close(fd)
            fd = os.open(self.proc_path, os.O_RDONLY)
            try:
                chunks = []
                while True:
                    chunk = os.read(fd, 16384)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(fd)
        return b"".join(chunks).decode("ascii", "replace")

    def call(self, command, command_type, data, outsize):
        args = build_bios_args(command, command_type, data)
        mid = method_id_for_outsize(outsize)
        try:
            out = self.raw_call(f"{self.method_path} 0x0 0x{mid:x} b{args.hex()}")
        except OSError as exc:
            raise WmiError(f"acpi_call I/O error: {exc}") from exc
        return parse_bios_return(parse_acpi_call_output(out), outsize)

    def verify(self) -> None:
        """用心跳命令（风扇数量）确认 BIOS 方法存在且能响应；BIOS 返回错误码也算可用。"""
        self.call(CMD_GAMING, 0x10, bytes(4), 4)

    def info(self):
        return {"name": self.name, "method": self.method_path}


class HpWmiSysfsBackend(Backend):
    """降级后端：没有 acpi_call 时，把部分命令翻译为内核 hp-wmi 驱动的 sysfs 接口。

    支持：性能模式(0x1A) -> platform_profile、最大风扇(0x27)/手动转速(0x2E) -> hwmon pwm、
    风扇转速读取(0x2D)、风扇数量(0x10)、显卡模式(0x52) -> gpu_mux_mode。其余命令视为不支持。
    """

    name = "hp-wmi"
    PLATFORM_DIR = "/sys/devices/platform/hp-wmi"
    PLATFORM_PROFILE = "/sys/firmware/acpi/platform_profile"
    PROFILE_CHOICES = "/sys/firmware/acpi/platform_profile_choices"

    def __init__(self, max_fan_level: int = 64):
        self.hwmon = self._find_hwmon("hp")
        self.max_fan_level = max_fan_level  # 单位 100 RPM，用于 pwm 换算
        self.mux_path = os.path.join(self.PLATFORM_DIR, "gpu_mux_mode")

    @classmethod
    def available(cls) -> bool:
        return os.path.isdir(cls.PLATFORM_DIR)

    @staticmethod
    def _find_hwmon(wanted: str):
        base = "/sys/class/hwmon"
        try:
            for entry in sorted(os.listdir(base)):
                path = os.path.join(base, entry)
                if _read_text(os.path.join(path, "name")) == wanted:
                    return path
        except OSError:
            pass
        return None

    def _hw(self, attr):
        return os.path.join(self.hwmon, attr) if self.hwmon else None

    def _fail(self):
        return WmiResult(RET_UNKNOWN_CMDTYPE, b"")

    def _ok(self, data: bytes = b"", outsize: int = 0):
        return WmiResult(RET_OK, (data + bytes(max(0, outsize - len(data))))[:outsize])

    def _set_profile(self, mode: int) -> bool:
        choices = (_read_text(self.PROFILE_CHOICES) or "").split()
        if mode in (0x01, 0x31, 0x04):
            wanted = ["performance"]
        elif mode in (0x02, 0x50):
            wanted = ["cool", "low-power", "quiet"]
        elif mode == 0x03:
            wanted = ["quiet", "low-power", "cool"]
        else:
            wanted = ["balanced"]
        for name in wanted:
            if name in choices:
                _write_text(self.PLATFORM_PROFILE, name)
                return True
        return False

    def call(self, command, command_type, data, outsize):
        data = bytes(data or b"")
        try:
            if command == CMD_GAMING:
                if command_type == 0x1A and len(data) >= 2:
                    return self._ok() if self._set_profile(data[1]) else self._fail()
                if command_type == 0x27 and self._hw("pwm1_enable") and data:
                    _write_text(self._hw("pwm1_enable"), "0" if data[0] else "2")
                    return self._ok()
                if command_type == 0x2E and self._hw("pwm1") and len(data) >= 2:
                    _write_text(self._hw("pwm1_enable"), "1")
                    for idx, level in enumerate(data[:2]):
                        path = self._hw(f"pwm{idx + 1}")
                        if os.path.exists(path):
                            pwm = round((level & 0x7F) * 255 / max(1, self.max_fan_level))
                            _write_text(path, str(max(0, min(255, pwm))))
                    return self._ok()
                if command_type in (0x2D, 0x10) and self.hwmon:
                    levels = []
                    for idx in (1, 2, 3):
                        rpm = _read_text(self._hw(f"fan{idx}_input"))
                        if rpm is None:
                            break
                        levels.append(min(255, int(rpm) // 100))
                    if not levels:
                        return self._fail()
                    if command_type == 0x10:
                        return self._ok(bytes([len(levels)]), outsize)
                    return self._ok(bytes(levels), outsize)
            if command_type == 0x52 and os.path.exists(self.mux_path):
                if command == CMD_READ:
                    mode = int(_read_text(self.mux_path) or "0")
                    return self._ok(bytes([mode]), outsize)
                if command == CMD_WRITE and data:
                    _write_text(self.mux_path, str(data[0] & 0x7F))
                    return self._ok(b"", outsize)
        except (OSError, ValueError) as exc:
            raise WmiError(f"hp-wmi sysfs: {exc}") from exc
        return self._fail()

    def info(self):
        return {"name": self.name, "hwmon": self.hwmon,
                "platform_profile": os.path.exists(self.PLATFORM_PROFILE)}


class MockBios(Backend):
    """模拟一台暗影精灵 9（V1 热策略、双风扇、四分区键盘、NVIDIA 独显）的 BIOS。"""

    name = "mock"

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []
        design = bytearray(128)
        design[0:2] = (280).to_bytes(2, "little")  # 原装适配器 280 W
        design[3] = 1      # ThermalPolicyVersion V1
        design[4] = 0x03   # SwFanControl | Turbo
        design[5] = 120    # PL4 默认值
        design[7] = 0x0B   # UMA | Hybrid | Optimus
        design[8] = 60     # DefaultConcurrentTdp
        design[9] = 0x14   # LoadLine：4 档，默认 1
        design[10] = 0x0F  # IR / 环境 / PCH / VR 传感器
        design[11] = 0x03  # Fn+P / Fn+F1
        self.design = design
        self.fan_types = bytes([0x21, 0, 0, 0, 0, 0, 0, 0, 0x03, 0])  # CPU、GPU 风扇，均支持反转除尘
        table = bytearray(128)
        table[0:2] = bytes([2, 0])
        for i, (cpu, gpu, noise) in enumerate([(20, 22, 28), (30, 33, 33), (40, 44, 38),
                                               (50, 55, 43), (58, 64, 49)]):
            table[2 + i * 3:5 + i * 3] = bytes([cpu, gpu, noise])
        self.fan_table = table
        self.fan_set = [0, 0, 0]
        self.max_fan = False
        self.thermal = 0x30
        self.power_limits = [0, 0, 0, 0]
        self.gpu_modes = bytearray([1, 1, 1, 87])
        self.mux = 0
        self.kb_type = 1
        light = bytearray(128)
        for zone in range(4):
            light[25 + zone * 3:28 + zone * 3] = bytes([255, 0, 0])
        self.light_table = light
        self.brightness = 100
        self.animation = 0
        self.sensors = [46, 38, 52, 58]
        self.loadline = 1
        self.iccmax = 0
        self.legacy_clean = bytearray(4)

    def fan_levels(self):
        if self.max_fan:
            return [58, 64]
        if self.fan_set[0] or self.fan_set[1]:
            return [self.fan_set[0] & 0x7F, self.fan_set[1] & 0x7F]
        return [24, 26]

    def call(self, command, command_type, data, outsize):
        data = bytes(data or b"")
        with self.lock:
            self.calls.append((command, command_type, data, outsize))
            out = self._dispatch(command, command_type, data)
        if out is None:
            return WmiResult(RET_UNKNOWN_CMDTYPE, b"")
        return WmiResult(RET_OK, (bytes(out) + bytes(max(0, outsize - len(out))))[:outsize])

    def _dispatch(self, command, ctype, data):
        if command == CMD_GAMING:
            if ctype == 0x28:
                return self.design
            if ctype == 0x10:
                return bytes([2, 0, 0, 0])
            if ctype == 0x2C:
                return self.fan_types
            if ctype == 0x2F:
                return self.fan_table
            if ctype == 0x2D:
                return bytes(self.fan_levels())
            if ctype == 0x2E:
                for i, level in enumerate(data[:3]):
                    self.fan_set[i] = level
                return b""
            if ctype == 0x27:
                self.max_fan = bool(data and data[0])
                return b""
            if ctype == 0x1A:
                self.thermal = data[1] if len(data) > 1 else self.thermal
                return b""
            if ctype == 0x29:
                for i, value in enumerate(data[:4]):
                    if value != 0xFF:
                        self.power_limits[i] = value
                return b""
            if ctype == 0x21:
                return self.gpu_modes
            if ctype == 0x22:
                self.gpu_modes[:] = (data + bytes(4))[:4]
                return b""
            if ctype == 0x23:
                index = data[0] if data else 0
                return bytes([self.sensors[index] if index < 4 else 0])
            if ctype == 0x2B:
                return bytes([self.kb_type])
            if ctype == 0x37:
                if len(data) > 2 and data[1] == 13:
                    if len(data) > 4:
                        self.loadline = data[2]
                    return bytes([0, 13, self.loadline, 0])
                if len(data) > 3 and data[1] == 15:
                    self.iccmax = data[2] | (data[3] << 8)
                return b""
            return None
        if command == CMD_LIGHTING:
            if ctype == 0x01:
                return bytes([1])
            if ctype == 0x02:
                return self.light_table
            if ctype == 0x03:
                self.light_table[:] = (data + bytes(128))[:128]
                return b""
            if ctype == 0x04:
                return bytes([self.brightness])
            if ctype == 0x05:
                self.brightness = data[0] if data else self.brightness
                return b""
            if ctype == 0x07:
                self.animation = data[1] if len(data) > 1 else 0
                return b""
            if ctype == 0x0B:
                if len(data) > 3:
                    self.brightness = data[3]
                    self.animation = data[1]
                    if data[1] == 0 and len(data) >= 19:
                        for zone in range(4):
                            self.light_table[25 + zone * 3:28 + zone * 3] = data[7 + zone * 3:10 + zone * 3]
                return b""
            if ctype == 0x0C:
                return bytes([self.animation])
            return None
        if ctype == 0x52:
            if command == CMD_READ:
                return bytes([self.mux])
            if command == CMD_WRITE:
                self.mux = data[0] & 0x7F if data else self.mux
                return b""
        if ctype == 0x2C and command == CMD_READ:
            return self.legacy_clean
        if ctype == 0x2C and command == CMD_WRITE:
            self.legacy_clean[:] = (data + bytes(4))[:4]
            return b""
        if ctype == 0x01 and command == CMD_READ:
            return bytes([0x00])
        return None


def create_backend(kind: str = "auto", method_path=None, max_fan_level: int = 64) -> Backend:
    """按配置创建后端。auto：优先 acpi_call，其次 hp-wmi sysfs。"""
    kind = (kind or "auto").lower()
    if kind == "mock":
        return MockBios()
    error = None
    if kind in ("auto", "acpi_call") and AcpiCallBackend.ensure_module():
        backend = AcpiCallBackend(method_path)
        try:
            backend.verify()
            return backend
        except WmiError as exc:
            error = f"HP BIOS WMI method {backend.method_path} is not usable: {exc}"
            log.error("%s", error)
    if kind == "acpi_call":
        raise WmiError(error or "acpi_call kernel module is not available (/proc/acpi/call missing)")
    if kind in ("auto", "hp-wmi", "sysfs") and HpWmiSysfsBackend.available():
        return HpWmiSysfsBackend(max_fan_level)
    raise WmiError(error or "no usable BIOS backend: install the acpi_call kernel module")
