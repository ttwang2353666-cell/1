"""托盘图标与右键菜单（Program.Menu.cs）。

Linux 托盘菜单多通过 DBus（StatusNotifierItem）导出，不能嵌入滑块控件，
所以滑块改为常用数值 + “自定义…”，完整的滑块放在控制面板中。
"""

import textwrap
import unicodedata

from ..display import kb_type_name, preset_name, sensor_temperature, thermal_mode_name, validation_name
from ..hardware import GFX_DISCRETE, GFX_HYBRID, GFX_MODE_NAMES, GFX_OPTIMUS, GFX_SUPPORT_BITS, GFX_UMA
from ..i18n import LANGUAGES, tr
from .qt import QAction, QActionGroup, QtWidgets

COLORS = (("color_red", (255, 0, 0)), ("color_green", (0, 255, 0)), ("color_blue", (0, 0, 255)),
          ("color_white", (255, 255, 255)), ("color_cyan", (0, 255, 255)), ("color_magenta", (255, 0, 255)),
          ("color_yellow", (255, 255, 0)))
DOJO_EFFECTS = (2, 3, 4, 6, 7, 8, 9, 10, 11)
BASIC_EFFECTS = (2, 4)
CPU_POWER_VALUES = (25, 35, 45, 55, 65, 80, 95, 110, 125, 140, 160, 180, 200, 254)
TPP_VALUES = (40, 60, 80, 100, 120, 140, 160, 180, 200, 254)
CORE_OC_VALUES = (0, 30, 60, 90, 120, 150, 180, 210, 240, 270)
MEM_OC_VALUES = (0, 200, 400, 600, 800, 1000, 1200, 1400, 1600)
NOTE_WIDTH = 56  # 提示文字每行的显示宽度（中文字符计 2）


def wrap_note(text: str, width: int = NOTE_WIDTH):
    """把过长的菜单提示拆成多行，避免菜单被撑得过宽。"""
    if all(ord(ch) < 0x2E80 for ch in text):
        return textwrap.wrap(text, width) or [text]
    lines, current, used = [], "", 0
    for ch in text:
        cost = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + cost > width and current:
            lines.append(current)
            current, used = "", 0
        current += ch
        used += cost
    if current:
        lines.append(current)
    return lines


