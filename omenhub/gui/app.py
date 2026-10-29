"""托盘程序入口（Program.Main 的界面部分）。"""

import argparse
import logging
import os
import shlex
import shutil
import subprocess
import sys

from .. import __version__, paths
from ..display import monitor_lines, preset_name, tray_text
from ..hardware import GFX_MODE_NAMES, GFX_UMA
from ..i18n import current_language, detect_system_language, set_language, tr
from . import dialogs
from .client import DaemonClient
from .icons import app_icon, custom_icon, temperature_icon
from .overlay import OverlayWindow, ordered_screens
from .qt import QAction, QActionGroup, QtCore, QtGui, QtNetwork, QtWidgets, Signal
from .settings import GuiSettings, autostart_enabled, set_autostart
from .tray import TrayMenu

log = logging.getLogger(__name__)

ERROR_TEXT = {"denied": "daemon_denied", "auto_fan_needs_monitor": "monitor_auto_fan_warning",
              "offline": "daemon_offline"}


class Controller(QtCore.QObject):
    """托盘菜单与控制面板共用的操作入口。"""

    lightingChanged = Signal()
    guiSettingsChanged = Signal()

    def __init__(self, app, client: DaemonClient, settings: GuiSettings):
        super().__init__()
        self.app = app
        self.client = client
        self.settings = settings
        self.lighting_state = {}
        self.panel = None
        self.editor = None
        self.help = None
        self.overlay = None
        self._first_info = True
        self.tray = QtWidgets.QSystemTrayIcon(app_icon())
        self.tray.setToolTip("OmenSuperHub")
        self.menu = TrayMenu(self)
        self.tray.setContextMenu(self.menu.menu)
        self.tray.activated.connect(self._on_activated)
        client.connectionChanged.connect(self._on_connection)
        client.infoChanged.connect(self._on_info)
        client.stateChanged.connect(self._on_state)
        client.statusChanged.connect(self._on_status)
        client.eventReceived.connect(self._on_event)
        client.requestFailed.connect(self.show_error)
        self.tray.show()
        if settings["overlay"]:
            self.set_overlay(True, save=False)

    # ---------------------------------------------------------------- 数据
    @property
    def info(self) -> dict:
        return self.client.info or {}

    @property
    def caps(self) -> dict:
        return self.info.get("caps", {})

    @property
    def state(self) -> dict:
        return self.client.state or {}

    @property
    def fields(self) -> dict:
        return self.state.get("fields", {})

    @property
    def dsettings(self) -> dict:
        return self.state.get("settings", {})

    @property
    def language(self) -> str:
        return current_language()

    # ---------------------------------------------------------------- 守护进程事件
    def _on_connection(self, connected: bool) -> None:
        if self.panel:
            self.panel.sync()
        if not connected:
            # 连接成功时随后会收到 infoChanged 并重建菜单
            self.menu.rebuild()
            self.tray.setToolTip(f"OmenSuperHub\n{tr('daemon_offline')}")

    def _on_info(self, info: dict) -> None:
        self.menu.rebuild()
        if self.panel is not None:
            visible = self.panel.isVisible()
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
            if visible:
                self.show_panel()
        if self._first_info:
            self._first_info = False
            QtCore.QTimer.singleShot(300, self._first_run_checks)

    def _first_run_checks(self) -> None:
        """每个版本首次运行时提示机型支持情况并显示帮助（OSH 的 alreadyRead）。"""
        if self.settings["already_read"] == __version__:
            return
        validation = self.info.get("validation")
        text = {"unsupported": "product_unsupported", "unsupported_hp": "product_unsupported_hp",
                "old_omen": "product_old_omen"}.get(validation)
        if text:
            answer = QtWidgets.QMessageBox.warning(
                None, tr("warning"), tr(text),
                QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Cancel)
            if answer != QtWidgets.QMessageBox.StandardButton.Ok:
                self.quit()
                return
        self.settings["already_read"] = __version__
        self.show_help()

    def _on_state(self, _state: dict) -> None:
        self.menu.refresh()
        self._update_presentation()

    def _on_status(self, _status: dict) -> None:
        self._update_presentation()

    def _update_presentation(self) -> None:
        if not self.client.connected or not self.state:
            return
        status = self.client.status or {}
        self.tray.setToolTip(tray_text(status, self.state, self.caps))
        if self.overlay is not None and self.overlay.isVisible():
            text = "\n".join(monitor_lines(status, self.fields, self.caps))
            self.overlay.set_content(text, self.settings["overlay_size"], self.settings["overlay_location"],
                                     self.settings["overlay_screen"])
        self._update_icon(status)

    def _update_icon(self, status=None) -> None:
        mode = self.settings["icon"]
        if mode == "custom":
            icon = custom_icon(self.settings.custom_icon_path())
            self.tray.setIcon(icon or app_icon())
        elif mode == "dynamic" and status:
            cpu, gpu = status.get("cpu", {}), status.get("gpu", {})
            if self.fields.get("monitor_cpu") and cpu.get("temp") is not None:
                self.tray.setIcon(temperature_icon(cpu["temp"]))
            elif self.fields.get("monitor_gpu") and gpu.get("temp") is not None:
                self.tray.setIcon(temperature_icon(gpu["temp"]))
            else:
                self.tray.setIcon(app_icon())
        else:
            self.tray.setIcon(app_icon())

    def _on_event(self, event: str, data) -> None:
        data = data or {}
        if event == "omen_key":
            if data.get("action") == "overlay":
                self.toggle_overlay()
            elif data.get("action") == "app":
                self.launch_app(self.settings["omen_key_app"])
        elif event == "notify":
            kind = data.get("kind")
            if kind == "high_temp":
                self.notify(tr("high_temp_title"), tr("high_temp_text", limit=data.get("limit"), temp=data.get("temp")),
                            warning=True)
            elif kind == "preset_switched":
                name = preset_name(data.get("preset"), self.state.get("custom_names"))
                self.notify(tr("omen_key_preset_balloon_title"), tr("omen_key_preset_balloon_text", name=name))
            elif kind == "shortcut_not_set":
                self.notify(tr("hint"), tr("omen_key_shortcut_not_set"), warning=True)
            elif kind == "shortcut_failed":
                self.notify(tr("error"), tr("operation_failed", msg=data.get("error", "")), warning=True)

    def _on_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_overlay()
        elif reason == QtWidgets.QSystemTrayIcon.ActivationReason.MiddleClick:
            self.show_panel()

    # ---------------------------------------------------------------- 通用
    def notify(self, title: str, text: str, warning: bool = False) -> None:
        icon = QtWidgets.QSystemTrayIcon.MessageIcon.Warning if warning else \
            QtWidgets.QSystemTrayIcon.MessageIcon.Information
        self.tray.showMessage(title, text, icon, 4000)

    def show_error(self, code: str, message: str) -> None:
        self.menu.refresh()
        if self.panel:
            self.panel.sync()
        key = ERROR_TEXT.get(code)
        dialogs.message(self.panel if self.panel and self.panel.isVisible() else None, "warning",
                        tr(key) if key else tr("operation_failed", msg=message))

    def set_field(self, key: str, value) -> None:
        self.client.set(key, value)

    def set_setting(self, key: str, value) -> None:
        self.client.set(key, value)

    def ask_field(self, key: str, title: str, minimum: int, maximum: int, step: int, default: int) -> None:
        current = self.fields.get(key)
        value = dialogs.ask_int(None, title, minimum, maximum, current if isinstance(current, int) and current
                                else default, step)
        if value is None:
            self.menu.refresh()
        else:
            self.set_field(key, value)

    # ---------------------------------------------------------------- 预设
    def apply_preset(self, preset: str) -> None:
        self.client.call("apply_preset", {"preset": preset})

    def rename_preset(self, preset: str) -> None:
        if not preset or not preset.startswith("custom"):
            return
        current = preset_name(preset, self.state.get("custom_names"))
        text, ok = QtWidgets.QInputDialog.getText(None, tr("rename_preset_title"), tr("rename_preset_prompt"),
                                                  QtWidgets.QLineEdit.EchoMode.Normal, current)
        if ok:
            self.client.call("rename_preset", {"preset": preset, "name": text.strip()},
                             on_error=lambda code, msg: dialogs.message(None, "error", tr("rename_preset_error")))

    # ---------------------------------------------------------------- 风扇
    def edit_curve(self, table: str) -> None:
        from .curve_editor import FanCurveEditor

        if self.editor is not None and self.editor.isVisible():
            if self.editor.table == table:
                self.editor.raise_()
                self.editor.activateWindow()
                return
            self.editor.close()

        def opened(data):
            self.editor = FanCurveEditor(self.client, table, data)
            self.editor.show()
            self.editor.raise_()
            self.editor.activateWindow()
        self.client.call("fan_curve_get", {"table": table}, opened,
                         lambda code, msg: dialogs.message(None, "error", f"{tr('fan_curve_load_failed')}\n{msg}"))

    def start_clean(self) -> None:
        answer = QtWidgets.QMessageBox.information(
            None, tr("clean_creek_title"), tr("clean_creek_confirm"),
            QtWidgets.QMessageBox.StandardButton.Ok | QtWidgets.QMessageBox.StandardButton.Cancel)
        if answer != QtWidgets.QMessageBox.StandardButton.Ok:
            return

        def started(result):
            dialog = dialogs.CleanCreekDialog(self.client, result.get("seconds", 0))
            dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
            dialog.show()
        self.client.call("fan_clean", {"action": "start"}, started)

    # ---------------------------------------------------------------- 性能
    def set_gfx_mode(self, mode: int) -> None:
        if mode == self.state.get("gfx_mode"):
            return
        if mode == GFX_UMA and not dialogs.confirm(None, tr("gfx_uma_title"), tr("gfx_uma_confirm")):
            self.menu.refresh()
            if self.panel:
                self.panel.sync()
            return
        self.client.call("gfx_mode_set", {"mode": mode},
                         lambda r: dialogs.message(None, "info", tr("gfx_switched_to", mode=GFX_MODE_NAMES[mode])),
                         lambda code, msg: (self.menu.refresh(), dialogs.message(None, "info", tr("gfx_unsupported"))))

    def update_gpu_caps(self, result: dict) -> None:
        if result.get("gpu") and self.caps.get("gpu") is not None:
            self.caps["gpu"].update(result["gpu"])
        self.menu.refresh()

    def fill_gpu_processes(self, menu) -> None:
        def fill(processes):
            menu.clear()
            if not processes:
                action = menu.addAction(tr("gpu_apps_none"))
                action.setEnabled(False)
            for proc in processes:
                action = menu.addAction(f"{proc['name']} (PID: {proc['pid']})")
                action.triggered.connect(lambda _c=False, p=proc: self.kill_process(p["pid"], p["name"]))
        self.client.call("gpu_processes", on_done=fill, on_error=lambda *_: None)

    def kill_process(self, pid: int, name: str, on_done=None) -> None:
        if not dialogs.confirm(None, tr("gpu_close_title"), tr("gpu_close_confirm", name=name)):
            return
        self.client.call("kill_process", {"pid": pid}, lambda _r: on_done() if on_done else None,
                         lambda code, msg: dialogs.message(None, "error", tr("gpu_close_error", msg=msg)))

    def set_powerd(self, enable: bool) -> None:
        self.client.call("nvidia_powerd", {"enable": bool(enable)})

    # ---------------------------------------------------------------- 灯光
    def lighting(self, cmd: str, **args) -> None:
        self.client.call(cmd, args, lambda _r: self.refresh_lighting())

    def refresh_lighting(self) -> None:
        def done(result):
            self.lighting_state = result or {}
            self.menu.refresh()
            self.lightingChanged.emit()
        self.client.call("lighting_get", on_done=done, on_error=lambda *_: None)

    def pick_color(self, device: str, zone: int) -> None:
        colors = self.lighting_state.get("colors") or [[255, 255, 255]] * 4
        initial = colors[max(0, zone)]
        color = QtWidgets.QColorDialog.getColor(QtGui.QColor(*initial), None, tr("lighting_custom"))
        if color.isValid():
            self.lighting("lighting_color", device=device, zone=zone, color=[color.red(), color.green(), color.blue()])

    def set_animation(self, device: str, **params) -> None:
        self.client.call("lighting_animation", {"device": device, **params}, lambda _r: self.refresh_lighting(),
                         lambda code, msg: (self.menu.refresh(), self.show_error(code, msg)))

    # ---------------------------------------------------------------- 浮窗
    def set_overlay(self, on: bool, save: bool = True) -> None:
        if save:
            self.settings["overlay"] = bool(on)
        if on:
            if self.overlay is None:
                self.overlay = OverlayWindow()
            self.overlay.show()
            self._update_presentation()
            if not self.client.connected:
                self.overlay.set_content(tr("daemon_offline"), self.settings["overlay_size"],
                                         self.settings["overlay_location"], self.settings["overlay_screen"])
        elif self.overlay is not None:
            self.overlay.hide()
        self.menu.refresh()
        self.guiSettingsChanged.emit()

    def toggle_overlay(self) -> None:
        self.set_overlay(not self.settings["overlay"])

    def set_gui(self, key: str, value) -> None:
        self.settings[key] = value
        self._update_presentation()
        self.menu.refresh()
        self.guiSettingsChanged.emit()

    def fill_screens(self, menu) -> None:
        menu.clear()
        group = QActionGroup(menu)
        current = self.settings["overlay_screen"] or QtGui.QGuiApplication.primaryScreen().name()
        primary = QtGui.QGuiApplication.primaryScreen()
        for index, screen in enumerate(ordered_screens()):
            label = tr("floating_screen_item", index + 1)
            if screen is primary:
                label += f"  ({tr('floating_screen_primary')})"
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(screen.name() == current)
            action.triggered.connect(lambda _c=False, n=screen.name(): self.set_gui("overlay_screen", n))
            group.addAction(action)
            menu.addAction(action)

    # ---------------------------------------------------------------- Omen 键
    def set_omen_key(self, action: str) -> None:
        if action == "app" and not self.settings["omen_key_app"]:
            self.select_app()
            return
        if action == "shortcut" and not self.dsettings.get("omen_key_shortcut"):
            self.select_shortcut()
            return
        self.set_setting("omen_key", action)

    def toggle_candidate(self, preset: str) -> None:
        candidates = list(self.state.get("omen_key_candidates", []))
        if preset in candidates:
            if len(candidates) <= 1:
                self.menu.refresh()
                return
            candidates.remove(preset)
        else:
            candidates.append(preset)
        self.set_setting("omen_key_presets", candidates)

    def omen_key_app_name(self) -> str:
        return dialogs.app_display_name(self.settings["omen_key_app"], self.settings["omen_key_app_name"])

    def select_app(self) -> None:
        dialog = dialogs.AppDialog(self.settings["omen_key_app"])
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self.menu.refresh()
            return
        command, name = dialog.result_command()
        if not command:
            return
        self.settings["omen_key_app"] = command
        self.settings["omen_key_app_name"] = name
        self.set_setting("omen_key", "app")
        self.guiSettingsChanged.emit()

    def clear_app(self) -> None:
        self.settings["omen_key_app"] = ""
        self.settings["omen_key_app_name"] = ""
        if self.dsettings.get("omen_key") == "app":
            self.set_setting("omen_key", "default")
        self.menu.refresh()
        self.guiSettingsChanged.emit()

    def select_shortcut(self) -> None:
        dialog = dialogs.ShortcutDialog(self.dsettings.get("omen_key_shortcut", ""))
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted or not dialog.shortcut:
            self.menu.refresh()
            return
        self.set_setting("omen_key_shortcut", dialog.shortcut)
        self.set_setting("omen_key", "shortcut")

    def clear_shortcut(self) -> None:
        self.set_setting("omen_key_shortcut", "")
        if self.dsettings.get("omen_key") == "shortcut":
            self.set_setting("omen_key", "default")

    def launch_app(self, command: str) -> None:
        """LaunchOmenKeyApp：以当前用户身份启动命令或 .desktop 应用。"""
        if not command:
            self.notify(tr("hint"), tr("omen_key_app_not_found"), warning=True)
            return
        try:
            if command.endswith(".desktop") and os.path.isfile(command):
                if shutil.which("gio"):
                    args = ["gio", "launch", command]
                else:
                    args = self._desktop_exec(command)
                subprocess.Popen(args, start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["/bin/sh", "-c", command], start_new_session=True, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, ValueError) as exc:
            self.notify(tr("error"), tr("omen_key_app_launch_failed", msg=exc), warning=True)

    @staticmethod
    def _desktop_exec(path: str):
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Exec="):
                    args = [a for a in shlex.split(line[5:].strip()) if not (len(a) == 2 and a.startswith("%"))]
                    if args:
                        return args
        raise ValueError(f"no Exec line in {path}")

    # ---------------------------------------------------------------- 其他设置
    def set_language(self, lang: str) -> None:
        if lang == current_language():
            return
        self.settings["language"] = lang
        set_language(lang)
        visible = self.panel is not None and self.panel.isVisible()
        if self.panel is not None:
            self.panel.close()
            self.panel.deleteLater()
            self.panel = None
        self.menu.rebuild()
        self._update_presentation()
        if visible:
            self.show_panel()

    def set_icon_mode(self, mode: str) -> None:
        if mode == "custom" and not custom_icon(self.settings.custom_icon_path()):
            dialogs.message(None, "warning", tr("no_custom_icon", path=self.settings.custom_icon_path()))
            self.menu.refresh()
            self.guiSettingsChanged.emit()
            return
        self.settings["icon"] = mode
        self._update_icon(self.client.status)
        self.menu.refresh()
        self.guiSettingsChanged.emit()

    @staticmethod
    def autostart_enabled() -> bool:
        return autostart_enabled()

    def set_autostart(self, enabled: bool) -> None:
        try:
            set_autostart(bool(enabled))
        except OSError as exc:
            dialogs.message(None, "error", tr("operation_failed", msg=exc))
        self.menu.refresh()
        self.guiSettingsChanged.emit()

    def show_panel(self) -> None:
        from .panel import ControlPanel

        if self.panel is None:
            self.panel = ControlPanel(self)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        if self.caps.get("kb_type", -1) is not None and self.caps.get("kb_type", -1) >= 0:
            self.refresh_lighting()

    def show_help(self) -> None:
        if self.help is None:
            self.help = dialogs.HelpDialog()
        self.help.show()
        self.help.raise_()
        self.help.activateWindow()

    def quit(self) -> None:
        self.client.stop()
        self.tray.hide()
        self.app.quit()


