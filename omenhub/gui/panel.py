"""控制面板：托盘菜单的完整图形版本（含滑块与实时状态）。"""

from contextlib import contextmanager

from ..display import kb_type_name, preset_name, sensor_temperature, thermal_mode_name, validation_name
from ..hardware import GFX_DISCRETE, GFX_HYBRID, GFX_MODE_NAMES, GFX_OPTIMUS, GFX_SUPPORT_BITS, GFX_UMA
from ..i18n import LANGUAGES, tr
from .icons import app_icon
from .overlay import ordered_screens
from .qt import Qt, QtCore, QtGui, QtWidgets, Signal
from .tray import DOJO_EFFECTS


@contextmanager
def blocked(*widgets):
    states = [w.blockSignals(True) for w in widgets]
    try:
        yield
    finally:
        for widget, state in zip(widgets, states):
            widget.blockSignals(state)


class SliderRow(QtWidgets.QWidget):
    """滑块 + 数值 + 可选的“不设置”勾选框；松开或滚轮停止 0.4 秒后提交。"""

    committed = Signal(object)

    def __init__(self, minimum, maximum, step, unit, unset_label=None, parent=None):
        super().__init__(parent)
        self.step = step
        self.unit = unit
        self.unset = QtWidgets.QCheckBox(unset_label) if unset_label else None
        self.slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum // step, maximum // step)
        self.slider.setPageStep(max(1, 10 // max(1, step)))
        self.label = QtWidgets.QLabel()
        self.label.setMinimumWidth(90)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if self.unset:
            layout.addWidget(self.unset)
            self.unset.toggled.connect(self._unset_toggled)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._commit)
        self.slider.valueChanged.connect(self._changed)
        self.slider.sliderReleased.connect(self._commit)
        self._update_label()

    def value(self):
        if self.unset and self.unset.isChecked():
            return None
        return self.slider.value() * self.step

    def set_value(self, value) -> None:
        with blocked(self.slider, *( [self.unset] if self.unset else [])):
            if self.unset:
                self.unset.setChecked(value is None)
            if value is not None:
                self.slider.setValue(int(value) // self.step)
            self.slider.setEnabled(not (self.unset and self.unset.isChecked()))
        self._update_label()

    def _update_label(self) -> None:
        value = self.value()
        self.label.setText("—" if value is None else f"{value} {self.unit}")

    def _changed(self) -> None:
        self._update_label()
        if not self.slider.isSliderDown():
            self._timer.start()

    def _unset_toggled(self, checked: bool) -> None:
        self.slider.setEnabled(not checked)
        self._update_label()
        self._commit()

    def _commit(self) -> None:
        self._timer.stop()
        self.committed.emit(self.value())


def _combo(items, slot):
    combo = QtWidgets.QComboBox()
    for value, label in items:
        combo.addItem(label, value)
    combo.activated.connect(lambda index, c=combo: slot(c.itemData(index)))
    return combo


def _select(combo, value) -> None:
    with blocked(combo):
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)


def _form(parent_layout, title):
    box = QtWidgets.QGroupBox(title)
    form = QtWidgets.QFormLayout(box)
    form.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
    parent_layout.addWidget(box)
    return form


def _scroll(widget):
    area = QtWidgets.QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    area.setWidget(widget)
    return area


class ControlPanel(QtWidgets.QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        self.setWindowTitle(tr("panel_title"))
        self.setWindowIcon(app_icon())
        self._syncers = []
        tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(tabs)
        self.offline = QtWidgets.QLabel(f"⚠ {tr('daemon_offline')} — {tr('daemon_offline_tip')}")
        self.offline.setStyleSheet("padding: 6px; background: #fdecea; color: #a61b1b;")
        self.statusBar().addPermanentWidget(self.offline, 1)
        builders = [("tab_overview", self._overview), ("tab_fan", self._fans), ("tab_perf", self._performance),
                    ("tab_lighting", self._lighting), ("tab_monitor", self._monitor),
                    ("tab_omen_key", self._omen_key), ("tab_settings", self._settings)]
        for key, builder in builders:
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            builder(layout)
            layout.addStretch(1)
            tabs.addTab(_scroll(page), tr(key))
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(min(900, screen.width() * 0.8)), int(min(760, screen.height() * 0.85)))
        controller.client.statusChanged.connect(self.update_status)
        controller.client.stateChanged.connect(lambda _s: self.sync())
        controller.client.connectionChanged.connect(lambda _c: self.sync())
        self.sync()
        self.update_status(controller.client.status)

    # ---------------------------------------------------------------- 同步
    def _sync(self, func):
        self._syncers.append(func)

    def sync(self) -> None:
        self.offline.setVisible(not self.c.client.connected)
        self.centralWidget().setEnabled(self.c.client.connected)
        if not self.c.client.connected:
            return
        for func in self._syncers:
            try:
                func()
            except (KeyError, TypeError, AttributeError):
                pass

    @property
    def f(self):
        return self.c.fields

    @property
    def s(self):
        return self.c.dsettings

    # ---------------------------------------------------------------- 概览
    def _overview(self, layout) -> None:
        c = self.c
        info, caps = c.info, c.caps
        dev = info.get("device", {})
        form = _form(layout, tr("sys_info"))
        backend = info.get("backend", {})
        rows = [
            (tr("sys_model_name"), dev.get("product_name")),
            (tr("sys_model_validation"), validation_name(info.get("validation"))),
            (tr("sys_board"), dev.get("board")),
            (tr("sys_bios"), dev.get("bios_version")),
            (tr("sys_backend"), backend.get("name", "none") + (f" ({backend['method']})" if backend.get("method")
                                                               else "")),
            (tr("sys_kernel"), dev.get("kernel")),
            (tr("sys_cpu"), dev.get("cpu_model")),
            (tr("sys_cpu_tjmax"), f"{caps['cpu_temp_limit']}°C" if caps.get("cpu_temp_limit") else None),
            (tr("sys_gpu"), (caps.get("gpu") or {}).get("name")),
            (tr("sys_nvidia_tjmax"), f"{caps['gpu_temp_limit']}°C" if caps.get("gpu_temp_limit") else None),
            (tr("sys_kb_type"), kb_type_name(caps.get("kb_type"))),
            (tr("sys_adapter_power"), f"{caps['adapter_power']}W" if (caps.get("adapter_power") or 0) > 0 else None),
            (tr("sys_thermal_policy"), f"V{caps.get('thermal_policy', 0)}" if caps.get("bios") else None),
            (tr("sys_max_fan"), f"{caps['max_fan_rpm']} RPM" if caps.get("max_fan_rpm") else None),
        ]
        for label, value in rows:
            text = QtWidgets.QLabel(str(value) if value else "--")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label + ":", text)
        if backend.get("error"):
            warn = QtWidgets.QLabel(tr("backend_missing"))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #a61b1b;")
            form.addRow(warn)

        live = _form(layout, tr("live_status"))
        self.live = {}
        for key in ("preset", "cpu", "gpu", "fan"):
            label = QtWidgets.QLabel("--")
            label.setStyleSheet("font-size: 12pt; font-weight: bold;")
            self.live[key] = label
            live.addRow({"preset": tr("presets_menu"), "cpu": "CPU", "gpu": "GPU", "fan": tr("fan_speeds")}[key]
                        + ":", label)

        presets = _form(layout, tr("presets_menu"))
        row = QtWidgets.QHBoxLayout()
        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.activated.connect(lambda i: c.apply_preset(self.preset_combo.itemData(i)))
        rename = QtWidgets.QPushButton(tr("rename_preset"))
        rename.clicked.connect(lambda: c.rename_preset(self.preset_combo.currentData()))
        row.addWidget(self.preset_combo, 1)
        row.addWidget(rename)
        presets.addRow(row)
        note = QtWidgets.QLabel(tr("preset_internal_note"))
        note.setWordWrap(True)
        presets.addRow(note)

        def sync_presets():
            with blocked(self.preset_combo):
                self.preset_combo.clear()
                for preset in c.state.get("presets_available", []):
                    self.preset_combo.addItem(preset_name(preset, c.state.get("custom_names")), preset)
                _select(self.preset_combo, c.state.get("current_preset"))
            rename.setEnabled(c.state.get("current_preset", "").startswith("custom"))
        self._sync(sync_presets)
        self.preset_combo.currentIndexChanged.connect(
            lambda _i: rename.setEnabled(str(self.preset_combo.currentData()).startswith("custom")))

        if caps.get("bios"):
            sensors = _form(layout, f"{tr('sys_ir')} / {tr('sys_ambient')} / {tr('sys_pch')} / {tr('sys_vr')}")
            labels = []
            row = QtWidgets.QHBoxLayout()
            for key in ("sys_ir", "sys_ambient", "sys_pch", "sys_vr"):
                label = QtWidgets.QLabel(f"{tr(key)}: --")
                labels.append((key, label))
                row.addWidget(label)
            refresh = QtWidgets.QPushButton(tr("refresh"))

            def load_sensors():
                c.client.call("bios_sensors", on_done=lambda values: [
                    lb.setText(f"{tr(k)}: {sensor_temperature(v)}") for (k, lb), v in zip(labels, values)],
                    on_error=lambda *_: None)
            refresh.clicked.connect(load_sensors)
            row.addWidget(refresh)
            sensors.addRow(row)
            QtCore.QTimer.singleShot(500, load_sensors)

    def update_status(self, status) -> None:
        if not status or not hasattr(self, "live"):
            return
        c = self.c
        self.live["preset"].setText(preset_name(c.state.get("current_preset"), c.state.get("custom_names")))
        cpu, gpu = status.get("cpu", {}), status.get("gpu", {})
        cpu_parts = []
        if cpu.get("temp") is not None:
            cpu_parts.append(f"{cpu['temp']:.1f}°C")
        if cpu.get("power"):
            cpu_parts.append(f"{cpu['power']:.1f}W")
        if cpu.get("freq"):
            cpu_parts.append(f"{cpu['freq'] / 1000:.2f}GHz")
        self.live["cpu"].setText("  ".join(cpu_parts) or tr("monitor_preparing"))
        if gpu.get("state") == "off":
            gpu_text = tr("gpu_powered_off")
        elif gpu.get("state") in ("none", "disabled"):
            gpu_text = "--"
        elif gpu.get("ready"):
            parts = [f"{gpu['temp']:.1f}°C"] if gpu.get("temp") is not None else []
            parts.append(f"{gpu.get('power') or 0:.1f}W")
            if gpu.get("freq"):
                parts.append(f"{gpu['freq']:.0f}MHz")
            gpu_text = "  ".join(parts)
        else:
            gpu_text = tr("monitor_preparing")
        self.live["gpu"].setText(gpu_text)
        levels = status.get("fan", {}).get("levels", [])
        control = status.get("fan_control")
        mode = {"auto": tr("fan_auto"), "max": tr("fan_max")}.get(control, f"{tr('fan_fixed')} {control} RPM")
        cleaning = f" — {tr('clean_creek')} {status['cleaning']}s" if status.get("cleaning") else ""
        self.live["fan"].setText(", ".join(f"{level * 100} RPM" for level in levels) + f"  ({mode}){cleaning}")

    # ---------------------------------------------------------------- 风扇
    def _fans(self, layout) -> None:
        c, caps = self.c, self.c.caps
        form = _form(layout, tr("fan_control"))
        mode_row = QtWidgets.QHBoxLayout()
        self.fan_mode = QtWidgets.QButtonGroup(self)
        for index, (value, label) in enumerate((("auto", tr("fan_auto")), ("max", tr("fan_max")),
                                                ("fixed", tr("fan_fixed")))):
            button = QtWidgets.QRadioButton(label)
            self.fan_mode.addButton(button, index)
            mode_row.addWidget(button)
        mode_row.addStretch(1)
        form.addRow(mode_row)
        fan_slider = SliderRow(0, caps.get("fan_slider_max", 6400), 100, "RPM")
        fan_slider.set_value(caps.get("fan_slider_max", 6400) // 200 * 100)
        form.addRow(tr("set_fan_speed_slider"), fan_slider)

        def mode_clicked(index):
            if index == 2:
                c.set_field("fan_control", fan_slider.value() or 0)
            else:
                c.set_field("fan_control", ("auto", "max")[index])
        self.fan_mode.idClicked.connect(mode_clicked) if hasattr(self.fan_mode, "idClicked") else \
            self.fan_mode.buttonClicked[int].connect(mode_clicked)
        fan_slider.committed.connect(lambda v: c.set_field("fan_control", v))

        def sync_fan():
            control = self.f.get("fan_control")
            index = {"auto": 0, "max": 1}.get(control, 2)
            with blocked(self.fan_mode):
                self.fan_mode.button(index).setChecked(True)
            if isinstance(control, int):
                fan_slider.set_value(control)
        self._sync(sync_fan)
        if caps.get("clean_creek"):
            clean = QtWidgets.QPushButton(tr("clean_creek") if caps["clean_creek"] == "new"
                                          else tr("clean_creek_legacy"))
            clean.clicked.connect(c.start_clean)
            form.addRow(clean)

        form = _form(layout, tr("fan_config"))
        table_row = QtWidgets.QHBoxLayout()
        self.fan_table = QtWidgets.QButtonGroup(self)
        for index, table in enumerate(("silent", "cool", "custom")):
            button = QtWidgets.QRadioButton(tr(f"fan_{table}"))
            self.fan_table.addButton(button, index)
            edit = QtWidgets.QToolButton()
            edit.setText(tr("edit_fan_curve") + "…")
            edit.clicked.connect(lambda _c=False, t=table: c.edit_curve(t))
            table_row.addWidget(button)
            table_row.addWidget(edit)
            table_row.addSpacing(12)
        table_row.addStretch(1)
        form.addRow(table_row)
        handler = lambda index: c.set_field("fan_table", ("silent", "cool", "custom")[index])  # noqa: E731
        self.fan_table.idClicked.connect(handler) if hasattr(self.fan_table, "idClicked") else \
            self.fan_table.buttonClicked[int].connect(handler)
        response = _combo([(v, tr(f"fan_resp_{v}")) for v in ("realtime", "high", "medium", "low")],
                          lambda v: c.set_field("temp_sensitivity", v))
        form.addRow(tr("fan_response"), response)
        protect = QtWidgets.QCheckBox(tr("fan_auto_protect"))
        protect.setToolTip(tr("fan_auto_protect_note"))
        protect.toggled.connect(lambda on: c.set_setting("auto_fan_protect", on))
        form.addRow(protect)
        note = QtWidgets.QLabel(tr("fan_auto_protect_note"))
        note.setWordWrap(True)
        form.addRow(note)

        def sync_table():
            with blocked(self.fan_table):
                self.fan_table.button(("silent", "cool", "custom").index(self.f.get("fan_table", "cool"))) \
                    .setChecked(True)
            _select(response, self.f.get("temp_sensitivity"))
            with blocked(protect):
                protect.setChecked(bool(self.s.get("auto_fan_protect")))
        self._sync(sync_table)

    # ---------------------------------------------------------------- 性能
    def _performance(self, layout) -> None:
        c, caps = self.c, self.c.caps
        if caps.get("bios"):
            form = _form(layout, tr("thermal_mode_menu"))
            modes = list(caps.get("perf_modes") or [])
            if "unleash" not in modes and "performance" not in modes:
                modes.append("unleash")
            thermal = _combo([(m, thermal_mode_name(m)) for m in modes + ["system"]],
                             lambda v: c.set_setting("thermal_mode", v))
            form.addRow(thermal)
            tip = QtWidgets.QLabel(tr("thermal_mode_tip"))
            tip.setWordWrap(True)
            form.addRow(tip)
            self._sync(lambda: _select(thermal, self.s.get("thermal_mode")))
            supported = caps.get("gfx_supported") or 0
            if supported:
                items = [(m, GFX_MODE_NAMES[m]) for m in (GFX_OPTIMUS, GFX_DISCRETE, GFX_UMA, GFX_HYBRID)
                         if supported & GFX_SUPPORT_BITS[m] or c.state.get("gfx_mode") == m]
                gfx = _combo(items, c.set_gfx_mode)
                form.addRow(tr("graphics_mode"), gfx)
                gfx_tip = QtWidgets.QLabel(tr("gfx_only_internal"))
                gfx_tip.setWordWrap(True)
                form.addRow(gfx_tip)
                self._sync(lambda: _select(gfx, c.state.get("gfx_mode")))

        if caps.get("cpu_power_control") or caps.get("iccmax") or caps.get("loadline_levels"):
            form = _form(layout, tr("cpu_power_menu"))
            if caps.get("cpu_power_control"):
                cpu = SliderRow(10, 254, 1, "W", tr("not_set"))
                cpu.committed.connect(lambda v: c.set_field("cpu_power", v))
                form.addRow(tr("cpu_power_menu"), cpu)
                tip = QtWidgets.QLabel(tr("perf_cpu_power_tip"))
                tip.setWordWrap(True)
                form.addRow(tip)
                self._sync(lambda: cpu.set_value(self.f.get("cpu_power")))
            if caps.get("iccmax"):
                icc = _combo([(None, tr("not_set"))] + [(a, f"{a} A") for a in range(150, 351, 20)],
                             lambda v: c.set_field("iccmax", v))
                form.addRow(tr("iccmax_menu"), icc)
                self._sync(lambda: _select(icc, self.f.get("iccmax")))
            if caps.get("loadline_levels"):
                loadline = _combo([(None, tr("not_set"))] + [(lv, str(180 - 10 * lv))
                                                            for lv in range(1, caps["loadline_levels"] + 1)],
                                  lambda v: c.set_field("ac_loadline", v))
                form.addRow(tr("ac_loadline_menu"), loadline)
                self._sync(lambda: _select(loadline, self.f.get("ac_loadline")))

        if caps.get("bios"):
            form = _form(layout, tr("gpu_power_control_menu"))
            tgp = QtWidgets.QCheckBox("Tgp")
            ppab = QtWidgets.QCheckBox("Ppab")
            tgp.setToolTip(tr("perf_tgp_tip"))
            ppab.setToolTip(tr("perf_ppab_tip"))
            tgp.toggled.connect(lambda on: c.set_field("tgp", on))
            ppab.toggled.connect(lambda on: c.set_field("ppab", on))
            row = QtWidgets.QHBoxLayout()
            row.addWidget(tgp)
            row.addWidget(ppab)
            row.addStretch(1)
            form.addRow(row)
            dstate = _combo([("normal", tr("standard")), ("low", tr("low_power"))], lambda v: c.set_field("dstate", v))
            form.addRow(tr("dstate_menu"), dstate)

            def sync_gpu_power():
                with blocked(tgp, ppab):
                    tgp.setChecked(bool(self.f.get("tgp")))
                    ppab.setChecked(bool(self.f.get("ppab")))
                _select(dstate, self.f.get("dstate"))
            self._sync(sync_gpu_power)
            if caps.get("tpp"):
                tpp = SliderRow(20, 254, 1, "W", tr("not_set"))
                tpp.committed.connect(lambda v: c.set_field("tpp", v))
                form.addRow(tr("tpp_menu"), tpp)
                tip = QtWidgets.QLabel(tr("perf_tpp_tip"))
                tip.setWordWrap(True)
                form.addRow(tip)
                self._sync(lambda: tpp.set_value(self.f.get("tpp")))

        gpu = caps.get("gpu")
        if gpu and gpu.get("kind") == "nvidia":
            form = _form(layout, "NVIDIA")
            core = SliderRow(-300, 300, 15, "MHz", tr("not_set"))
            core.committed.connect(lambda v: c.set_field("gpu_core_oc", v))
            form.addRow(tr("gpu_core_oc"), core)
            mem = SliderRow(-1000, 2000, 50, "MHz", tr("not_set"))
            mem.committed.connect(lambda v: c.set_field("gpu_mem_oc", v))
            form.addRow(tr("gpu_mem_oc"), mem)
            tip = QtWidgets.QLabel(tr("gpu_oc_tip"))
            tip.setWordWrap(True)
            form.addRow(tip)
            boost = gpu.get("max_clock") or 0
            clock = SliderRow(210, max(boost + 300, 2100), 10, "MHz", tr("unlimited"))
            clock.committed.connect(lambda v: c.set_field("gpu_clock", v or 0))
            form.addRow(tr("gpu_clock_menu"), clock)
            if boost:
                form.addRow(QtWidgets.QLabel(tr("graphics_boost_clock_tip", boost)))

            def sync_nvidia():
                core.set_value(self.f.get("gpu_core_oc"))
                mem.set_value(self.f.get("gpu_mem_oc"))
                clock.set_value(self.f.get("gpu_clock") or None)
            self._sync(sync_nvidia)
            if c.state.get("nvidia_powerd") in ("active", "inactive"):
                powerd = QtWidgets.QCheckBox(tr("db_menu"))
                powerd.setToolTip(tr("db_tip"))
                powerd.toggled.connect(c.set_powerd)
                form.addRow(powerd)

                def sync_powerd():
                    with blocked(powerd):
                        powerd.setChecked(c.state.get("nvidia_powerd") == "active")
                self._sync(sync_powerd)

        if gpu:
            form = _form(layout, tr("gpu_apps_menu"))
            processes = QtWidgets.QListWidget()
            processes.setMaximumHeight(130)
            buttons = QtWidgets.QHBoxLayout()
            refresh = QtWidgets.QPushButton(tr("refresh"))
            kill = QtWidgets.QPushButton(tr("gpu_close_title"))

            def fill(items):
                processes.clear()
                for proc in items:
                    item = QtWidgets.QListWidgetItem(f"{proc['name']} (PID: {proc['pid']})  {proc['cmdline']}")
                    item.setData(Qt.ItemDataRole.UserRole, (proc["pid"], proc["name"]))
                    processes.addItem(item)
                if not items:
                    processes.addItem(tr("gpu_apps_none"))

            def load():
                c.client.call("gpu_processes", on_done=fill, on_error=lambda *_: None)

            def kill_selected():
                item = processes.currentItem()
                data = item.data(Qt.ItemDataRole.UserRole) if item else None
                if data:
                    c.kill_process(data[0], data[1], on_done=load)
            refresh.clicked.connect(load)
            kill.clicked.connect(kill_selected)
            buttons.addWidget(refresh)
            buttons.addWidget(kill)
            buttons.addStretch(1)
            form.addRow(processes)
            form.addRow(buttons)
            QtCore.QTimer.singleShot(800, load)

    # ---------------------------------------------------------------- 灯光
    def _lighting(self, layout) -> None:
        c, caps = self.c, self.c.caps
        kb_type = caps.get("kb_type", -1)
        if not caps.get("bios") or kb_type is None or kb_type < 0:
            layout.addWidget(QtWidgets.QLabel(tr("kb_type_unknown")))
            return
        if kb_type == 3:
            layout.addWidget(QtWidgets.QLabel(tr("lighting_per_key_tip")))
            return
        if kb_type == 0:
            form = _form(layout, tr("lighting_control"))
            row = QtWidgets.QHBoxLayout()
            for on, label in ((True, tr("lighting_on")), (False, tr("lighting_off"))):
                button = QtWidgets.QPushButton(label)
                button.clicked.connect(lambda _c=False, v=on: c.lighting("lighting_backlight", on=v))
                row.addWidget(button)
            row.addStretch(1)
            form.addRow(row)
            return
        devices = [("keyboard", tr("lighting_four_zone_kb"))]
        if caps.get("lightbar"):
            devices.append(("lightbar", tr("lighting_lightbar")))
        for device, title in devices:
            self._lighting_device(layout, device, title, kb_type)
        status = _form(layout, tr("live_status"))
        state_label = QtWidgets.QLabel("--")
        state_label.setWordWrap(True)
        refresh = QtWidgets.QPushButton(tr("refresh"))
        status.addRow(state_label)
        status.addRow(refresh)

        def show_state():
            st = c.lighting_state
            text = f"{tr('lighting_brightness_status')}: {st.get('brightness', '--')}   " \
                   f"{tr('lighting_animation_status')}: {st.get('animation', '--')}"
            for i, color in enumerate(st.get("colors") or []):
                text += f"\n{tr('lighting_zone_tag')}{i + 1}: RGB({color[0]},{color[1]},{color[2]})"
            state_label.setText(text)
        c.lightingChanged.connect(show_state)
        refresh.clicked.connect(c.refresh_lighting)

    def _lighting_device(self, layout, device, title, kb_type) -> None:
        c = self.c
        key = "kb_protocol" if device == "keyboard" else "lb_protocol"
        form = _form(layout, title)
        protocol = _combo([("basic", tr("lighting_protocol_basic")), ("dojo", tr("lighting_protocol_dojo"))],
                          lambda v: c.set_setting(key, v))
        protocol.setToolTip(tr("lighting_wmi_tip"))
        form.addRow(tr("lighting_wmi_protocol"), protocol)
        brightness = _combo([(v, f"{v}%") for v in (0, 25, 50, 75, 100, 128, 228)],
                            lambda v: c.lighting("lighting_brightness", device=device, value=v))
        form.addRow(tr("lighting_brightness"), brightness)
        form.addRow(QtWidgets.QLabel(tr("lighting_brightness_range_tip")))
        c.lightingChanged.connect(lambda: _select(brightness, c.lighting_state.get("brightness")))
        row = QtWidgets.QHBoxLayout()
        all_button = QtWidgets.QPushButton(tr("lighting_all_zones"))
        all_button.clicked.connect(lambda: c.pick_color(device, -1))
        row.addWidget(all_button)
        zones = 4 if kb_type in (1, 2) or device == "lightbar" else 1
        for zone in range(zones):
            button = QtWidgets.QPushButton(f"{tr('lighting_zone')} {zone + 1}")
            button.clicked.connect(lambda _c=False, z=zone: c.pick_color(device, z))
            row.addWidget(button)
        row.addStretch(1)
        form.addRow(tr("lighting_static_color"), row)
        effect = _combo([(e, tr(f"anim_{e}")) for e in DOJO_EFFECTS],
                        lambda v: c.set_animation(device, effect=v))
        speed = _combo([(v, tr(f"speed_{v}")) for v in range(3)], lambda v: c.set_animation(device, speed=v))
        direction = _combo([(v, tr(f"dir_{v}")) for v in range(2)], lambda v: c.set_animation(device, direction=v))
        theme = _combo([(v, tr(f"theme_{v}")) for v in range(5)], lambda v: c.set_animation(device, theme=v))
        form.addRow(tr("lighting_effect"), effect)
        form.addRow(tr("lighting_speed"), speed)
        form.addRow(tr("lighting_direction"), direction)
        form.addRow(tr("lighting_theme"), theme)

        def sync_lighting():
            _select(protocol, self.s.get(key))
            anim = self.s.get("light_anim", {})
            for combo, field in ((effect, "effect"), (speed, "speed"), (direction, "direction"), (theme, "theme")):
                _select(combo, anim.get(field))
            dojo = self.s.get(key) == "dojo"
            direction.setEnabled(dojo)
            theme.setEnabled(dojo)
        self._sync(sync_lighting)

    # ---------------------------------------------------------------- 监控与浮窗
    def _monitor(self, layout) -> None:
        c, caps = self.c, self.c.caps
        form = _form(layout, tr("hw_monitor"))
        checks = []
        groups = [("monitor_cpu", tr("monitor_cpu_on"), ("show_cpu_temp", "show_cpu_power", "show_cpu_freq"))]
        if caps.get("gpu"):
            groups.append(("monitor_gpu", tr("monitor_gpu_on"), ("show_gpu_temp", "show_gpu_power", "show_gpu_freq")))
        groups.append(("monitor_fan", tr("monitor_fan_on"), ()))
        for key, label, metrics in groups:
            row = QtWidgets.QHBoxLayout()
            box = QtWidgets.QCheckBox(label)
            box.toggled.connect(lambda on, k=key: c.set_field(k, on))
            checks.append((box, key))
            row.addWidget(box)
            row.addSpacing(16)
            for metric in metrics:
                mbox = QtWidgets.QCheckBox(tr(metric))
                mbox.toggled.connect(lambda on, k=metric: c.set_field(k, on))
                checks.append((mbox, metric))
                row.addWidget(mbox)
            row.addStretch(1)
            form.addRow(row)
        if caps.get("gpu"):
            tip = QtWidgets.QLabel(tr("monitor_gpu_tip"))
            tip.setWordWrap(True)
            form.addRow(tip)
        refresh = _combo([("high", tr("refresh_high")), ("low", tr("refresh_low"))],
                         lambda v: c.set_field("refresh_rate", v))
        temp = _combo([("smoothed", tr("temp_smoothed")), ("raw", tr("temp_raw"))],
                      lambda v: c.set_field("temp_display", v))
        form.addRow(tr("monitor_refresh"), refresh)
        form.addRow(tr("temp_display"), temp)
        data = QtWidgets.QCheckBox(tr("data_localize") + " (/run/omenhub/*.txt)")
        data.toggled.connect(lambda on: c.set_setting("data_localize", on))
        form.addRow(data)

        def sync_monitor():
            for box, key in checks:
                with blocked(box):
                    box.setChecked(bool(self.f.get(key)))
            _select(refresh, self.f.get("refresh_rate"))
            _select(temp, self.f.get("temp_display"))
            with blocked(data):
                data.setChecked(bool(self.s.get("data_localize")))
        self._sync(sync_monitor)

        form = _form(layout, tr("floating_bar"))
        show = QtWidgets.QCheckBox(tr("floating_show"))
        show.toggled.connect(c.set_overlay)
        form.addRow(show)
        size = SliderRow(24, 72, 4, "px")
        size.committed.connect(lambda v: c.set_gui("overlay_size", v))
        form.addRow(tr("floating_size"), size)
        location = _combo([("left", tr("floating_loc_left")), ("right", tr("floating_loc_right"))],
                          lambda v: c.set_gui("overlay_location", v))
        form.addRow(tr("floating_location"), location)
        screens = QtWidgets.QComboBox()
        for index, screen in enumerate(ordered_screens()):
            label = tr("floating_screen_item", index + 1)
            if screen is QtGui.QGuiApplication.primaryScreen():
                label += f"  ({tr('floating_screen_primary')})"
            screens.addItem(f"{label}  {screen.name()}", screen.name())
        screens.activated.connect(lambda i: c.set_gui("overlay_screen", screens.itemData(i)))
        form.addRow(tr("floating_screen"), screens)
        form.addRow(QtWidgets.QLabel(tr("floating_toggle_tip")))

        def sync_overlay():
            with blocked(show):
                show.setChecked(bool(c.settings["overlay"]))
            size.set_value(c.settings["overlay_size"])
            _select(location, c.settings["overlay_location"])
            _select(screens, c.settings["overlay_screen"] or QtGui.QGuiApplication.primaryScreen().name())
        self._sync(sync_overlay)
        c.guiSettingsChanged.connect(sync_overlay)

    # ---------------------------------------------------------------- Omen 键
    def _omen_key(self, layout) -> None:
        c = self.c
        form = _form(layout, tr("omen_key_menu"))
        if not c.state.get("omen_key_device"):
            form.addRow(QtWidgets.QLabel(tr("omen_key_not_detected")))
        tip = QtWidgets.QLabel(tr("omen_key_default_tip"))
        tip.setWordWrap(True)
        form.addRow(tip)
        group = QtWidgets.QButtonGroup(self)
        actions = (("default", "omen_key_default"), ("overlay", "omen_key_toggle"),
                   ("preset", "omen_key_switch_preset"), ("app", "omen_key_launch_app"),
                   ("shortcut", "omen_key_shortcut"))
        buttons = {}
        for index, (action, label) in enumerate(actions):
            button = QtWidgets.QRadioButton(tr(label))
            group.addButton(button, index)
            buttons[action] = button
            form.addRow(button)
            if action == "preset":
                row = QtWidgets.QHBoxLayout()
                row.addSpacing(24)
                self.candidates = {}
                for preset in ("extreme", "gpu", "light", "custom1", "custom2", "custom3"):
                    box = QtWidgets.QCheckBox()
                    box.toggled.connect(lambda _on, p=preset: c.toggle_candidate(p))
                    self.candidates[preset] = box
                    row.addWidget(box)
                row.addStretch(1)
                form.addRow(row)
            elif action == "app":
                row = QtWidgets.QHBoxLayout()
                row.addSpacing(24)
                app_label = QtWidgets.QLabel()
                select = QtWidgets.QPushButton(tr("omen_key_select_app"))
                select.clicked.connect(c.select_app)
                clear = QtWidgets.QPushButton(tr("omen_key_clear_app"))
                clear.clicked.connect(c.clear_app)
                row.addWidget(app_label, 1)
                row.addWidget(select)
                row.addWidget(clear)
                form.addRow(row)
            elif action == "shortcut":
                row = QtWidgets.QHBoxLayout()
                row.addSpacing(24)
                shortcut_label = QtWidgets.QLabel()
                select = QtWidgets.QPushButton(tr("omen_key_set_shortcut"))
                select.clicked.connect(c.select_shortcut)
                clear = QtWidgets.QPushButton(tr("omen_key_clear_shortcut"))
                clear.clicked.connect(c.clear_shortcut)
                row.addWidget(shortcut_label, 1)
                row.addWidget(select)
                row.addWidget(clear)
                form.addRow(row)
        handler = lambda index: c.set_omen_key(actions[index][0])  # noqa: E731
        group.idClicked.connect(handler) if hasattr(group, "idClicked") else group.buttonClicked[int].connect(handler)

        def sync_omen():
            with blocked(group):
                current = buttons.get(self.s.get("omen_key"))
                if current:
                    current.setChecked(True)
            available = c.state.get("presets_available", [])
            for preset, box in self.candidates.items():
                with blocked(box):
                    box.setText(preset_name(preset, c.state.get("custom_names")))
                    box.setVisible(preset in available)
                    box.setChecked(preset in c.state.get("omen_key_candidates", []))
            app_label.setText(f"{tr('omen_key_current_app')}: {c.omen_key_app_name()}")
            shortcut_label.setText(f"{tr('omen_key_current_shortcut')}: "
                                   f"{self.s.get('omen_key_shortcut') or tr('omen_key_no_shortcut')}")
        self._sync(sync_omen)
        c.guiSettingsChanged.connect(sync_omen)

    # ---------------------------------------------------------------- 设置
    def _settings(self, layout) -> None:
        c = self.c
        form = _form(layout, tr("other_settings"))
        language = _combo([(lang, tr(f"lang_{lang}")) for lang in LANGUAGES], c.set_language)
        _select(language, c.language)
        form.addRow(tr("language_menu"), language)
        icon = _combo([("original", tr("icon_original")), ("custom", tr("icon_custom")),
                       ("dynamic", tr("icon_dynamic"))], c.set_icon_mode)
        form.addRow(tr("icon_menu"), icon)
        autostart = QtWidgets.QCheckBox(tr("autostart"))
        autostart.toggled.connect(c.set_autostart)
        form.addRow(autostart)
        tip = QtWidgets.QLabel(tr("autostart_tip"))
        tip.setWordWrap(True)
        form.addRow(tip)
        help_button = QtWidgets.QPushButton(tr("help"))
        help_button.clicked.connect(c.show_help)
        form.addRow(help_button)
        info = c.info
        about = QtWidgets.QLabel(f"OmenSuperHub for Linux {info.get('version', '')} — "
                                 f"OmenSuperHub {info.get('upstream_version', '')} (GPL-3.0)")
        about.setStyleSheet("color: gray;")
        form.addRow(about)

        def sync_settings():
            _select(icon, c.settings["icon"])
            with blocked(autostart):
                autostart.setChecked(c.autostart_enabled())
        self._sync(sync_settings)
        c.guiSettingsChanged.connect(sync_settings)
