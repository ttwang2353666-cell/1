"""图标：按原版 fan.ico 的样式用 QPainter 绘制（不依赖 Qt SVG 插件）；动态图标显示当前温度。"""

import os

from .qt import QtCore, QtGui, Qt

RING = QtGui.QColor(43, 43, 43)
BLADE = QtGui.QColor(163, 196, 91)
TAN = QtGui.QColor(210, 180, 140)


def _blade_path() -> QtGui.QPainterPath:
    """一片扇叶（100×100 坐标系，指向右上方）。"""
    path = QtGui.QPainterPath()
    path.moveTo(48.5, 39.5)
    path.cubicTo(50.0, 33.0, 44.0, 27.0, 45.0, 20.0)
    path.cubicTo(46.0, 13.0, 55.0, 11.0, 62.0, 13.0)
    path.cubicTo(72.0, 16.0, 77.0, 26.0, 75.0, 34.0)
    path.cubicTo(73.0, 41.0, 66.0, 45.0, 60.5, 48.0)
    path.cubicTo(59.0, 44.0, 54.0, 40.0, 48.5, 39.5)
    path.closeSubpath()
    return path


def draw_fan(painter: QtGui.QPainter, rect: QtCore.QRectF, background: bool = True) -> None:
    painter.save()
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 100.0, rect.height() / 100.0)
    painter.setPen(Qt.PenStyle.NoPen)
    if background:
        painter.setBrush(QtGui.QColor(255, 255, 255))
        painter.drawEllipse(QtCore.QPointF(50, 50), 49, 49)
    pen = QtGui.QPen(RING, 8.0)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QtCore.QPointF(50, 50), 44.5, 44.5)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(BLADE)
    blade = _blade_path()
    for angle in (0, 120, 240):
        transform = QtGui.QTransform()
        transform.translate(50, 50)
        transform.rotate(angle)
        transform.translate(-50, -50)
        painter.drawPath(transform.map(blade))
    painter.setBrush(QtGui.QColor(255, 255, 255))
    painter.drawEllipse(QtCore.QPointF(50, 50), 10.5, 10.5)
    painter.setBrush(RING)
    painter.drawEllipse(QtCore.QPointF(50, 50), 7.0, 7.0)
    painter.restore()


def fan_pixmap(size: int) -> QtGui.QPixmap:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    draw_fan(painter, QtCore.QRectF(0, 0, size, size))
    painter.end()
    return pixmap


_APP_ICON = None


def app_icon() -> QtGui.QIcon:
    global _APP_ICON
    if _APP_ICON is None:
        _APP_ICON = QtGui.QIcon()
        for size in (16, 22, 24, 32, 48, 64, 128, 256):
            _APP_ICON.addPixmap(fan_pixmap(size))
    return _APP_ICON


def temperature_icon(value: float, size: int = 64) -> QtGui.QIcon:
    """GenerateDynamicIcon：两位数温度，Tan 色粗体。"""
    text = f"{int(max(0, min(99, round(value)))):02d}"
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    font = QtGui.QFont("Arial")
    font.setBold(True)
    font.setPixelSize(int(size * 0.78))
    path = QtGui.QPainterPath()
    path.addText(0, 0, font, text)
    bounds = path.boundingRect()
    scale = min((size - 2) / max(1.0, bounds.width()), (size - 2) / max(1.0, bounds.height()))
    painter.translate(size / 2.0, size / 2.0)
    painter.scale(scale, scale)
    painter.translate(-bounds.center().x(), -bounds.center().y())
    painter.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30, 200), size / 18.0 / scale))
    painter.setBrush(TAN)
    painter.drawPath(path)
    painter.end()
    return QtGui.QIcon(pixmap)


def custom_icon(path: str):
    if path and os.path.isfile(path):
        icon = QtGui.QIcon(path)
        if not icon.isNull():
            return icon
    return None
