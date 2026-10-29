"""OmenSuperHub for Linux 托盘程序（Qt）。"""

import importlib
import shutil
import subprocess
import sys


def main(argv=None) -> int:
    """omenhub-gui 入口：缺少 Qt 绑定时给出可读的提示，而不是一段异常堆栈。"""
    try:
        importlib.import_module(f"{__name__}.qt")
    except ImportError as exc:
        print(f"omenhub-gui: {exc}", file=sys.stderr)
        if shutil.which("notify-send"):  # 从应用菜单启动时没有终端
            subprocess.run(["notify-send", "OmenSuperHub", str(exc)], check=False)
        return 1
    from .app import main as app_main
    return app_main(argv)