class TrayMenu:
    """根据守护进程的 info/state 构建菜单；state 变化时只刷新勾选状态。"""

    def __init__(self, controller):
        self.c = controller
        self.menu = QtWidgets.QMenu()
        self._checks = []     # (action, predicate)
        self._texts = []      # (action, text_fn)
        self._visible = []    # (action, predicate)
        self.rebuild()

    # ---------------------------------------------------------------- 工具
    def _action(self, menu, text, slot=None, enabled=True):
        action = QAction(text, menu)
        action.setEnabled(enabled)
        if slot:
            action.triggered.connect(lambda _checked=False, s=slot: s())
        menu.addAction(action)
        return action

    def _note(self, menu, text):
        action = None
        for line in wrap_note(text):
            action = self._action(menu, line, enabled=False)
        return action

    def _check(self, menu, text, slot, predicate, group=None):
        action = self._action(menu, text, slot)
        action.setCheckable(True)
        if group is not None:
            group.addAction(action)
        self._checks.append((action, predicate))
        return action

    def _group(self, menu):
        group = QActionGroup(menu)
        group.setExclusive(True)
        return group

    def _sub(self, menu, text):
        submenu = menu.addMenu(text)
        return submenu

    @property
    def f(self):
        return self.c.fields

    @property
    def s(self):
        return self.c.dsettings

    @property
    def caps(self):
        return self.c.caps

    # ---------------------------------------------------------------- 构建
    def rebuild(self) -> None:
        self.menu.clear()
        self._checks, self._texts, self._visible = [], [], []
        c = self.c
        if not c.client.connected:
            self._note(self.menu, tr("daemon_offline"))
            self._note(self.menu, tr("daemon_offline_tip"))
            self.menu.addSeparator()
            self._action(self.menu, tr("help"), c.show_help)
            self._action(self.menu, tr("exit"), c.quit)
            return
        if not self.caps.get("bios"):
            self._note(self.menu, tr("backend_missing"))
        elif c.info.get("backend", {}).get("name") == "hp-wmi":
            self._note(self.menu, tr("backend_limited"))
        self._build_sysinfo()
        self.menu.addSeparator()
        self._build_presets()
        self.menu.addSeparator()
        self._build_fan_config()
        self._build_fan_control()
        self._build_performance()
        self._build_lighting()
        self.menu.addSeparator()
        self._build_monitor()
        self._build_overlay()
        self._build_omen_key()
        self._build_settings()
        self.menu.addSeparator()
        self._action(self.menu, tr("control_panel"), c.show_panel)
        self._action(self.menu, tr("help"), c.show_help)
        self.menu.addSeparator()
        self._action(self.menu, tr("exit"), c.quit)
        self.refresh()

    def refresh(self) -> None:
        if not self.c.client.connected:
            return
        for action, predicate in self._checks:
            try:
                action.setChecked(bool(predicate()))
            except (KeyError, TypeError):
                action.setChecked(False)
        for action, text_fn in self._texts:
            action.setText(text_fn())
        for action, predicate in self._visible:
            action.setVisible(bool(predicate()))

    # ---------------------------------------------------------------- 本机信息
    def _build_sysinfo(self) -> None:
        info, caps = self.c.info, self.caps
        dev = info.get("device", {})
        menu = self._sub(self.menu, tr("sys_info"))
        backend = info.get("backend", {})
        backend_text = backend.get("name", "none") + (f" ({backend['method']})" if backend.get("method") else "")
        rows = [
            (tr("sys_model_name"), dev.get("product_name")),
            (tr("sys_model_validation"), validation_name(info.get("validation"))),
            (tr("sys_board"), dev.get("board")),
            (tr("sys_bios"), dev.get("bios_version")),
            (tr("sys_backend"), backend_text),
            (tr("sys_cpu"), dev.get("cpu_model")),
        ]
        if caps.get("cpu_temp_limit"):
            rows.append((tr("sys_cpu_tjmax"), f"{caps['cpu_temp_limit']}°C"))
        for label, value in rows:
            self._note(menu, f"{label}: {value or tr('unknown')}")
        gpu = caps.get("gpu")
        if gpu:
            self._note(menu, f"{tr('sys_gpu')}: {gpu.get('name') or tr('unknown')}")
            if caps.get("gpu_temp_limit"):
                self._note(menu, f"{tr('sys_nvidia_tjmax')}: {caps['gpu_temp_limit']}°C")
            if gpu.get("kind") == "nvidia":
                power = self._note(menu, "")
                self._texts.append((power, lambda: f"{tr('sys_nvidia_power')}: " + (
                    " / ".join(f"{v:.0f}W" for v in self.caps['gpu']['power_limits'])
                    if self.caps["gpu"].get("power_limits") else "--W / --W")))
        self._note(menu, f"{tr('sys_kb_type')}: {kb_type_name(caps.get('kb_type'))}")
        sensors = []
        for key in ("sys_ir", "sys_ambient", "sys_pch", "sys_vr"):
            action = self._note(menu, f"{tr(key)}: --°C")
            sensors.append((key, action))
        if (caps.get("adapter_power") or 0) > 0:
            self._note(menu, f"{tr('sys_adapter_power')}: {caps['adapter_power']}W")

        def on_show():
            def update_sensors(values):
                for (key, action), value in zip(sensors, values):
                    action.setText(f"{tr(key)}: {sensor_temperature(value)}")
            if caps.get("bios"):
                self.c.client.call("bios_sensors", on_done=update_sensors, on_error=lambda *_: None)
            if gpu:
                self.c.client.call("gpu_refresh", on_done=lambda r: self.c.update_gpu_caps(r),
                                   on_error=lambda *_: None)
        menu.aboutToShow.connect(on_show)

    # ---------------------------------------------------------------- 预设
    def _build_presets(self) -> None:
        c = self.c
        menu = self._sub(self.menu, tr("presets_menu"))
        self._note(menu, tr("preset_note"))
        group = self._group(menu)
        available = c.state.get("presets_available", [])
        if "extreme" in available:
            self._note(menu, tr("preset_internal_note"))
            for preset, tip in (("extreme", "preset_extreme_tip"), ("gpu", "preset_gpu_tip"),
                                ("light", "preset_light_tip")):
                action = self._check(menu, tr(f"preset_{preset}"), lambda p=preset: c.apply_preset(p),
                                     lambda p=preset: c.state.get("current_preset") == p, group)
                action.setToolTip(tr(tip))
            menu.addSeparator()
        for preset in ("custom1", "custom2", "custom3"):
            action = self._check(menu, "", lambda p=preset: c.apply_preset(p),
                                 lambda p=preset: c.state.get("current_preset") == p, group)
            self._texts.append((action, lambda p=preset: preset_name(p, c.state.get("custom_names"))))
        menu.addSeparator()
        rename = self._sub(menu, tr("rename_preset_menu"))
        for preset in ("custom1", "custom2", "custom3"):
            action = self._action(rename, "", lambda p=preset: c.rename_preset(p))
            self._texts.append((action, lambda p=preset: preset_name(p, c.state.get("custom_names")) + "…"))
        menu.setToolTipsVisible(True)

    # ---------------------------------------------------------------- 风扇
    def _build_fan_config(self) -> None:
        c = self.c
        menu = self._sub(self.menu, tr("fan_config"))
        self._note(menu, tr("fan_curve_note"))
        group = self._group(menu)
        for table in ("silent", "cool", "custom"):
            self._check(menu, tr(f"fan_{table}"), lambda t=table: c.set_field("fan_table", t),
                        lambda t=table: self.f.get("fan_table") == t, group)
        edit = self._sub(menu, tr("edit_fan_curve"))
        for table in ("silent", "cool", "custom"):
            self._action(edit, tr(f"fan_{table}") + "…", lambda t=table: c.edit_curve(t))
        menu.addSeparator()
        response = self._sub(menu, tr("fan_response"))
        group = self._group(response)
        for speed in ("realtime", "high", "medium", "low"):
            self._check(response, tr(f"fan_resp_{speed}"), lambda v=speed: c.set_field("temp_sensitivity", v),
                        lambda v=speed: self.f.get("temp_sensitivity") == v, group)
        protect = self._sub(menu, tr("fan_auto_protect"))
        self._note(protect, tr("fan_auto_protect_note"))
        group = self._group(protect)
        self._check(protect, tr("fan_auto_protect_on"), lambda: c.set_setting("auto_fan_protect", True),
                    lambda: self.s.get("auto_fan_protect"), group)
        self._check(protect, tr("fan_auto_protect_off"), lambda: c.set_setting("auto_fan_protect", False),
                    lambda: not self.s.get("auto_fan_protect"), group)

    def _build_fan_control(self) -> None:
        c, caps = self.c, self.caps
        menu = self._sub(self.menu, tr("fan_control"))
        if caps.get("clean_creek"):
            text = tr("clean_creek") if caps["clean_creek"] == "new" else tr("clean_creek_legacy")
            self._action(menu, text + "…", c.start_clean)
            menu.addSeparator()
        group = self._group(menu)
        self._check(menu, tr("fan_auto"), lambda: c.set_field("fan_control", "auto"),
                    lambda: self.f.get("fan_control") == "auto", group)
        self._check(menu, tr("fan_max"), lambda: c.set_field("fan_control", "max"),
                    lambda: self.f.get("fan_control") == "max", group)
        fixed = self._sub(menu, tr("fan_fixed"))
        self._texts.append((fixed.menuAction(), lambda: tr("fan_fixed") + (
            f" ({self.f['fan_control']} RPM)" if isinstance(self.f.get("fan_control"), int) else "")))
        maximum = caps.get("fan_slider_max", 6400)
        values = list(range(0, maximum + 1, 500))
        for rpm in values:
            self._check(fixed, f"{rpm} RPM", lambda v=rpm: c.set_field("fan_control", v),
                        lambda v=rpm: self.f.get("fan_control") == v, group)
        fixed.addSeparator()
        custom = self._check(fixed, tr("fan_fixed_custom"), lambda: c.ask_field(
            "fan_control", tr("fan_fixed"), 0, maximum, 100, 3000),
            lambda: isinstance(self.f.get("fan_control"), int) and self.f["fan_control"] not in values, group)
        custom.setCheckable(True)

    # ---------------------------------------------------------------- 性能
    def _value_menu(self, parent, title, key, values, unit, minimum, maximum, step, tip=None, not_set=True,
                    max_label=None):
        c = self.c
        menu = self._sub(parent, title)
        if tip:
            self._note(menu, tip)
            menu.addSeparator()
        group = self._group(menu)
        if not_set:
            self._check(menu, tr("not_set"), lambda: c.set_field(key, None), lambda: self.f.get(key) is None, group)
        for value in values:
            label = max_label if (max_label and value == values[-1]) else f"{value} {unit}"
            self._check(menu, label, lambda v=value: c.set_field(key, v), lambda v=value: self.f.get(key) == v, group)
        self._check(menu, tr("custom_value"), lambda: c.ask_field(key, title, minimum, maximum, step,
                                                                  values[len(values) // 2]),
                    lambda: self.f.get(key) is not None and self.f.get(key) not in values, group)
        return menu

    def _build_performance(self) -> None:
        c, caps = self.c, self.caps
        menu = self._sub(self.menu, tr("perf_control"))
        if caps.get("bios"):
            thermal = self._sub(menu, tr("thermal_mode_menu"))
            self._note(thermal, tr("thermal_mode_tip"))
            thermal.addSeparator()
            group = self._group(thermal)
            modes = list(caps.get("perf_modes") or [])
            if "unleash" not in modes and "performance" not in modes:
                modes.append("unleash")
            for mode in modes + ["system"]:
                self._check(thermal, thermal_mode_name(mode), lambda m=mode: c.set_setting("thermal_mode", m),
                            lambda m=mode: self.s.get("thermal_mode") == m, group)
        supported = caps.get("gfx_supported") or 0
        if caps.get("bios") and supported:
            gfx = self._sub(menu, tr("graphics_mode"))
            self._note(gfx, tr("gfx_only_internal"))
            gfx.addSeparator()
            group = self._group(gfx)
            for mode in (GFX_OPTIMUS, GFX_DISCRETE, GFX_UMA, GFX_HYBRID):
                if supported & GFX_SUPPORT_BITS[mode] or c.state.get("gfx_mode") == mode:
                    self._check(gfx, GFX_MODE_NAMES[mode], lambda m=mode: c.set_gfx_mode(m),
                                lambda m=mode: c.state.get("gfx_mode") == m, group)
        gpu = caps.get("gpu")
        if gpu:
            apps = self._sub(menu, tr("gpu_apps_menu"))
            apps.aboutToShow.connect(lambda m=apps: c.fill_gpu_processes(m))
            self._note(apps, tr("gpu_apps_none"))
        menu.addSeparator()
        if caps.get("iccmax"):
            self._value_menu(menu, tr("iccmax_menu"), "iccmax", tuple(range(150, 351, 20)), "A", 150, 350, 10)
        levels = caps.get("loadline_levels") or 0
        if levels:
            loadline = self._sub(menu, tr("ac_loadline_menu"))
            group = self._group(loadline)
            self._check(loadline, tr("not_set"), lambda: c.set_field("ac_loadline", None),
                        lambda: self.f.get("ac_loadline") is None, group)
            for level in range(1, levels + 1):
                self._check(loadline, str(180 - 10 * level), lambda v=level: c.set_field("ac_loadline", v),
                            lambda v=level: self.f.get("ac_loadline") == v, group)
        if caps.get("cpu_power_control"):
            self._value_menu(menu, tr("cpu_power_menu"), "cpu_power", CPU_POWER_VALUES, "W", 10, 254, 1,
                             tr("perf_cpu_power_tip"), max_label=tr("maximum"))
        if caps.get("bios"):
            gpu_power = self._sub(menu, tr("gpu_power_control_menu"))
            for key, title, tip in (("tgp", "Tgp", "perf_tgp_tip"), ("ppab", "Ppab", "perf_ppab_tip")):
                sub = self._sub(gpu_power, title)
                self._note(sub, tr(tip))
                sub.addSeparator()
                group = self._group(sub)
                self._check(sub, tr("enable"), lambda k=key: c.set_field(k, True), lambda k=key: self.f.get(k), group)
                self._check(sub, tr("disable"), lambda k=key: c.set_field(k, False),
                            lambda k=key: not self.f.get(k), group)
            if caps.get("tpp"):
                self._value_menu(gpu_power, tr("tpp_menu"), "tpp", TPP_VALUES, "W", 20, 254, 1, tr("perf_tpp_tip"),
                                 max_label=tr("maximum"))
            dstate = self._sub(gpu_power, tr("dstate_menu"))
            self._note(dstate, tr("perf_dstate_tip"))
            dstate.addSeparator()
            group = self._group(dstate)
            self._check(dstate, tr("standard"), lambda: c.set_field("dstate", "normal"),
                        lambda: self.f.get("dstate") == "normal", group)
            self._check(dstate, tr("low_power"), lambda: c.set_field("dstate", "low"),
                        lambda: self.f.get("dstate") == "low", group)
        if gpu and gpu.get("kind") == "nvidia":
            self._value_menu(menu, tr("gpu_core_oc"), "gpu_core_oc", CORE_OC_VALUES, "MHz", -300, 300, 15,
                             tr("gpu_oc_tip"))
            self._value_menu(menu, tr("gpu_mem_oc"), "gpu_mem_oc", MEM_OC_VALUES, "MHz", -1000, 2000, 50,
                             tr("gpu_oc_tip"))
            boost = gpu.get("max_clock") or 0
            clock = self._sub(menu, tr("gpu_clock_menu"))
            if boost:
                self._note(clock, tr("graphics_boost_clock_tip", boost))
            group = self._group(clock)
            self._check(clock, tr("unlimited"), lambda: c.set_field("gpu_clock", 0),
                        lambda: not self.f.get("gpu_clock"), group)
            top = max(1500, boost)
            values = list(range(600, top + 1, 300))
            for value in values:
                self._check(clock, f"{value} MHz", lambda v=value: c.set_field("gpu_clock", v),
                            lambda v=value: self.f.get("gpu_clock") == v, group)
            self._check(clock, tr("custom_value"), lambda: c.ask_field("gpu_clock", tr("gpu_clock_menu"), 210,
                                                                       max(boost + 300, 2100), 10, 1500),
                        lambda: self.f.get("gpu_clock") and self.f.get("gpu_clock") not in values, group)
            if c.state.get("nvidia_powerd") in ("active", "inactive"):
                db = self._sub(menu, tr("db_menu"))
                self._note(db, tr("db_tip"))
                db.addSeparator()
                group = self._group(db)
                self._check(db, tr("enable"), lambda: c.set_powerd(True),
                            lambda: c.state.get("nvidia_powerd") == "active", group)
                self._check(db, tr("disable"), lambda: c.set_powerd(False),
                            lambda: c.state.get("nvidia_powerd") == "inactive", group)

    # ---------------------------------------------------------------- 灯光
    def _build_lighting(self) -> None:
        c, caps = self.c, self.caps
        kb_type = caps.get("kb_type", -1)
        if not caps.get("bios") or kb_type is None or kb_type < 0:
            return
        menu = self._sub(self.menu, tr("lighting_control"))
        if kb_type == 3:
            self._note(menu, tr("lighting_per_key_tip"))
        elif kb_type > 0:
            kb = self._sub(menu, tr("lighting_four_zone_kb"))
            self._note(kb, tr("lighting_brightness_range_tip"))
            kb.addSeparator()
            self._lighting_ui(kb, "keyboard")
        else:
            group = self._group(menu)
            self._check(menu, tr("lighting_on"), lambda: c.lighting("lighting_backlight", on=True),
                        lambda: c.lighting_state.get("brightness") == 228, group)
            self._check(menu, tr("lighting_off"), lambda: c.lighting("lighting_backlight", on=False),
                        lambda: c.lighting_state.get("brightness") == 100, group)
        if caps.get("lightbar"):
            lb = self._sub(menu, tr("lighting_lightbar"))
            self._note(lb, tr("lighting_brightness_range_tip"))
            lb.addSeparator()
            self._lighting_ui(lb, "lightbar")
        menu.addSeparator()
        brightness = self._note(menu, "")
        self._texts.append((brightness, lambda: f"{tr('lighting_brightness_status')}: "
                                                f"{c.lighting_state.get('brightness', '--')}%"))
        animation = self._note(menu, "")
        self._texts.append((animation, lambda: f"{tr('lighting_animation_status')}: " + (
            f"ID {c.lighting_state['animation']}" if c.lighting_state.get("animation", -1) not in (-1, None)
            else tr("lighting_animation_none"))))
        if kb_type in (1, 2, 4, 5):
            zones = range(4) if kb_type in (1, 2) else range(1)
            for zone in zones:
                action = self._note(menu, "")
                label = f"{tr('lighting_zone_tag')}{zone + 1}" if kb_type in (1, 2) else \
                    tr("lighting_single_zone_color")
                self._texts.append((action, lambda z=zone, lb=label: f"{lb}: " + (
                    "RGB({},{},{})".format(*c.lighting_state["colors"][z]) if c.lighting_state.get("colors")
                    else "--")))
        menu.aboutToShow.connect(c.refresh_lighting)

    def _lighting_ui(self, parent, device: str) -> None:
        c = self.c
        key = "kb_protocol" if device == "keyboard" else "lb_protocol"
        protocol = self._sub(parent, tr("lighting_wmi_protocol"))
        self._note(protocol, tr("lighting_wmi_tip"))
        group = self._group(protocol)
        for value, label in (("basic", "lighting_protocol_basic"), ("dojo", "lighting_protocol_dojo")):
            self._check(protocol, tr(label), lambda v=value: c.set_setting(key, v),
                        lambda v=value: self.s.get(key) == v, group)
        parent.addSeparator()
        brightness = self._sub(parent, tr("lighting_brightness"))
        for value in (0, 25, 50, 75, 100, 128, 228):
            self._action(brightness, f"{value}%", lambda v=value: c.lighting("lighting_brightness", device=device,
                                                                             value=v))
        static = self._sub(parent, tr("lighting_static_color"))
        for zone in [-1, 0, 1, 2, 3]:
            title = tr("lighting_all_zones") if zone < 0 else f"{tr('lighting_zone')} {zone + 1}"
            sub = self._sub(static, title)
            for name, rgb in COLORS:
                self._action(sub, tr(name), lambda z=zone, col=rgb: c.lighting(
                    "lighting_color", device=device, zone=z, color=list(col)))
            sub.addSeparator()
            self._action(sub, tr("lighting_custom"), lambda z=zone: c.pick_color(device, z))
        anim = self._sub(parent, tr("lighting_animation"))
        effect = self._sub(anim, tr("lighting_effect"))
        group = self._group(effect)
        is_dojo = lambda: self.s.get(key) == "dojo"  # noqa: E731
        for value in DOJO_EFFECTS:
            action = self._check(effect, tr(f"anim_{value}"), lambda v=value: c.set_animation(device, effect=v),
                                 lambda v=value: self.s.get("light_anim", {}).get("effect") == v, group)
            if value not in BASIC_EFFECTS:
                self._visible.append((action, is_dojo))
        for title, field, count in (("lighting_speed", "speed", 3), ("lighting_direction", "direction", 2),
                                    ("lighting_theme", "theme", 5)):
            sub = self._sub(anim, tr(title))
            if field != "speed":
                self._visible.append((sub.menuAction(), is_dojo))
            group = self._group(sub)
            prefix = {"speed": "speed_", "direction": "dir_", "theme": "theme_"}[field]
            for value in range(count):
                self._check(sub, tr(f"{prefix}{value}"), lambda v=value, fd=field: c.set_animation(device, **{fd: v}),
                            lambda v=value, fd=field: self.s.get("light_anim", {}).get(fd) == v, group)

    # ---------------------------------------------------------------- 监控
    def _build_monitor(self) -> None:
        c, caps = self.c, self.caps
        menu = self._sub(self.menu, tr("hw_monitor"))
        targets = [("cpu", tr("monitor_cpu_label"), ("show_cpu_temp", "show_cpu_power", "show_cpu_freq"))]
        if caps.get("gpu"):
            targets.append(("gpu", tr("monitor_gpu_label"), ("show_gpu_temp", "show_gpu_power", "show_gpu_freq")))
        for target, title, metrics in targets:
            sub = self._sub(menu, title)
            group = self._group(sub)
            key = f"monitor_{target}"
            self._check(sub, tr(f"monitor_{target}_on"), lambda k=key: c.set_field(k, True),
                        lambda k=key: self.f.get(k), group)
            self._check(sub, tr(f"monitor_{target}_off"), lambda k=key: c.set_field(k, False),
                        lambda k=key: not self.f.get(k), group)
            sub.addSeparator()
            for metric in metrics:
                self._check(sub, tr(metric), lambda m=metric: c.set_field(m, not self.f.get(m)),
                            lambda m=metric: self.f.get(m))
            if target == "gpu":
                sub.addSeparator()
                self._note(sub, tr("monitor_gpu_tip"))
        fan = self._sub(menu, tr("monitor_fan_label"))
        group = self._group(fan)
        self._check(fan, tr("monitor_fan_on"), lambda: c.set_field("monitor_fan", True),
                    lambda: self.f.get("monitor_fan"), group)
        self._check(fan, tr("monitor_fan_off"), lambda: c.set_field("monitor_fan", False),
                    lambda: not self.f.get("monitor_fan"), group)
        refresh = self._sub(menu, tr("monitor_refresh"))
        group = self._group(refresh)
        for value in ("high", "low"):
            self._check(refresh, tr(f"refresh_{value}"), lambda v=value: c.set_field("refresh_rate", v),
                        lambda v=value: self.f.get("refresh_rate") == v, group)
        temp = self._sub(menu, tr("temp_display"))
        group = self._group(temp)
        for value in ("smoothed", "raw"):
            self._check(temp, tr(f"temp_{value}"), lambda v=value: c.set_field("temp_display", v),
                        lambda v=value: self.f.get("temp_display") == v, group)

    def _build_overlay(self) -> None:
        c = self.c
        menu = self._sub(self.menu, tr("floating_bar"))
        self._note(menu, tr("floating_toggle_tip"))
        group = self._group(menu)
        self._check(menu, tr("floating_hide"), lambda: c.set_overlay(False), lambda: not c.settings["overlay"], group)
        self._check(menu, tr("floating_show"), lambda: c.set_overlay(True), lambda: c.settings["overlay"], group)
        menu.addSeparator()
        size = self._sub(menu, tr("floating_size"))
        group = self._group(size)
        for value in range(24, 73, 4):
            self._check(size, str(value), lambda v=value: c.set_gui("overlay_size", v),
                        lambda v=value: c.settings["overlay_size"] == v, group)
        menu.addSeparator()
        group = self._group(menu)
        for value in ("left", "right"):
            self._check(menu, tr(f"floating_loc_{value}"), lambda v=value: c.set_gui("overlay_location", v),
                        lambda v=value: c.settings["overlay_location"] == v, group)
        menu.addSeparator()
        screens = self._sub(menu, tr("floating_screen"))
        screens.aboutToShow.connect(lambda m=screens: c.fill_screens(m))
        self._note(screens, "…")

    def _build_omen_key(self) -> None:
        c = self.c
        menu = self._sub(self.menu, tr("omen_key_menu"))
        if not c.state.get("omen_key_device"):
            self._note(menu, tr("omen_key_not_detected"))
        self._note(menu, tr("omen_key_default_tip"))
        menu.addSeparator()
        group = self._group(menu)
        for action, label in (("default", "omen_key_default"), ("overlay", "omen_key_toggle"),
                              ("preset", "omen_key_switch_preset")):
            self._check(menu, tr(label), lambda a=action: c.set_omen_key(a),
                        lambda a=action: self.s.get("omen_key") == a, group)
        candidates = self._sub(menu, tr("omen_key_candidates"))
        for preset in ("extreme", "gpu", "light", "custom1", "custom2", "custom3"):
            action = self._check(candidates, "", lambda p=preset: c.toggle_candidate(p),
                                 lambda p=preset: p in c.state.get("omen_key_candidates", []))
            self._texts.append((action, lambda p=preset: preset_name(p, c.state.get("custom_names"))))
            self._visible.append((action, lambda p=preset: p in c.state.get("presets_available", [])))
        self._check(menu, tr("omen_key_launch_app"), lambda: c.set_omen_key("app"),
                    lambda: self.s.get("omen_key") == "app", group)
        app = self._sub(menu, tr("omen_key_launch_app"))
        current = self._note(app, "")
        self._texts.append((current, lambda: f"{tr('omen_key_current_app')}: {c.omen_key_app_name()}"))
        self._action(app, tr("omen_key_select_app"), c.select_app)
        self._action(app, tr("omen_key_clear_app"), c.clear_app)
        self._check(menu, tr("omen_key_shortcut"), lambda: c.set_omen_key("shortcut"),
                    lambda: self.s.get("omen_key") == "shortcut", group)
        shortcut = self._sub(menu, tr("omen_key_shortcut"))
        current = self._note(shortcut, "")
        self._texts.append((current, lambda: f"{tr('omen_key_current_shortcut')}: "
                                             f"{self.s.get('omen_key_shortcut') or tr('omen_key_no_shortcut')}"))
        self._action(shortcut, tr("omen_key_set_shortcut"), c.select_shortcut)
        self._action(shortcut, tr("omen_key_clear_shortcut"), c.clear_shortcut)

    def _build_settings(self) -> None:
        c = self.c
        menu = self._sub(self.menu, tr("other_settings"))
        language = self._sub(menu, tr("language_menu"))
        group = self._group(language)
        for lang in LANGUAGES:
            self._check(language, tr(f"lang_{lang}"), lambda v=lang: c.set_language(v),
                        lambda v=lang: c.language == v, group)
        icon = self._sub(menu, tr("icon_menu"))
        group = self._group(icon)
        for value, label in (("original", "icon_original"), ("custom", "icon_custom"), ("dynamic", "icon_dynamic")):
            self._check(icon, tr(label), lambda v=value: c.set_icon_mode(v), lambda v=value: c.settings["icon"] == v,
                        group)
        data = self._sub(menu, tr("data_localize"))
        group = self._group(data)
        self._check(data, tr("enable"), lambda: c.set_setting("data_localize", True),
                    lambda: self.s.get("data_localize"), group)
        self._check(data, tr("disable"), lambda: c.set_setting("data_localize", False),
                    lambda: not self.s.get("data_localize"), group)
        autostart = self._sub(menu, tr("autostart"))
        self._note(autostart, tr("autostart_tip"))
        group = self._group(autostart)
        self._check(autostart, tr("enable"), lambda: c.set_autostart(True), c.autostart_enabled, group)
        self._check(autostart, tr("disable"), lambda: c.set_autostart(False), lambda: not c.autostart_enabled(),
                    group)

