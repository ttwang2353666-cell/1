"""Linux 传感器：替代 Windows 版的 LibreHardwareMonitor（及其 PawnIO 驱动）。

CPU：hwmon(coretemp / k10temp / zenpower) 温度，RAPL powercap 封装功率，cpufreq 频率。
GPU：NVIDIA 通过 nvidia-smi，AMD 独显通过 amdgpu hwmon。混合模式下独显休眠时不读取，
     空闲时主动退避，避免监控本身让独显无法进入节能状态。
"""

import glob
import logging
import math
import os
import random
import shutil
import subprocess
import time

log = logging.getLogger(__name__)


def read_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def read_int(path, default=None):
    value = read_file(path)
    try:
        return int(value) if value is not None else default
    except ValueError:
        return default


# ---------------------------------------------------------------------------- 系统信息
DMI_DIR = "/sys/class/dmi/id"
DMI_KEYS = ("sys_vendor", "product_name", "product_sku", "product_family", "product_version",
            "board_name", "board_vendor", "bios_version", "bios_date")


def read_dmi() -> dict:
    return {key: read_file(os.path.join(DMI_DIR, key), "") or "" for key in DMI_KEYS}


def read_feature_byte() -> str:
    """SMBIOS 类型 11（OEM 字符串）中的 FBYTE#…，即 OSH 的 DeviceModel.FeatureByte。"""
    for path in sorted(glob.glob("/sys/firmware/dmi/entries/11-*/raw")):
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            continue
        if len(raw) < 5:
            continue
        for item in raw[raw[1]:].split(b"\0"):
            text = item.decode("ascii", "replace")
            if text.startswith("FBYTE#"):
                return text[6:]
    return ""


def cpu_info() -> dict:
    model, vendor = "", ""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "model name" and not model:
                    model = value.strip()
                elif key == "vendor_id" and not vendor:
                    vendor = value.strip()
                if model and vendor:
                    break
    except OSError:
        pass
    return {"model": model, "vendor": vendor}


def find_hwmon(name: str):
    result = []
    for path in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        if read_file(os.path.join(path, "name")) == name:
            result.append(path)
    return result


def ac_online():
    """是否连接交流电源；没有 Mains 类型电源时返回 None。"""
    found = False
    for path in glob.glob("/sys/class/power_supply/*"):
        if read_file(os.path.join(path, "type")) == "Mains":
            found = True
            if read_file(os.path.join(path, "online")) == "1":
                return True
    return False if found else None


class ResumeDetector:
    """通过 CLOCK_BOOTTIME 与 CLOCK_MONOTONIC 的差值跳变检测系统从睡眠中恢复。"""

    def __init__(self):
        self._offset = self._read()

    @staticmethod
    def _read() -> float:
        return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()

    def check(self) -> bool:
        offset = self._read()
        resumed = offset - self._offset > 2.0
        self._offset = offset
        return resumed


