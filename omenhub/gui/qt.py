# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Qt 绑定兼容层：依次尝试 PyQt6、PySide6、PyQt5（代码统一使用完整的枚举名与 exec()）。"""

import os

_preferred = os.environ.get("OMENHUB_QT_API", "").lower()
_order = ["pyqt6", "pyside6", "pyqt5"]
if _preferred in _order:
    _order.remove(_preferred)
    _order.insert(0, _preferred)

API = None
for _name in _order:
    try:
        if _name == "pyqt6":
            from PyQt6 import QtCore, QtGui, QtNetwork, QtWidgets
            from PyQt6.QtCore import pyqtSignal as Signal
        elif _name == "pyside6":
            from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
            from PySide6.QtCore import Signal
        else:
            from PyQt5 import QtCore, QtGui, QtNetwork, QtWidgets
            from PyQt5.QtCore import pyqtSignal as Signal
        API = _name
        break
    except ImportError:
        continue

if API is None:
    raise ImportError("PyQt6, PySide6 or PyQt5 is required for the OmenSuperHub tray (e.g. apt install python3-pyqt6)")

Qt = QtCore.Qt
QAction = getattr(QtGui, "QAction", None) or QtWidgets.QAction
QActionGroup = getattr(QtGui, "QActionGroup", None) or QtWidgets.QActionGroup

__all__ = ["API", "QtCore", "QtGui", "QtNetwork", "QtWidgets", "Signal", "Qt", "QAction", "QActionGroup"]
