"""Omen 键（Program.OmenKey.cs 的 Linux 版）。

Windows 版通过 WMI 事件订阅 hpqBEvnt(eventId=29, eventData=8613) 捕获 Omen 键；Linux 内核的
hp-wmi 驱动把同一事件（0x21A5）映射为 "HP WMI hotkeys" 输入设备上的 KEY_PROG2，
这里直接读取 evdev。按键模拟通过 uinput 虚拟键盘实现，X11 与 Wayland 下均可用。
"""

import fcntl
import logging
import os
import struct
import time

log = logging.getLogger(__name__)

EV_SYN = 0x00
EV_KEY = 0x01
SYN_REPORT = 0
KEY_PROG2 = 149
HOTKEY_DEVICE_NAME = "HP WMI hotkeys"

_EVENT = struct.Struct("llHHi")  # struct input_event（64 位：timeval + type + code + value）

KEYCODES = {
    "ESC": 1, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9, "9": 10, "0": 11,
    "-": 12, "=": 13, "BACKSPACE": 14, "TAB": 15, "Q": 16, "W": 17, "E": 18, "R": 19, "T": 20,
    "Y": 21, "U": 22, "I": 23, "O": 24, "P": 25, "[": 26, "]": 27, "ENTER": 28, "CTRL": 29,
    "A": 30, "S": 31, "D": 32, "F": 33, "G": 34, "H": 35, "J": 36, "K": 37, "L": 38, ";": 39,
    "'": 40, "`": 41, "SHIFT": 42, "\\": 43, "Z": 44, "X": 45, "C": 46, "V": 47, "B": 48, "N": 49,
    "M": 50, ",": 51, ".": 52, "/": 53, "RIGHTSHIFT": 54, "ALT": 56, "SPACE": 57, "CAPSLOCK": 58,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65, "F8": 66, "F9": 67,
    "F10": 68, "NUMLOCK": 69, "SCROLLLOCK": 70, "F11": 87, "F12": 88, "RIGHTCTRL": 97,
    "PRINTSCREEN": 99, "RIGHTALT": 100, "HOME": 102, "UP": 103, "PAGEUP": 104, "LEFT": 105,
    "RIGHT": 106, "END": 107, "DOWN": 108, "PAGEDOWN": 109, "INSERT": 110, "DELETE": 111,
    "MUTE": 113, "VOLUMEDOWN": 114, "VOLUMEUP": 115, "PAUSE": 119, "SUPER": 125, "MENU": 127,
    "CALCULATOR": 140, "WWW": 150, "MAIL": 155, "NEXT": 163, "PLAYPAUSE": 164, "PREVIOUS": 165,
    "STOP": 166, "F13": 183, "F14": 184, "F15": 185, "F16": 186, "F17": 187, "F18": 188,
    "F19": 189, "F20": 190, "F21": 191, "F22": 192, "F23": 193, "F24": 194,
}
KEY_ALIASES = {
    "CONTROL": "CTRL", "LCTRL": "CTRL", "LEFTCTRL": "CTRL", "RCTRL": "RIGHTCTRL",
    "LSHIFT": "SHIFT", "LEFTSHIFT": "SHIFT", "RSHIFT": "RIGHTSHIFT",
    "LALT": "ALT", "LEFTALT": "ALT", "RALT": "RIGHTALT", "ALTGR": "RIGHTALT",
    "WIN": "SUPER", "META": "SUPER", "LWIN": "SUPER", "CMD": "SUPER", "LOGO": "SUPER",
    "ESCAPE": "ESC", "RETURN": "ENTER", "DEL": "DELETE", "INS": "INSERT", "PGUP": "PAGEUP",
    "PRIOR": "PAGEUP", "PGDN": "PAGEDOWN", "NEXT_PAGE": "PAGEDOWN", "PRINT": "PRINTSCREEN",
    "SYSRQ": "PRINTSCREEN", "MINUS": "-", "EQUAL": "=", "PLUS": "=", "COMMA": ",", "PERIOD": ".",
    "DOT": ".", "SLASH": "/", "BACKSLASH": "\\", "SEMICOLON": ";", "APOSTROPHE": "'",
    "GRAVE": "`", "BRACKETLEFT": "[", "BRACKETRIGHT": "]", "MEDIAPLAY": "PLAYPAUSE",
    "MEDIANEXT": "NEXT", "MEDIAPREVIOUS": "PREVIOUS", "MEDIASTOP": "STOP", "VOLUMEMUTE": "MUTE",
}
MODIFIERS = ("CTRL", "SHIFT", "ALT", "SUPER", "RIGHTCTRL", "RIGHTSHIFT", "RIGHTALT")