# ---------------------------------------------------------------------------- CPU
class CpuSensor:
    def __init__(self):
        self.temp_path = None
        self.tjmax = None
        self.source = None
        for driver in ("coretemp", "k10temp", "zenpower"):
            for hwmon in find_hwmon(driver):
                picked = self._pick_temp(hwmon, driver)
                if picked:
                    self.temp_path, self.tjmax = picked
                    self.source = driver
                    break
            if self.temp_path:
                break
        self.energy_path, self.energy_range = self._find_rapl()
        self.freq_path = "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
        self._last_energy = None
        self._last_power = None

    @staticmethod
    def _pick_temp(hwmon: str, driver: str):
        labels = {}
        for label_path in glob.glob(os.path.join(hwmon, "temp*_label")):
            labels[read_file(label_path, "")] = label_path[:-len("_label")]
        if driver == "coretemp":
            preferred = [labels.get(name) for name in labels if name.startswith("Package id")]
        else:
            preferred = [labels.get("Tdie"), labels.get("Tctl")]
        for base in preferred:
            if base and os.path.exists(base + "_input"):
                crit = read_int(base + "_crit")
                return base + "_input", (crit // 1000 if crit and crit > 0 else None)
        inputs = sorted(glob.glob(os.path.join(hwmon, "temp*_input")))
        return (inputs[0], None) if inputs else None

    @staticmethod
    def _find_rapl():
        for zone in sorted(glob.glob("/sys/class/powercap/intel-rapl:[0-9]*")):
            if os.path.basename(zone).count(":") != 1:
                continue
            if (read_file(os.path.join(zone, "name")) or "").startswith("package"):
                energy = os.path.join(zone, "energy_uj")
                if read_int(energy) is not None:
                    return energy, read_int(os.path.join(zone, "max_energy_range_uj"), 0)
        return None, None

    @property
    def available(self) -> bool:
        return bool(self.temp_path or self.energy_path)

    def read(self) -> dict:
        temp = read_int(self.temp_path) if self.temp_path else None
        result = {"temp": temp / 1000.0 if temp is not None else None,
                  "power": self._read_power(), "freq": None}
        freq = read_int(self.freq_path)
        if freq:
            result["freq"] = freq / 1000.0
        return result

    def _read_power(self):
        if not self.energy_path:
            return None
        energy = read_int(self.energy_path)
        now = time.monotonic()
        if energy is None:
            return None
        last = self._last_energy
        self._last_energy = (energy, now)
        if last is None:
            return None
        delta_t = now - last[1]
        if delta_t < 0.05:
            return self._last_power
        delta_e = energy - last[0]
        if delta_e < 0:
            delta_e += self.energy_range or 0
        if delta_e < 0:
            return self._last_power
        self._last_power = delta_e / delta_t / 1e6
        return self._last_power


# ---------------------------------------------------------------------------- GPU
def _pci_attr(dev: str, attr: str):
    return read_file(os.path.join(dev, attr))


def runtime_status(dev: str) -> str:
    """独显的运行时电源状态：active / suspended / suspending / resuming / unsupported。"""
    return _pci_attr(dev, "power/runtime_status") or "unsupported"


def runtime_pm_enabled(dev: str) -> bool:
    return _pci_attr(dev, "power/control") == "auto"


class GpuMonitor:
    """独显监控状态机：

    off    —— 独显已休眠（D3），不读取，界面显示“节能”；
    idle   —— 独显醒着但空闲，暂停读取一段时间，让驱动有机会把它关掉；
    active —— 正常按刷新间隔读取。
    """

    IDLE_SAMPLES_BEFORE_REST = 3
    REST_SECONDS = 20.0

    def __init__(self, kind: str, pci_dev: str, name: str = ""):
        self.kind = kind
        self.pci = pci_dev
        self.name = name
        self._idle_samples = 0
        self._rest_until = 0.0
        self._last = {"temp": None, "power": None, "freq": None}

    def read(self) -> dict:
        status = runtime_status(self.pci)
        if status in ("suspended", "suspending"):
            self._idle_samples = 0
            return {"state": "off", "temp": None, "power": None, "freq": None}
        now = time.monotonic()
        if runtime_pm_enabled(self.pci) and now < self._rest_until:
            return {"state": "idle", **self._last}
        values = self._query()
        if values is None:
            return {"state": "error", "temp": None, "power": None, "freq": None}
        util = values.pop("util", None)
        self._last = values
        if util is not None and util <= 2 and runtime_pm_enabled(self.pci):
            self._idle_samples += 1
            if self._idle_samples >= self.IDLE_SAMPLES_BEFORE_REST:
                self._idle_samples = 0
                self._rest_until = now + self.REST_SECONDS
        else:
            self._idle_samples = 0
        return {"state": "active", **values}

    def _query(self):
        raise NotImplementedError


class NvidiaGpuMonitor(GpuMonitor):
    FIELDS_INSTANT = "temperature.gpu,power.draw.instant,clocks.gr,utilization.gpu"
    FIELDS_LEGACY = "temperature.gpu,power.draw,clocks.gr,utilization.gpu"

    def __init__(self, pci_dev: str, name: str = ""):
        super().__init__("nvidia", pci_dev, name)
        self.smi = shutil.which("nvidia-smi")
        self._fields = self.FIELDS_INSTANT

    def _query(self):
        if not self.smi:
            return None
        # 新驱动支持 power.draw.instant；旧驱动不认识该字段或返回 N/A 时退回 power.draw
        candidates = [self._fields] if self._fields == self.FIELDS_LEGACY else \
            [self.FIELDS_INSTANT, self.FIELDS_LEGACY]
        for fields in candidates:
            try:
                proc = subprocess.run([self.smi, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
                                      capture_output=True, text=True, timeout=5)
            except (OSError, subprocess.SubprocessError) as exc:
                log.debug("nvidia-smi failed: %s", exc)
                return None
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            parts = [p.strip() for p in proc.stdout.strip().splitlines()[0].split(",")]
            values = ([_to_float(p) for p in parts] + [None] * 4)[:4]
            if values[1] is None and fields == self.FIELDS_INSTANT:
                continue
            self._fields = fields
            return {"temp": values[0], "power": values[1], "freq": values[2], "util": values[3]}
        return None


class AmdGpuMonitor(GpuMonitor):
    def __init__(self, pci_dev: str, name: str = ""):
        super().__init__("amd", pci_dev, name)
        hwmons = sorted(glob.glob(os.path.join(pci_dev, "hwmon", "hwmon*")))
        self.hwmon = hwmons[0] if hwmons else None

    def _query(self):
        if not self.hwmon:
            return None
        temp = read_int(os.path.join(self.hwmon, "temp1_input"))
        power = read_int(os.path.join(self.hwmon, "power1_average"))
        if power is None:
            power = read_int(os.path.join(self.hwmon, "power1_input"))
        freq = read_int(os.path.join(self.hwmon, "freq1_input"))
        util = read_int(os.path.join(self.pci, "gpu_busy_percent"))
        if temp is None and power is None:
            return None
        return {"temp": temp / 1000.0 if temp is not None else None,
                "power": power / 1e6 if power is not None else None,
                "freq": freq / 1e6 if freq is not None else None,
                "util": util}


def _to_float(text: str):
    try:
        value = float(text)
        return None if math.isnan(value) else value
    except ValueError:
        return None


def detect_gpus():
    """返回独显列表 [(kind, pci_path)]：NVIDIA 全部计入，AMD 仅计入显存≥2GiB 的独显。"""
    result = []
    for dev in sorted(glob.glob("/sys/bus/pci/devices/*")):
        cls = _pci_attr(dev, "class") or ""
        if not cls.startswith(("0x0300", "0x0302", "0x0380")):
            continue
        vendor = _pci_attr(dev, "vendor")
        if vendor == "0x10de":
            result.append(("nvidia", dev))
        elif vendor == "0x1002":
            vram = read_int(os.path.join(dev, "mem_info_vram_total"), 0)
            if vram >= 2 * 1024 ** 3:
                result.append(("amd", dev))
    return result


def create_gpu_monitor(name: str = ""):
    gpus = detect_gpus()
    if not gpus:
        return None
    kind, dev = gpus[0]
    if kind == "nvidia":
        return NvidiaGpuMonitor(dev, name)
    return AmdGpuMonitor(dev, name)


# ---------------------------------------------------------------------------- 模拟
class MockSensors:
    """模拟的 CPU / GPU 负载与温度，温度随风扇转速变化，用于测试与演示。"""

    def __init__(self, bios=None):
        self.bios = bios
        self.t0 = time.monotonic()
        self.cpu_temp = 55.0
        self.gpu_temp = 48.0
        self.tjmax = 100
        self.available = True
        self.name = "NVIDIA GeForce RTX 4060 Laptop GPU (mock)"

    def _fan(self) -> float:
        if self.bios is not None:
            levels = self.bios.fan_levels()
            return sum(levels) / max(1, len(levels)) * 100
        return 3000.0

    def read_cpu(self) -> dict:
        t = time.monotonic() - self.t0
        load = 0.5 + 0.45 * math.sin(t / 25.0)
        power = 12 + 95 * load + random.uniform(-2, 2)
        target = 40 + power * 0.55 - self._fan() / 400
        self.cpu_temp += (target - self.cpu_temp) * 0.15
        return {"temp": round(self.cpu_temp + random.uniform(-0.4, 0.4), 2), "power": round(power, 2),
                "freq": round(1200 + 3600 * load, 0)}

    def read_gpu(self) -> dict:
        t = time.monotonic() - self.t0
        load = 0.5 + 0.5 * math.sin(t / 40.0 + 1)
        power = 8 + 110 * load
        target = 36 + power * 0.35 - self._fan() / 500
        self.gpu_temp += (target - self.gpu_temp) * 0.12
        return {"state": "active", "temp": round(self.gpu_temp, 2), "power": round(power, 2),
                "freq": round(300 + 2100 * load, 0)}
