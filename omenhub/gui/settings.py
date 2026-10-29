"""图形界面的个人设置（~/.config/omenhub/gui.json）：语言、图标、浮窗、自启动等。"""

import json
import logging
import os
import sys

from .. import paths

log = logging.getLogger(__name__)

DEFAULTS = {
    "language": None,          # None 表示跟随系统
    "icon": "original",        # original / custom / dynamic
    "custom_icon": "",         # 为空时使用 ~/.config/omenhub/custom.png
    "overlay": False,
    "overlay_size": 40,        # 与 OSH 的 FloatingBarSize 相同（24~72）
    "overlay_location": "left",
    "overlay_screen": "",
    "already_read": "",        # 已阅读帮助的版本，每个版本首次启动显示一次
    "omen_key_app": "",        # Omen 键“打开应用”的命令或 .desktop 文件（只由本用户的托盘程序执行）
    "omen_key_app_name": "",
}

AUTOSTART_NAME = "omenhub-gui.desktop"


class GuiSettings:
    def __init__(self, path: str = None):
        self.path = path or os.path.join(paths.user_config_dir(), "gui.json")
        self.data = dict(DEFAULTS)

    def load(self) -> "GuiSettings":
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                stored = json.load(f)
            if isinstance(stored, dict):
                for key, default in DEFAULTS.items():
                    value = stored.get(key, default)
                    if default is None or isinstance(value, type(default)):
                        self.data[key] = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("cannot read %s: %s", self.path, exc)
        self.data["overlay_size"] = max(24, min(72, int(self.data["overlay_size"])))
        return self

    def save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.error("cannot write %s: %s", self.path, exc)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value
        self.save()

    def custom_icon_path(self) -> str:
        return self.data["custom_icon"] or os.path.join(paths.user_config_dir(), "custom.png")


def autostart_path() -> str:
    return os.path.join(paths.user_autostart_dir(), AUTOSTART_NAME)


def autostart_enabled() -> bool:
    return os.path.exists(autostart_path())


def set_autostart(enabled: bool) -> None:
    """登录后自动启动托盘程序（守护进程由 systemd 负责开机启动）。"""
    path = autostart_path()
    if not enabled:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exe = "omenhub-gui"
    for candidate in ("/usr/bin/omenhub-gui", "/usr/local/bin/omenhub-gui"):
        if os.path.exists(candidate):
            exe = candidate
            break
    else:
        if not any(os.access(os.path.join(d, "omenhub-gui"), os.X_OK)
                   for d in os.environ.get("PATH", "").split(os.pathsep) if d):
            exe = f"{sys.executable} -m omenhub.gui"
    with open(path, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=OmenSuperHub\n"
                f"Exec={exe} --autostart\nIcon=omenhub\nTerminal=false\n"
                "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=3\n")
