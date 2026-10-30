# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""温度-转速曲线：移植自 FanCurveProfile.cs 与 Program.Config.cs 中的曲线逻辑。

文件格式与 Windows 版完全相同（silent.txt / cool.txt / custom.txt），可直接复用：

    Fan_Table_CPU_Temperature_List=45,95,100
    Fan_Table_CPU_Fan_Speed_List=1600,6400,7040
    Fan_Table_GPU_Temperature_List=...
    Fan_Table_GPU_Fan_Speed_List=...

同时兼容更早的 CSV 旧格式（CPU,Fan1,Fan2,GPU,Fan1,Fan2）。
"""

import os

KEY_CPU_TEMPS = "Fan_Table_CPU_Temperature_List"
KEY_CPU_SPEEDS = "Fan_Table_CPU_Fan_Speed_List"
KEY_GPU_TEMPS = "Fan_Table_GPU_Temperature_List"
KEY_GPU_SPEEDS = "Fan_Table_GPU_Fan_Speed_List"

FAN_TABLES = ("silent", "cool", "custom")


class InvalidFanCurve(ValueError):
    pass


def _validate(points):
    temps = [t for t, _ in points]
    if len(points) < 2 or any(t < 0 or s < 0 for t, s in points) or len(set(temps)) != len(temps):
        raise InvalidFanCurve("invalid fan curve")


class FanCurveProfile:
    """CPU 与 GPU 两条曲线，每条为按温度排序的 (温度, 转速RPM) 列表。"""

    def __init__(self, cpu_points, gpu_points):
        self.cpu = sorted((int(t), int(s)) for t, s in cpu_points)
        self.gpu = sorted((int(t), int(s)) for t, s in gpu_points)

    def validate(self) -> "FanCurveProfile":
        _validate(self.cpu)
        _validate(self.gpu)
        return self

    # ---------------------------------------------------------------- 序列化
    def dumps(self) -> str:
        self.validate()
        return "\n".join([
            KEY_CPU_TEMPS + "=" + ",".join(str(t) for t, _ in self.cpu),
            KEY_CPU_SPEEDS + "=" + ",".join(str(s) for _, s in self.cpu),
            KEY_GPU_TEMPS + "=" + ",".join(str(t) for t, _ in self.gpu),
            KEY_GPU_SPEEDS + "=" + ",".join(str(s) for _, s in self.gpu),
        ]) + "\n"

    def save(self, path: str) -> None:
        text = self.dumps()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)

    @classmethod
    def loads(cls, text: str) -> "FanCurveProfile":
        lines = [line.strip() for line in text.splitlines()]
        if not any(lines):
            raise InvalidFanCurve("empty fan curve file")
        if any("=" in line for line in lines):
            return cls._load_key_value(lines)
        return cls._load_legacy_csv(lines)

    @classmethod
    def load(cls, path: str) -> "FanCurveProfile":
        with open(path, "r", encoding="utf-8-sig") as f:
            return cls.loads(f.read())

    @classmethod
    def _load_key_value(cls, lines):
        values = {}
        for line in lines:
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            try:
                values[key.strip().lower()] = [int(v.strip()) for v in value.split(",") if v.strip()]
            except ValueError as exc:
                raise InvalidFanCurve("invalid number") from exc
        try:
            ct, cs = values[KEY_CPU_TEMPS.lower()], values[KEY_CPU_SPEEDS.lower()]
            gt, gs = values[KEY_GPU_TEMPS.lower()], values[KEY_GPU_SPEEDS.lower()]
        except KeyError as exc:
            raise InvalidFanCurve(f"missing {exc}") from exc
        return cls._create(ct, cs, gt, gs)

    @classmethod
    def _load_legacy_csv(cls, lines):
        ct, cs, gt, gs = [], [], [], []
        for line in lines[1:]:  # 跳过标题行
            parts = line.split(",")
            if len(parts) < 6:
                continue
            try:
                ct.append(int(parts[0]))
                cs.append(int(parts[1]))
                gt.append(int(parts[3]))
                gs.append(int(parts[4]))
            except ValueError as exc:
                raise InvalidFanCurve("invalid number") from exc
        return cls._create(ct, cs, gt, gs)

    @classmethod
    def _create(cls, ct, cs, gt, gs):
        if len(ct) != len(cs) or len(gt) != len(gs):
            raise InvalidFanCurve("list length mismatch")
        return cls(zip(ct, cs), zip(gt, gs)).validate()

    def to_dict(self) -> dict:
        return {"cpu": [list(p) for p in self.cpu], "gpu": [list(p) for p in self.gpu]}

    @classmethod
    def from_dict(cls, data: dict) -> "FanCurveProfile":
        try:
            return cls(data["cpu"], data["gpu"]).validate()
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidFanCurve(str(exc)) from exc

    def __eq__(self, other):
        return isinstance(other, FanCurveProfile) and self.cpu == other.cpu and self.gpu == other.gpu


def interpolate(points, temperature: float) -> int:
    """GetFanSpeedForSpecificTemperature：线性插值，区间外取端点值。"""
    if not points:
        return 0
    lower = upper = points[0]
    for point in points:
        if point[0] <= temperature:
            lower = upper = point
        else:
            upper = point
            break
    if lower[0] == upper[0]:
        return lower[1]
    ratio = (temperature - lower[0]) / (upper[0] - lower[0])
    return int(lower[1] + (upper[1] - lower[1]) * ratio)


def _normalize(temperatures, speeds, temperature_max: int):
    """NormalizeDefaultFanCurve：温度截断到 [0, 上限]，同温度保留最后一个。"""
    merged = {}
    for t, s in zip(temperatures, speeds):
        merged[max(0, min(temperature_max, t))] = max(0, s)
    points = sorted(merged.items())
    if len(points) < 2:
        final = max(0, speeds[-1]) if speeds else 5600
        points = [(0, 0), (max(1, temperature_max), final)]
    return points


def create_default_profile(silent: bool, max_rpm=None, max_cpu_temp=None, max_gpu_temp=None) -> FanCurveProfile:
    """CreateDefaultFanCurveProfile：根据平台最高转速与温度墙生成安静/降温曲线。"""
    max_gpu = max_gpu_temp if max_gpu_temp else 87
    if max_rpm and max_cpu_temp:
        delta = max_cpu_temp - max_gpu
        if silent:
            cpu_t = [30, 60, 87, max_cpu_temp]
            gpu_t = [30 - delta, 60 - delta, 87 - delta, max_gpu]
            speeds = [0, max_rpm // 3, max_rpm * 2 // 3, max_rpm - max_rpm // 10]
        else:
            cpu_t = [45, max_cpu_temp - 5, max_cpu_temp]
            gpu_t = [45 - delta, max_gpu - 5, max_gpu]
            speeds = [max_rpm // 4, max_rpm, max_rpm + max_rpm // 10]
        return FanCurveProfile(_normalize(cpu_t, speeds, max_cpu_temp),
                               _normalize(gpu_t, speeds, max_gpu))
    return FanCurveProfile(
        _normalize([50, 60, 85, 100], [1600, 2000, 4000, 5600], max_cpu_temp or 100),
        _normalize([40, 50, 75, 90], [1600, 2000, 4000, 5600], max_gpu_temp or 90))
