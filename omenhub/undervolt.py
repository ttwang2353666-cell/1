"""CPU 降压（ThrottleStop 的 FIVR 功能）：通过 Intel 超频邮箱 MSR 0x150 读写电压偏移。

与 ThrottleStop、intel-undervolt 使用同一个接口。只调整核心（plane 0）与缓存（plane 2）：
12 代起核心与缓存共用电压，两者需要设为相同的偏移。偏移以 1/1.024 mV 为单位，写入后立即生效，
重新开机后由 CPU 清零。

以下情况无法降压：
- 不是 Intel CPU；
- 内核处于 lockdown 模式（通常由安全启动开启），禁止程序写 MSR；
- BIOS 锁定了降压（Plundervolt 修复、12 代起的 Undervolt Protection）：写入会被忽略，由读回校验发现。

注意：读取偏移也要先向 MSR 0x150 写入读取请求，内核会为此记录一条警告，
因此只在用户设置了降压或主动读取时才访问 MSR。
"""

import json
import os
import struct
import subprocess
import threading
import time

from . import sensors

MSR_OC_MAILBOX = 0x150
PLANES = {"core": 0, "cache": 2}
MIN_OFFSET = -200  # mV：可设置的最大降压幅度
MAX_OFFSET = 0     # 不允许加压
LOCKDOWN_PATH = "/sys/kernel/security/lockdown"
BOOT_ID_PATH = "/proc/sys/kernel/random/boot_id"

_BUSY = 1 << 63
_CMD_READ = 0x10
_CMD_WRITE = 0x11


class UndervoltError(Exception):
    """访问超频邮箱失败；code 为 "rejected" 时表示 CPU / BIOS 拒绝了写入。"""

    def __init__(self, message: str, code: str = "msr_error"):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------- 编码
def encode_offset(mv: float) -> int:
    """电压偏移（mV）编码为邮箱数据的第 21~31 位：11 位补码，单位 1/1.024 mV。"""
    return (round(mv * 1.024) & 0x7FF) << 21


def decode_offset(value: int) -> float:
    field = (value >> 21) & 0x7FF
    if field & 0x400:
        field -= 0x800
    return field / 1.024


def read_request(plane: int) -> int:
    return (1 << 63) | (plane << 40) | (_CMD_READ << 32)


def write_request(plane: int, mv: float) -> int:
    return (1 << 63) | (plane << 40) | (_CMD_WRITE << 32) | encode_offset(mv)


def same_offset(a: float, b: float) -> bool:
    """按编码后的单位比较两个偏移，避免浮点误差。"""
    return encode_offset(a) == encode_offset(b)


def valid_offset(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and MIN_OFFSET <= value <= MAX_OFFSET


# ---------------------------------------------------------------------------- 设备
class MsrDevice:
    """/dev/cpu/0/msr：文件偏移量即 MSR 编号，每次读写 8 字节。"""

    def __init__(self, cpu: int = 0):
        self.path = f"/dev/cpu/{cpu}/msr"

    def ensure(self) -> bool:
        if not os.path.exists(self.path):
            try:
                subprocess.run(["modprobe", "msr"], check=False, capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                pass
        return os.path.exists(self.path)

    def read(self, msr: int) -> int:
        fd = os.open(self.path, os.O_RDONLY)
        try:
            data = os.pread(fd, 8, msr)
        finally:
            os.close(fd)
        if len(data) != 8:
            raise OSError(f"short read from MSR {msr:#x}")
        return struct.unpack("<Q", data)[0]

    def write(self, msr: int, value: int) -> None:
        fd = os.open(self.path, os.O_WRONLY)
        try:
            os.pwrite(fd, struct.pack("<Q", value), msr)
        finally:
            os.close(fd)


class FakeMsrDevice:
    """模拟超频邮箱（--mock 与测试使用）；locked=True 时像 BIOS 锁定那样忽略写入。"""

    def __init__(self, offsets: dict = None, locked: bool = False):
        self.raw = {plane: 0 for plane in range(5)}
        for name, mv in (offsets or {}).items():
            self.raw[PLANES[name]] = encode_offset(mv)
        self.locked = locked
        self.response = 0
        self.requests = []

    def ensure(self) -> bool:
        return True

    def write(self, msr: int, value: int) -> None:
        if msr != MSR_OC_MAILBOX:
            raise OSError(f"unexpected MSR {msr:#x}")
        self.requests.append(value)
        plane = (value >> 40) & 0x7
        if (value >> 32) & 0xFF == _CMD_WRITE and not self.locked:
            self.raw[plane] = value & 0xFFE00000
        self.response = self.raw[plane]

    def read(self, msr: int) -> int:
        return self.response

    def offset(self, name: str) -> float:
        return decode_offset(self.raw[PLANES[name]])


class Mailbox:
    def __init__(self, device):
        self.device = device
        self.lock = threading.Lock()

    def _transact(self, request: int) -> int:
        with self.lock:
            try:
                self.device.write(MSR_OC_MAILBOX, request)
                for _ in range(20):
                    value = self.device.read(MSR_OC_MAILBOX)
                    if not value & _BUSY:
                        break
                    time.sleep(0.001)
                else:
                    raise UndervoltError("the overclocking mailbox stays busy")
            except OSError as exc:
                raise UndervoltError(f"MSR {MSR_OC_MAILBOX:#x}: {exc}") from exc
        status = (value >> 32) & 0xFF
        if status:
            code = "rejected" if (request >> 32) & 0xFF == _CMD_WRITE else "msr_error"
            raise UndervoltError(f"overclocking mailbox error {status:#x}", code)
        return value

    def read_offset(self, plane: int) -> float:
        return decode_offset(self._transact(read_request(plane)))

    def write_offset(self, plane: int, mv: float) -> float:
        """写入后读回校验：BIOS 锁定降压时写入会被忽略，读回的仍是原来的值。"""
        self._transact(write_request(plane, mv))
        actual = self.read_offset(plane)
        if not same_offset(actual, mv):
            raise UndervoltError(f"offset {mv} mV was not applied (read back {actual:.1f} mV)", "rejected")
        return actual

    def read_all(self) -> dict:
        return {name: self.read_offset(plane) for name, plane in PLANES.items()}

    def write_all(self, offsets: dict) -> dict:
        return {name: self.write_offset(PLANES[name], offsets[name]) for name in PLANES}


# ---------------------------------------------------------------------------- 系统状态
def lockdown_mode() -> str:
    """内核 lockdown 模式：none / integrity / confidentiality（方括号中的是当前值）。"""
    for word in (sensors.read_file(LOCKDOWN_PATH) or "").split():
        if word.startswith("["):
            return word.strip("[]")
    return "none"


def unsupported_reason(cpu_vendor: str):
    """不访问 MSR 的前置检查；返回 None 表示可以尝试，否则返回原因代码。"""
    if cpu_vendor != "GenuineIntel":
        return "not_intel"
    if lockdown_mode() != "none":
        return "lockdown"
    return None


def boot_id() -> str:
    return sensors.read_file(BOOT_ID_PATH) or ""


def load_state(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def save_state(path: str, state: dict) -> None:
    """立即落盘：异常关机检测依赖它在死机前已经写入磁盘。"""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
