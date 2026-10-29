"""命令行与图形界面共用的显示格式（monitorText / BuildTrayMonitorLines 等）。"""

from .config import CUSTOM_PRESETS
from .i18n import tr

PRESET_I18N = {"extreme": "preset_extreme", "gpu": "preset_gpu", "light": "preset_light",
               "custom1": "preset_custom1", "custom2": "preset_custom2", "custom3": "preset_custom3"}


def preset_name(preset: str, custom_names=None) -> str:
    if preset in CUSTOM_PRESETS and custom_names and custom_names.get(preset):
        return custom_names[preset]
    return tr(PRESET_I18N.get(preset, preset))


def sensor_temperature(value) -> str:
    """FormatSensorTemperature：<=0 不支持；1 连接断开；其余为温度。"""
    if value is None or value <= 0:
        return tr("sys_sensor_unsupported")
    if value == 1:
        return tr("sys_sensor_disconnected")
    return f"{value} ℃"


def kb_type_name(kb_type) -> str:
    key = f"kb_type_{kb_type}"
    return tr(key) if kb_type is not None and kb_type >= 0 and key != tr(key) else tr("kb_type_unknown")


def validation_name(validation: str) -> str:
    return tr(f"validation_{validation}") if validation else tr("unknown")


def thermal_mode_name(mode: str) -> str:
    return tr(f"mode_{mode}")


def _gpu_off(gpu: dict) -> bool:
    return gpu.get("state") == "off"


def monitor_lines(status: dict, fields: dict, caps: dict, compact: bool = False):
    """监控文本行。compact=True 为托盘提示格式，否则为浮窗格式。"""
    lines = []
    cpu, gpu, fan = status.get("cpu", {}), status.get("gpu", {}), status.get("fan", {})
    sep, colon = (" ", " ") if compact else (", ", ": ")
    if fields.get("monitor_cpu") and (fields.get("show_cpu_temp") or fields.get("show_cpu_power")
                                      or fields.get("show_cpu_freq")):
        parts = []
        if fields.get("show_cpu_temp") and cpu.get("ready") and cpu.get("temp") is not None:
            parts.append(f"{cpu['temp']:.0f}°C" if compact else f"{cpu['temp']:.1f}°C")
        if fields.get("show_cpu_power") and (cpu.get("power") or 0) > 0:
            parts.append(f"{cpu['power']:.0f}W" if compact else f"{cpu['power']:.1f}W")
        if fields.get("show_cpu_freq") and (cpu.get("freq") or 0) > 0:
            parts.append(f"{cpu['freq'] / 1000:.1f}G" if compact else f"{cpu['freq'] / 1000:.1f}GHz")
        if parts:
            lines.append("CPU" + colon + sep.join(parts))
        elif caps.get("cpu_sensor") is False and not cpu.get("ready"):
            lines.append("CPU" + colon + tr("sys_sensor_unsupported"))
        else:
            lines.append("CPU" + colon + tr("monitor_preparing"))
    if caps.get("gpu") and fields.get("monitor_gpu") and (fields.get("show_gpu_temp") or fields.get("show_gpu_power")
                                                          or fields.get("show_gpu_freq")):
        if _gpu_off(gpu):
            lines.append("GPU" + colon + tr("gpu_powered_off"))
        elif not gpu.get("ready"):
            lines.append("GPU" + colon + tr("monitor_preparing"))
        else:
            parts = []
            if fields.get("show_gpu_temp") and gpu.get("temp") is not None:
                parts.append(f"{gpu['temp']:.0f}°C" if compact else f"{gpu['temp']:.1f}°C")
            if fields.get("show_gpu_power"):
                power = gpu.get("power") or 0
                parts.append(f"{power:.0f}W" if compact else f"{power:.1f}W")
            if fields.get("show_gpu_freq") and (gpu.get("freq") or 0) > 0:
                parts.append(f"{gpu['freq'] / 1000:.1f}G" if compact else f"{gpu['freq']:.0f}MHz")
            lines.append("GPU" + colon + (sep.join(parts) if parts else tr("monitor_preparing")))
    if fields.get("monitor_fan"):
        rpm = [str(level * 100) for level in fan.get("levels", [])]
        lines.append(("Fan " + " ".join(rpm)) if compact else ("Fan:  " + ", ".join(rpm)))
    if not lines:
        lines.append(tr("monitor_closed"))
    return lines


def tray_text(status: dict, state: dict, caps: dict) -> str:
    name = preset_name(state.get("current_preset"), state.get("custom_names"))
    lines = monitor_lines(status, state.get("fields", {}), caps, compact=True)
    return tr("active_preset") + name + "\n" + "\n".join(lines)
