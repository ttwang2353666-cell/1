"""运行时路径。均可通过环境变量覆盖，便于测试或打包。"""

import os

# 守护进程状态目录（配置、风扇曲线），对应 systemd 的 StateDirectory=omenhub
STATE_DIR = os.environ.get("OMENHUB_STATE_DIR", "/var/lib/omenhub")
# 运行时目录（套接字、数据本地化文本），对应 systemd 的 RuntimeDirectory=omenhub
RUNTIME_DIR = os.environ.get("OMENHUB_RUNTIME_DIR", "/run/omenhub")
SOCKET_PATH = os.environ.get("OMENHUB_SOCKET", os.path.join(RUNTIME_DIR, "omenhub.sock"))
CONFIG_PATH = os.path.join(STATE_DIR, "config.json")


def user_config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "omenhub")


def user_autostart_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart")
