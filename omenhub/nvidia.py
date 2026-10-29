"""NVIDIA 相关功能（GpuAppManager.cs 的 Linux 版）。

  * nvidia-smi：型号、功率限制、温度墙、频率限制（--lock-gpu-clocks）
  * NVML（ctypes）：核心 / 显存频率偏移，替代 Windows 版的 NvAPIWrapper
  * /proc 扫描：占用独显的进程（替代 nvidia-smi --query-compute-apps，可看到图形程序）
  * nvidia-powerd：Linux 下 Dynamic Boost 的提供者

Windows 版的“DB 解锁”依赖替换 nvpcf 驱动包，“最大帧率”“热切换”依赖 NvAPI 驱动配置，
Linux 下没有对应接口，因此未移植。
"""

import ctypes
import logging
import os
import re
import shutil
import signal
import subprocess

log = logging.getLogger(__name__)

_NVIDIA_DEV_RE = re.compile(r"^/dev/nvidia\d+$")


def smi(*args, timeout: float = 10.0):
    """运行 nvidia-smi，失败返回 None。"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        proc = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("nvidia-smi %s failed: %s", " ".join(args), exc)
        return None
    if proc.returncode != 0:
        log.debug("nvidia-smi %s: %s", " ".join(args), (proc.stderr or proc.stdout).strip())
        return None
    return proc.stdout


def _query(field: str):
    out = smi(f"--query-gpu={field}", "--format=csv,noheader,nounits")
    if not out or not out.strip():
        return None
    value = out.strip().splitlines()[0].strip()
    return None if value.startswith("[") or value in ("N/A", "") else value


def gpu_name():
    return _query("name")


def driver_version():
    return _query("driver_version")


def max_graphics_clock() -> int:
    value = _query("clocks.max.graphics")
    try:
        return int(float(value)) if value else 0
    except ValueError:
        return 0


def power_limits():
    """GetGpuPowerLimits：返回 (当前功率限制, 最大功率限制)，失败返回 None。"""
    out = smi("-q", "-d", "POWER")
    if out:
        current = re.search(r"Current Power Limit\s+:\s+([\d.]+)\s+W", out)
        maximum = re.search(r"Max Power Limit\s+:\s+([\d.]+)\s+W", out)
        if current and maximum:
            return float(current.group(1)), float(maximum.group(1))
    limit, max_limit = _query("power.limit"), _query("power.max_limit")
    try:
        if limit and max_limit:
            return float(limit), float(max_limit)
    except ValueError:
        pass
    return None


def target_temperature():
    """GetGpuTemperatureTarget：GPU 目标温度（温度墙），失败返回 None。"""
    out = smi("-q", "-d", "TEMPERATURE")
    if not out:
        return None
    for pattern in (r"GPU Target Temperature\s+:\s+(\d+)\s+C", r"GPU Max Operating Temp\s+:\s+(\d+)\s+C"):
        match = re.search(pattern, out)
        if match and int(match.group(1)) > 50:
            return int(match.group(1))
    return None


def lock_gpu_clock(mhz: int) -> bool:
    """SetGPUClockLimit：限制最高核心频率（不是超频）。"""
    return smi(f"--lock-gpu-clocks=0,{int(mhz)}") is not None


def reset_gpu_clock() -> bool:
    return smi("--reset-gpu-clocks") is not None


# ---------------------------------------------------------------------------- 进程
def gpu_processes(exclude_pid: int = None):
    """扫描 /proc，找出打开了 /dev/nvidiaN 的进程。"""
    result = []
    own = os.getpid() if exclude_pid is None else exclude_pid
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return result
    for pid in pids:
        if pid == own:
            continue
        fd_dir = f"/proc/{pid}/fd"
        try:
            with os.scandir(fd_dir) as entries:
                uses_gpu = False
                for entry in entries:
                    try:
                        if _NVIDIA_DEV_RE.match(os.readlink(entry.path)):
                            uses_gpu = True
                            break
                    except OSError:
                        continue
        except OSError:
            continue
        if not uses_gpu:
            continue
        try:
            uid = os.stat(f"/proc/{pid}").st_uid
            with open(f"/proc/{pid}/comm", "r", encoding="utf-8", errors="replace") as f:
                name = f.read().strip()
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except OSError:
            continue
        result.append({"pid": pid, "name": name, "uid": uid, "cmdline": cmdline[:200]})
    return result


def kill_process(pid: int, requester_uid: int):
    """结束进程：只允许 root 或进程所有者操作。返回 (成功, 错误信息)。"""
    try:
        owner = os.stat(f"/proc/{pid}").st_uid
    except OSError:
        return False, "process not found"
    if requester_uid != 0 and requester_uid != owner:
        return False, "permission denied"
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return False, str(exc)
    return True, ""


# ---------------------------------------------------------------------------- NVML 频率偏移
NVML_CLOCK_GRAPHICS = 0
NVML_CLOCK_MEM = 2
NVML_PSTATE_0 = 0


class _ClockOffsetV1(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint), ("type", ctypes.c_int), ("pstate", ctypes.c_int),
                ("clockOffsetMHz", ctypes.c_int), ("minClockOffsetMHz", ctypes.c_int),
                ("maxClockOffsetMHz", ctypes.c_int)]


class NvmlError(Exception):
    pass


class Nvml:
    """最小化的 NVML 绑定，仅用于设置频率偏移。用法：with Nvml() as nv: nv.set_clock_offset(...)"""

    def __init__(self):
        try:
            self.lib = ctypes.CDLL("libnvidia-ml.so.1")
        except OSError as exc:
            raise NvmlError(f"libnvidia-ml.so.1 not found: {exc}") from exc
        self.handle = ctypes.c_void_p()

    def _check(self, rc: int, what: str):
        if rc != 0:
            try:
                self.lib.nvmlErrorString.restype = ctypes.c_char_p
                msg = self.lib.nvmlErrorString(rc).decode()
            except (AttributeError, OSError):
                msg = f"error {rc}"
            raise NvmlError(f"{what}: {msg}")

    def __enter__(self):
        self._check(self.lib.nvmlInit_v2(), "nvmlInit")
        try:
            self._check(self.lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(self.handle)),
                        "nvmlDeviceGetHandleByIndex")
        except NvmlError:
            self.lib.nvmlShutdown()
            raise
        return self

    def __exit__(self, *exc):
        self.lib.nvmlShutdown()

    def set_clock_offset(self, domain: str, mhz: int) -> None:
        """domain: core / mem。优先使用新接口 nvmlDeviceSetClockOffsets（R555+）。"""
        clock_type = NVML_CLOCK_GRAPHICS if domain == "core" else NVML_CLOCK_MEM
        if hasattr(self.lib, "nvmlDeviceSetClockOffsets"):
            info = _ClockOffsetV1()
            info.version = ctypes.sizeof(_ClockOffsetV1) | (1 << 24)
            info.type = clock_type
            info.pstate = NVML_PSTATE_0
            info.clockOffsetMHz = int(mhz)
            if self.lib.nvmlDeviceSetClockOffsets(self.handle, ctypes.byref(info)) == 0:
                return
        func = self.lib.nvmlDeviceSetGpcClkVfOffset if domain == "core" else self.lib.nvmlDeviceSetMemClkVfOffset
        self._check(func(self.handle, ctypes.c_int(int(mhz))), f"set {domain} clock offset")


def set_clock_offsets(core=None, mem=None):
    """设置核心 / 显存频率偏移（MHz），None 表示不修改。返回错误信息或 None。"""
    if core is None and mem is None:
        return None
    try:
        with Nvml() as nv:
            if core is not None:
                nv.set_clock_offset("core", core)
            if mem is not None:
                nv.set_clock_offset("mem", mem)
    except (NvmlError, AttributeError, OSError) as exc:
        log.warning("NVML clock offset failed: %s", exc)
        return str(exc)
    return None


# ---------------------------------------------------------------------------- nvidia-powerd
POWERD_UNIT = "nvidia-powerd.service"


def powerd_status() -> str:
    """active / inactive / missing。"""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return "missing"
    try:
        listed = subprocess.run([systemctl, "list-unit-files", POWERD_UNIT, "--no-legend"],
                                capture_output=True, text=True, timeout=10)
        if POWERD_UNIT not in listed.stdout:
            return "missing"
        state = subprocess.run([systemctl, "is-active", POWERD_UNIT], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "missing"
    return "active" if state.stdout.strip() == "active" else "inactive"


def powerd_set(enable: bool) -> bool:
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    action = "enable" if enable else "disable"
    try:
        proc = subprocess.run([systemctl, action, "--now", POWERD_UNIT], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0