def install_qt_translator(app, language: str):
    """标准对话框（是/否、颜色、文件选择）使用 Qt 自带的翻译。"""
    locale = {"zh-CN": "zh_CN", "zh-TW": "zh_TW"}.get(language)
    if not locale:
        return None
    location = getattr(QtCore.QLibraryInfo, "path", None)
    if location is not None:
        directory = location(QtCore.QLibraryInfo.LibraryPath.TranslationsPath)
    else:
        directory = QtCore.QLibraryInfo.location(QtCore.QLibraryInfo.TranslationsPath)
    translator = QtCore.QTranslator(app)
    if translator.load(f"qtbase_{locale}", directory):
        app.installTranslator(translator)
        return translator
    return None


def _single_instance(name: str):
    """单实例：已有实例时通知它打开控制面板并返回 None。"""
    socket = QtNetwork.QLocalSocket()
    socket.connectToServer(name)
    if socket.waitForConnected(300):
        socket.write(b"show\n")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return None
    QtNetwork.QLocalServer.removeServer(name)
    server = QtNetwork.QLocalServer()
    server.listen(name)
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="omenhub-gui", description="OmenSuperHub for Linux tray")
    parser.add_argument("--socket", default=paths.SOCKET_PATH)
    parser.add_argument("--panel", action="store_true", help="open the control panel on start")
    parser.add_argument("--autostart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-v", "--verbose", action="store_true")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    # Wayland 不允许程序自行定位窗口与置顶，浮窗需要通过 XWayland 显示
    if (os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ
            and os.environ.get("OMENHUB_NATIVE_WAYLAND") != "1"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    app = QtWidgets.QApplication([sys.argv[0]] + qt_args)
    app.setApplicationName("OmenSuperHub")
    app.setApplicationDisplayName("OmenSuperHub")
    app.setDesktopFileName("omenhub-gui")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())
    settings = GuiSettings().load()
    set_language(settings["language"] or detect_system_language())
    install_qt_translator(app, current_language())
    server = _single_instance(f"omenhub-gui-{os.getuid()}")
    if server is None:
        return 0
    client = DaemonClient(args.socket)
    controller = Controller(app, client, settings)

    def on_new_connection():
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.disconnected.connect(conn.deleteLater)
            controller.show_panel()
    server.newConnection.connect(on_new_connection)
    if args.panel or not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
        QtCore.QTimer.singleShot(0, controller.show_panel)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
