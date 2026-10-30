# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""风扇曲线编辑器（FanCurveForm.cs）：左键空白处加点、拖动调整、右键删除，至少保留两个点。"""

from ..fancurve import FanCurveProfile, InvalidFanCurve
from ..i18n import tr
from .icons import app_icon
from .qt import Qt, QtCore, QtGui, QtWidgets, Signal

PAGE_BG = QtGui.QColor(245, 247, 250)
CARD_BG = QtGui.QColor(255, 255, 255)
BORDER = QtGui.QColor(220, 225, 230)
ACCENT = QtGui.QColor(0, 122, 204)
MARKER = QtGui.QColor(220, 53, 69)
GRID = QtGui.QColor(232, 236, 240)
AXIS = QtGui.QColor(200, 205, 210)
HIT_RADIUS = 12


def _fan_interval(maximum: int) -> int:
    if maximum <= 3000:
        return 500
    if maximum <= 7000:
        return 1000
    return 2000


class CurveChart(QtWidgets.QWidget):
    changed = Signal()

    def __init__(self, label: str, temp_max: int, fan_max: int, parent=None):
        super().__init__(parent)
        self.label = label
        self.temp_max = max(1, temp_max)
        self.fan_max = max(100, fan_max)
        self.points = []
        self._drag = None
        self.setMouseTracking(True)
        self.setMinimumSize(360, 300)
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---------------------------------------------------------------- 坐标换算
    def _plot(self) -> QtCore.QRectF:
        return QtCore.QRectF(78, 18, max(10, self.width() - 100), max(10, self.height() - 80))

    def _to_px(self, temp: float, rpm: float) -> QtCore.QPointF:
        r = self._plot()
        return QtCore.QPointF(r.left() + temp / self.temp_max * r.width(),
                              r.bottom() - rpm / self.fan_max * r.height())

    def _to_value(self, pos: QtCore.QPointF):
        r = self._plot()
        if not r.adjusted(-4, -4, 4, 4).contains(pos):
            return None
        temp = (pos.x() - r.left()) / r.width() * self.temp_max
        rpm = (r.bottom() - pos.y()) / r.height() * self.fan_max
        return max(0.0, min(self.temp_max, temp)), max(0.0, min(self.fan_max, rpm))

    def _hit(self, pos: QtCore.QPointF):
        for index, (temp, rpm) in enumerate(self.points):
            p = self._to_px(temp, rpm)
            if (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2 <= HIT_RADIUS ** 2:
                return index
        return None

    def set_points(self, points) -> None:
        self.points = sorted((int(t), int(s)) for t, s in points)
        self.update()

    # ---------------------------------------------------------------- 鼠标
    @staticmethod
    def _pos(event) -> QtCore.QPointF:
        return event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())

    def mousePressEvent(self, event) -> None:
        pos = self._pos(event)
        index = self._hit(pos)
        if event.button() == Qt.MouseButton.RightButton:
            if index is not None and len(self.points) > 2:
                del self.points[index]
                self.update()
                self.changed.emit()
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if index is not None:
            self._drag = index
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        value = self._to_value(pos)
        if value is None:
            return
        temp = int(round(value[0]))
        rpm = int(round(value[1] / 100.0) * 100)
        for i, (t, _) in enumerate(self.points):
            if t == temp:
                self._drag = i
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                return
        self.points.append((temp, rpm))
        self.points.sort()
        self.update()
        self.changed.emit()

    def mouseMoveEvent(self, event) -> None:
        pos = self._pos(event)
        if self._drag is not None:
            value = self._to_value(pos)
            if value is None:
                return
            i = self._drag
            low = self.points[i - 1][0] + 1 if i > 0 else 0
            high = self.points[i + 1][0] - 1 if i < len(self.points) - 1 else self.temp_max
            temp = max(low, min(high, int(round(value[0]))))
            rpm = max(0, min(self.fan_max, int(round(value[1] / 100.0) * 100)))
            self.points[i] = (temp, rpm)
            self.update()
            self.changed.emit()
            QtWidgets.QToolTip.showText(self._global(event), f"{temp}°C, {rpm} RPM", self)
            return
        index = self._hit(pos)
        if index is None:
            self.setCursor(Qt.CursorShape.CrossCursor)
            QtWidgets.QToolTip.hideText()
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            temp, rpm = self.points[index]
            QtWidgets.QToolTip.showText(self._global(event), f"{temp}°C, {rpm} RPM", self)

    @staticmethod
    def _global(event) -> QtCore.QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        self.setCursor(Qt.CursorShape.CrossCursor)

    # ---------------------------------------------------------------- 绘制
    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), PAGE_BG)
        r = self._plot()
        painter.fillRect(r, CARD_BG)
        small = QtGui.QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        painter.setFont(small)
        metrics = QtGui.QFontMetricsF(small)
        # 网格与刻度
        for temp in range(0, self.temp_max + 1, 10):
            x = self._to_px(temp, 0).x()
            painter.setPen(QtGui.QPen(GRID, 1))
            painter.drawLine(QtCore.QPointF(x, r.top()), QtCore.QPointF(x, r.bottom()))
            painter.setPen(QtGui.QColor(90, 90, 90))
            text = str(temp)
            painter.drawText(QtCore.QPointF(x - metrics.horizontalAdvance(text) / 2, r.bottom() + metrics.height()),
                             text)
        step = _fan_interval(self.fan_max)
        for rpm in range(0, self.fan_max + 1, step):
            y = self._to_px(0, rpm).y()
            painter.setPen(QtGui.QPen(GRID, 1))
            painter.drawLine(QtCore.QPointF(r.left(), y), QtCore.QPointF(r.right(), y))
            painter.setPen(QtGui.QColor(90, 90, 90))
            text = str(rpm)
            painter.drawText(QtCore.QPointF(r.left() - metrics.horizontalAdvance(text) - 6, y + metrics.ascent() / 2.5),
                             text)
        painter.setPen(QtGui.QPen(AXIS, 1))
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        painter.drawLine(r.bottomLeft(), r.topLeft())
        # 坐标轴标题
        bold = QtGui.QFont(self.font())
        bold.setBold(True)
        painter.setFont(bold)
        painter.setPen(QtGui.QColor(80, 80, 80))
        x_title = f"{self.label} {tr('fan_curve_temp_axis')}"
        bm = QtGui.QFontMetricsF(bold)
        painter.drawText(QtCore.QPointF(r.center().x() - bm.horizontalAdvance(x_title) / 2,
                                        r.bottom() + metrics.height() + bm.height() + 4), x_title)
        painter.save()
        painter.translate(16, r.center().y() + bm.horizontalAdvance(tr("fan_curve_speed_axis")) / 2)
        painter.rotate(-90)
        painter.drawText(QtCore.QPointF(0, bm.ascent()), tr("fan_curve_speed_axis"))
        painter.restore()
        # 曲线与节点
        if self.points:
            pen = QtGui.QPen(ACCENT, 3)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            polyline = QtGui.QPolygonF([self._to_px(t, s) for t, s in self.points])
            painter.drawPolyline(polyline)
            painter.setPen(QtGui.QPen(CARD_BG, 1.5))
            painter.setBrush(MARKER)
            for t, s in self.points:
                painter.drawEllipse(self._to_px(t, s), 5, 5)
        painter.end()


