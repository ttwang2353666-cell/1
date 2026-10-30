# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""浮窗（FloatingForm.cs）：置顶、透明、鼠标穿透地在屏幕角落显示监控信息。"""

from .qt import Qt, QtCore, QtGui, QtWidgets

CONTENT_PADDING = 10
SCREEN_MARGIN = 10
TITLE_COLORS = {"CPU": QtGui.QColor(0, 128, 192), "GPU": QtGui.QColor(0, 128, 192), "Fan": QtGui.QColor(0, 128, 64)}
VALUE_COLOR = QtGui.QColor(255, 128, 0)


def find_screen(name: str):
    for screen in QtGui.QGuiApplication.screens():
        if screen.name() == name:
            return screen
    return QtGui.QGuiApplication.primaryScreen()


def ordered_screens():
    """主屏幕在前，其余按横坐标排序（与 OSH 的显示器菜单一致）。"""
    primary = QtGui.QGuiApplication.primaryScreen()
    return sorted(QtGui.QGuiApplication.screens(),
                  key=lambda s: (0 if s is primary else 1, s.geometry().x()))


class OverlayWindow(QtWidgets.QWidget):
    def __init__(self):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowDoesNotAcceptFocus)
        if QtGui.QGuiApplication.platformName() == "xcb":
            flags |= Qt.WindowType.X11BypassWindowManagerHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("OmenSuperHub Overlay")
        self._lines = []
        self._font = QtGui.QFont()
        self._text = ""
        self._size = 40
        self._location = "left"
        self._screen_name = ""

    def set_content(self, text: str, size: int, location: str, screen_name: str) -> None:
        if (text, size, location, screen_name) == (self._text, self._size, self._location, self._screen_name):
            return
        self._text, self._size, self._location, self._screen_name = text, size, location, screen_name
        self._font = QtGui.QFont()
        self._font.setFamilies(["Calibri", "Carlito", "DejaVu Sans", "Noto Sans CJK SC", "sans-serif"])
        self._font.setBold(True)
        self._font.setPixelSize(max(6, size))
        screen = find_screen(screen_name)
        area = screen.availableGeometry()
        metrics = QtGui.QFontMetricsF(self._font)
        max_width = max(1.0, area.width() - SCREEN_MARGIN * 2 - CONTENT_PADDING * 2)
        self._lines = self._build_lines(text, metrics, max_width)
        line_height = metrics.height()
        widest = max([metrics.horizontalAdvance(title) + metrics.horizontalAdvance(value)
                      for title, value in self._lines] + [1.0])
        width = int(min(area.width() - SCREEN_MARGIN * 2, widest + CONTENT_PADDING * 2 + 2))
        height = int(line_height * len(self._lines) + CONTENT_PADDING * 2 + 2)
        self.resize(width, height)
        x = area.left() + SCREEN_MARGIN if location == "left" else area.right() - width - SCREEN_MARGIN + 1
        self.move(max(area.left(), x), area.top() + SCREEN_MARGIN)
        self.update()

    @staticmethod
    def _build_lines(text: str, metrics: QtGui.QFontMetricsF, max_width: float):
        """BuildDisplayLines：标题 + 数值，数值过宽时按逗号换行。"""
        result = []
        for source in [line for line in text.split("\n") if line]:
            if ":" in source:
                title, _, value = source.partition(":")
                title, value, prefix = title.strip() + ":", value.strip(), " "
            else:
                title, value, prefix = "", source.strip(), ""
            title_width = metrics.horizontalAdvance(title)
            segments = value.split(",")
            current, first = "", True
            for index, segment in enumerate(segments):
                segment = segment.strip() + ("," if index < len(segments) - 1 else "")
                candidate = segment if not current else current + " " + segment
                width = (title_width if first else 0) + metrics.horizontalAdvance(prefix + candidate)
                if current and width > max_width:
                    result.append((title if first else "", prefix + current))
                    first, prefix, current = False, "", segment
                else:
                    current = candidate
            result.append((title if first else "", prefix + current))
        return result or [("", " ")]

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
        painter.setFont(self._font)
        metrics = QtGui.QFontMetricsF(self._font)
        y = CONTENT_PADDING + metrics.ascent()
        for title, value in self._lines:
            x = float(CONTENT_PADDING)
            if title:
                painter.setPen(TITLE_COLORS.get(title.rstrip(":").strip(), TITLE_COLORS["CPU"]))
                painter.drawText(QtCore.QPointF(x, y), title)
                x += metrics.horizontalAdvance(title)
            painter.setPen(VALUE_COLOR)
            painter.drawText(QtCore.QPointF(x, y), value)
            y += metrics.height()
        painter.end()