def parse_shortcut(text: str):
    """"Ctrl+Alt+T" -> [29, 56, 20]；修饰键在前，必须且只能有一个普通键。"""
    modifiers, key = [], None
    for raw in (text or "").split("+"):
        name = raw.strip().upper().replace(" ", "")
        if not name:
            continue
        name = KEY_ALIASES.get(name, name)
        if name not in KEYCODES:
            raise ValueError(f"unknown key: {raw.strip()}")
        if name in MODIFIERS:
            if KEYCODES[name] not in modifiers:
                modifiers.append(KEYCODES[name])
        elif key is None:
            key = KEYCODES[name]
        else:
            raise ValueError("only one non-modifier key is allowed")
    if key is None:
        raise ValueError("missing key")
    return modifiers + [key]


def find_hotkey_device(name: str = HOTKEY_DEVICE_NAME):
    """在 /proc/bus/input/devices 中查找 HP WMI hotkeys 对应的 /dev/input/eventN。"""
    try:
        with open("/proc/bus/input/devices", "r", encoding="utf-8", errors="replace") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return None
    for block in blocks:
        if f'Name="{name}"' not in block:
            continue
        for line in block.splitlines():
            if line.startswith("H: Handlers="):
                for handler in line.split("=", 1)[1].split():
                    if handler.startswith("event"):
                        return f"/dev/input/{handler}"
    return None


class OmenKeyListener:
    """在 asyncio 事件循环中监听 Omen 键；设备消失（例如重新加载 hp-wmi）后自动重连。"""

    def __init__(self, loop, callback, keycode: int = KEY_PROG2, device: str = None):
        self.loop = loop
        self.callback = callback
        self.keycode = keycode
        self.fixed_device = device
        self.path = None
        self.fd = None
        self._retry = None

    @property
    def connected(self) -> bool:
        return self.fd is not None

    def start(self) -> None:
        path = self.fixed_device or find_hotkey_device()
        if path:
            try:
                self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self.path = path
                self.loop.add_reader(self.fd, self._on_readable)
                log.info("listening for the Omen key on %s", path)
                return
            except OSError as exc:
                log.warning("cannot open %s: %s", path, exc)
                self.fd = None
        self._retry = self.loop.call_later(15, self.start)

    def stop(self) -> None:
        if self._retry:
            self._retry.cancel()
            self._retry = None
        if self.fd is not None:
            self.loop.remove_reader(self.fd)
            os.close(self.fd)
            self.fd = None

    def _on_readable(self) -> None:
        try:
            data = os.read(self.fd, _EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError as exc:
            log.warning("Omen key device lost: %s", exc)
            self.stop()
            self._retry = self.loop.call_later(5, self.start)
            return
        for offset in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
            _sec, _usec, etype, code, value = _EVENT.unpack_from(data, offset)
            if etype == EV_KEY and code == self.keycode and value == 1:
                try:
                    self.callback()
                except Exception:  # noqa: BLE001 - 回调异常不能中断监听
                    log.exception("Omen key callback failed")


# ---------------------------------------------------------------------------- uinput
UI_SET_EVBIT = 0x40045564
UI_SET_KEYBIT = 0x40045565
UI_DEV_CREATE = 0x5501
UI_DEV_DESTROY = 0x5502
UI_DEV_SETUP = 0x405C5503
BUS_VIRTUAL = 0x06


class VirtualKeyboard:
    """uinput 虚拟键盘，用于 Omen 键“模拟按键”。"""

    def __init__(self, path: str = "/dev/uinput"):
        self.fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
            fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_SYN)
            for code in sorted(set(KEYCODES.values())):
                fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
            name = b"OmenSuperHub virtual keyboard"
            try:
                fcntl.ioctl(self.fd, UI_DEV_SETUP, struct.pack("HHHH80sI", BUS_VIRTUAL, 0x03F0, 0x4F48, 1, name, 0))
            except OSError:
                # 旧内核：写入 struct uinput_user_dev
                os.write(self.fd, struct.pack("80sHHHHi", name, BUS_VIRTUAL, 0x03F0, 0x4F48, 1, 0)
                         + bytes(4 * 64 * 4))
            fcntl.ioctl(self.fd, UI_DEV_CREATE)
        except OSError:
            os.close(self.fd)
            raise
        self.created = time.monotonic()

    def _emit(self, etype: int, code: int, value: int) -> None:
        os.write(self.fd, _EVENT.pack(0, 0, etype, code, value))

    def send(self, codes) -> None:
        # 设备刚创建时桌面环境尚未接管，稍等片刻
        wait = 0.3 - (time.monotonic() - self.created)
        if wait > 0:
            time.sleep(wait)
        for code in codes:
            self._emit(EV_KEY, code, 1)
            self._emit(EV_SYN, SYN_REPORT, 0)
        for code in reversed(codes):
            self._emit(EV_KEY, code, 0)
            self._emit(EV_SYN, SYN_REPORT, 0)

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)
