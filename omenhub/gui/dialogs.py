# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""对话框：帮助、模拟按键录制、应用选择、反转除尘进度。"""

import os
import shlex

from ..i18n import tr
from .help_text import help_html
from .icons import app_icon
from .qt import Qt, QtGui, QtWidgets


def _ival(value) -> int:
    return value.value if hasattr(value, "value") else int(value)


def _key_names():
    names = {}
    for i in range(26):
        names[_ival(Qt.Key.Key_A) + i] = chr(ord("A") + i)
    for i in range(10):
        names[_ival(Qt.Key.Key_0) + i] = str(i)
    for i in range(24):
        names[_ival(Qt.Key.Key_F1) + i] = f"F{i + 1}"
    extra = {
        "Key_Escape": "Esc", "Key_Return": "Enter", "Key_Enter": "Enter", "Key_Space": "Space", "Key_Tab": "Tab",
        "Key_Backspace": "Backspace", "Key_Delete": "Delete", "Key_Insert": "Insert", "Key_Home": "Home",
        "Key_End": "End", "Key_PageUp": "PageUp", "Key_PageDown": "PageDown", "Key_Up": "Up", "Key_Down": "Down",
        "Key_Left": "Left", "Key_Right": "Right", "Key_Print": "PrintScreen", "Key_Pause": "Pause",
        "Key_CapsLock": "CapsLock", "Key_Minus": "-", "Key_Equal": "=", "Key_BracketLeft": "[",
        "Key_BracketRight": "]", "Key_Semicolon": ";", "Key_Apostrophe": "'", "Key_QuoteLeft": "`",
        "Key_Comma": ",", "Key_Period": ".", "Key_Slash": "/", "Key_Backslash": "\\", "Key_Menu": "Menu",
        "Key_VolumeUp": "VolumeUp", "Key_VolumeDown": "VolumeDown", "Key_VolumeMute": "Mute",
        "Key_MediaPlay": "PlayPause", "Key_MediaTogglePlayPause": "PlayPause", "Key_MediaNext": "Next",
        "Key_MediaPrevious": "Previous", "Key_MediaStop": "Stop", "Key_Calculator": "Calculator",
    }
    for attr, name in extra.items():
        key = getattr(Qt.Key, attr, None)
        if key is not None:
            names[_ival(key)] = name
    return names


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"OmenSuperHub {tr('help_title')}")
        self.setWindowIcon(app_icon())
        browser = QtWidgets.QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(help_html())
        buttons = QtWidgets.QDialogButtonBox()
        buttons.addButton(tr("close"), QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.close)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(browser)
        layout.addWidget(buttons)
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(min(screen.width() * 0.6, 1100)), int(min(screen.height() * 0.7, 900)))


class ShortcutDialog(QtWidgets.QDialog):
    """SelectOmenKeyShortcut：按下要模拟的按键组合。"""

    def __init__(self, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("omen_key_set_shortcut").rstrip("…"))
        self.setWindowIcon(app_icon())
        self.shortcut = current or ""
        self._names = _key_names()
        prompt = QtWidgets.QLabel(tr("omen_key_shortcut_prompt"))
        self.display = QtWidgets.QLineEdit(self.shortcut or tr("omen_key_no_shortcut"))
        self.display.setReadOnly(True)
        self.display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons = QtWidgets.QDialogButtonBox()
        self.ok = self.buttons.addButton(tr("ok"), QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        clear = self.buttons.addButton(tr("clear"), QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        self.buttons.addButton(tr("cancel"), QtWidgets.QDialogButtonBox.ButtonRole.RejectRole)
        self.ok.setEnabled(bool(self.shortcut))
        self.ok.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        clear.clicked.connect(self._clear)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(prompt)
        layout.addWidget(self.display)
        layout.addWidget(self.buttons)
        self.resize(420, 150)

    def _clear(self) -> None:
        self.shortcut = ""
        self.display.setText("")
        self.ok.setEnabled(False)

    def keyPressEvent(self, event) -> None:
        name = self._names.get(int(event.key()))
        if name is None:
            event.accept()
            return
        mods = event.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("Super")
        parts.append(name)
        self.shortcut = "+".join(parts)
        self.display.setText(self.shortcut)
        self.ok.setEnabled(True)
        event.accept()


def desktop_entry_name(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Name="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.path.basename(path)


def app_display_name(command: str, name: str = "") -> str:
    if name:
        return name
    if not command:
        return tr("omen_key_no_app")
    try:
        first = shlex.split(command)[0]
    except (ValueError, IndexError):
        first = command
    return os.path.basename(first)


class AppDialog(QtWidgets.QDialog):
    """SelectOmenKeyApp：输入命令或选择 .desktop 文件。"""

    def __init__(self, command: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("omen_key_select_app").rstrip("…"))
        self.setWindowIcon(app_icon())
        self.edit = QtWidgets.QLineEdit(command)
        browse = QtWidgets.QPushButton(tr("browse"))
        browse.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.edit, 1)
        row.addWidget(browse)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok
                                             | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(tr("omen_key_app_prompt")))
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.resize(640, 140)

    def _browse(self) -> None:
        start = "/usr/share/applications"
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, tr("omen_key_select_app"), start,
                                                        tr("omen_key_app_filter"))
        if path:
            self.edit.setText(path)

    def result_command(self):
        command = self.edit.text().strip()
        name = desktop_entry_name(command) if command.endswith(".desktop") else ""
        return command, name


class CleanCreekDialog(QtWidgets.QDialog):
    """StartCleanCreekWithProgress：显示剩余时间，可提前停止。"""

    def __init__(self, client, seconds: int, parent=None):
        super().__init__(parent)
        self.client = client
        self.setWindowTitle(tr("clean_creek_title"))
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.label = QtWidgets.QLabel(tr("clean_creek_progress", seconds))
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        stop = QtWidgets.QPushButton(tr("clean_creek_stop"))
        stop.clicked.connect(self._stop)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(stop)
        self.resize(320, 130)
        client.statusChanged.connect(self._on_status)

    def _on_status(self, status: dict) -> None:
        remaining = status.get("cleaning", 0)
        if remaining <= 0:
            self.accept()
        else:
            self.label.setText(tr("clean_creek_progress", remaining))

    def _stop(self) -> None:
        self.client.call("fan_clean", {"action": "stop"})
        self.accept()

    def done(self, result) -> None:
        try:
            self.client.statusChanged.disconnect(self._on_status)
        except (TypeError, RuntimeError):
            pass
        super().done(result)


def ask_int(parent, title: str, minimum: int, maximum: int, value: int, step: int = 1):
    value = max(minimum, min(maximum, value))
    result, ok = QtWidgets.QInputDialog.getInt(parent, title, tr("custom_value_prompt", min=minimum, max=maximum),
                                               value, minimum, maximum, step)
    return result if ok else None


def message(parent, kind: str, text: str, title: str = None) -> None:
    box = {"error": QtWidgets.QMessageBox.critical, "warning": QtWidgets.QMessageBox.warning}.get(
        kind, QtWidgets.QMessageBox.information)
    box(parent, title or tr({"error": "error", "warning": "warning"}.get(kind, "hint")), text)


def confirm(parent, title: str, text: str) -> bool:
    answer = QtWidgets.QMessageBox.question(parent, title, text)
    return answer == QtWidgets.QMessageBox.StandardButton.Yes