class FanCurveEditor(QtWidgets.QDialog):
    """saved(table, apply)：保存成功后发出。"""

    saved = Signal(str, bool)

    def __init__(self, client, table: str, data: dict, parent=None):
        super().__init__(parent)
        self.client = client
        self.table = table
        limits = data["limits"]
        mode = {"silent": tr("fan_silent"), "cool": tr("fan_cool")}.get(table, tr("fan_custom"))
        self.setWindowTitle(tr("fan_curve_editor_title", mode=mode))
        self.setWindowIcon(app_icon())
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setStyleSheet("QDialog { background: rgb(245,247,250); }")
        screen = QtGui.QGuiApplication.primaryScreen().availableGeometry()
        self.resize(int(min(screen.width() * 0.75, 1600)), int(min(screen.height() * 0.75, 900)))

        self.cpu_chart = CurveChart("CPU", limits["cpu_max"], limits["fan_max"])
        self.gpu_chart = CurveChart("GPU", limits["gpu_max"], limits["fan_max"])
        tip = QtWidgets.QLabel(tr("fan_curve_instructions"))
        tip.setStyleSheet("color: rgb(70,70,70); font-size: 12pt; padding: 4px 8px;")
        tip.setWordWrap(True)

        buttons = QtWidgets.QVBoxLayout()
        buttons.setSpacing(16)
        for text, slot in ((tr("fan_curve_save"), lambda: self._save(False)),
                           (tr("fan_curve_save_apply"), lambda: self._save(True)),
                           (tr("cancel"), self.reject),
                           (tr("fan_curve_load"), self._load),
                           (tr("fan_curve_reset"), self._reset)):
            button = QtWidgets.QPushButton(text)
            button.setMinimumSize(170, 48)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet("QPushButton { background: white; border: 1px solid rgb(220,225,230); "
                                 "border-radius: 4px; font-size: 11pt; } "
                                 "QPushButton:hover { background: rgb(238,243,248); }")
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)

        grid = QtWidgets.QGridLayout(self)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.addWidget(tip, 0, 0, 1, 2)
        grid.addWidget(self.cpu_chart, 1, 0)
        grid.addWidget(self.gpu_chart, 1, 1)
        grid.addLayout(buttons, 0, 2, 2, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        self._apply(data["profile"])

    def _apply(self, profile: dict) -> None:
        self.cpu_chart.set_points(profile["cpu"])
        self.gpu_chart.set_points(profile["gpu"])

    def profile(self) -> FanCurveProfile:
        return FanCurveProfile(self.cpu_chart.points, self.gpu_chart.points).validate()

    def _error(self, title: str, message: str) -> None:
        QtWidgets.QMessageBox.critical(self, tr("error"), f"{title}\n{message}")

    def _save(self, apply: bool) -> None:
        try:
            profile = self.profile()
        except InvalidFanCurve:
            self._error(tr("fan_curve_save_failed"), tr("fan_curve_out_of_range"))
            return

        def done(_result):
            self.saved.emit(self.table, apply)
            self.accept()

        self.client.call("fan_curve_set", {"table": self.table, "profile": profile.to_dict(), "apply": apply},
                         done, lambda code, msg: self._error(tr("fan_curve_save_failed"), msg))

    def _load(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, tr("fan_curve_load"), "", tr("fan_curve_file_filter"))
        if not path:
            return
        try:
            profile = FanCurveProfile.load(path)
        except (OSError, InvalidFanCurve) as exc:
            self._error(tr("fan_curve_load_failed"), str(exc))
            return
        self._apply(profile.to_dict())

    def _reset(self) -> None:
        self.client.call("fan_curve_get", {"table": self.table, "default": True},
                         lambda data: self._apply(data["profile"]),
                         lambda code, msg: self._error(tr("fan_curve_load_failed"), msg))
