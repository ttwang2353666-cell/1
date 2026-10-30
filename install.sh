#!/usr/bin/env bash
# OmenSuperHub for Linux 安装脚本
#
#   sudo ./install.sh [--prefix /usr/local] [--no-start]
#
# 安装内容：
#   $PREFIX/lib/omenhub/omenhub            Python 程序包
#   $PREFIX/bin/{omenhubd,omenhub,omenhub-gui}
#   /etc/systemd/system/omenhubd.service   以 root 运行的守护进程
#   /etc/modules-load.d/omenhub.conf       开机加载 acpi_call（仅当已安装该模块）
#   $PREFIX/share/applications、icons      托盘程序的菜单项与图标
#
# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/usr/local
START=1
ARGS=("$@")

usage() {
    sed -n '2,11p' "$SRC/install.sh" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="${2:?--prefix 需要参数}"; shift 2 ;;
        --prefix=*) PREFIX="${1#*=}"; shift ;;
        --no-start) START=0; shift ;;
        -h|--help) usage 0 ;;
        *) echo "未知参数：$1" >&2; usage 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限，正在通过 sudo 重新运行……"
    exec sudo -- bash "$SRC/install.sh" ${ARGS[@]+"${ARGS[@]}"}
fi

LIBDIR="$PREFIX/lib/omenhub"
BINDIR="$PREFIX/bin"
APPDIR="$PREFIX/share/applications"
ICONDIR="$PREFIX/share/icons/hicolor/scalable/apps"
UNITDIR=/etc/systemd/system

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==> 注意：\033[0m%s\n' "$*"; }

# 优先使用系统自带的 Python，发行版的 PyQt 包安装在它下面
if [ -x /usr/bin/python3 ]; then
    PYTHON=/usr/bin/python3
else
    PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
    echo "需要 Python 3.8 或更高版本" >&2
    exit 1
fi

info "安装程序文件到 $LIBDIR"
mkdir -p "$LIBDIR" "$BINDIR"
rm -rf "$LIBDIR/omenhub"
cp -r "$SRC/omenhub" "$LIBDIR/"
find "$LIBDIR/omenhub" -name __pycache__ -type d -prune -exec rm -rf {} +
chmod -R u=rwX,go=rX "$LIBDIR"
"$PYTHON" -m compileall -q "$LIBDIR/omenhub" >/dev/null 2>&1 || true

# 生成启动脚本。守护进程以隔离模式（-I）运行，不受环境变量与用户 site-packages 影响
write_launcher() {
    local name="$1" module="$2" flags="$3"
    cat >"$BINDIR/$name" <<EOF
#!$PYTHON$flags
import sys
sys.path.insert(0, "$LIBDIR")
from $module import main
sys.exit(main())
EOF
    chmod 0755 "$BINDIR/$name"
}
write_launcher omenhubd omenhub.daemon " -I"
write_launcher omenhub omenhub.cli ""
write_launcher omenhub-gui omenhub.gui ""

info "安装桌面菜单项与图标"
mkdir -p "$APPDIR" "$ICONDIR"
sed "s|^Exec=omenhub-gui|Exec=$BINDIR/omenhub-gui|" "$SRC/packaging/omenhub-gui.desktop" >"$APPDIR/omenhub-gui.desktop"
install -m 0644 "$SRC/packaging/omenhub.svg" "$ICONDIR/omenhub.svg"
chmod 0644 "$APPDIR/omenhub-gui.desktop"
if command -v update-desktop-database >/dev/null; then
    update-desktop-database -q "$APPDIR" 2>/dev/null || true
fi
# 只刷新已有的图标缓存，不在 $PREFIX 下新建缓存文件
if [ -f "$PREFIX/share/icons/hicolor/icon-theme.cache" ] && command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

# 用户组：组内成员可以修改设置（wheel / sudo / admin 组成员同样可以）
if ! getent group omenhub >/dev/null; then
    info "创建用户组 omenhub"
    groupadd -r omenhub 2>/dev/null || addgroup -S omenhub
fi
NEED_RELOGIN=0
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    case " $(id -nG "$SUDO_USER") " in
        *" omenhub "*) ;;
        *)
            info "将用户 $SUDO_USER 加入 omenhub 组"
            usermod -aG omenhub "$SUDO_USER" 2>/dev/null || addgroup "$SUDO_USER" omenhub
            NEED_RELOGIN=1
            ;;
    esac
fi

# acpi_call 是完整功能所需的内核模块
ACPI_OK=0
if modprobe acpi_call 2>/dev/null || [ -e /proc/acpi/call ]; then
    ACPI_OK=1
    install -D -m 0644 "$SRC/packaging/acpi_call.conf" /etc/modules-load.d/omenhub.conf
else
    warn "未能加载 acpi_call 内核模块，守护进程将退回内核 hp-wmi 驱动（仅支持性能模式、风扇与显卡模式等部分功能）。"
    cat <<'EOF'
    安装 acpi_call 后重新启动服务即可获得完整功能：
      Debian / Ubuntu：sudo apt install acpi-call-dkms
      Arch Linux     ：sudo pacman -S acpi_call-dkms   （或对应内核的 acpi_call 包）
      Fedora / 其他  ：从 https://github.com/nix-community/acpi_call 编译安装（推荐配合 DKMS）
    开启了安全启动（Secure Boot）时，第三方模块需要签名：按 DKMS 的提示登记 MOK 密钥，
    或在 BIOS 中关闭安全启动。安装后执行：sudo systemctl restart omenhubd
EOF
fi

if [ -d "$UNITDIR" ]; then
    info "安装 systemd 服务"
    sed "s|@BINDIR@|$BINDIR|g" "$SRC/packaging/omenhubd.service" >"$UNITDIR/omenhubd.service"
    chmod 0644 "$UNITDIR/omenhubd.service"
fi
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
    systemctl enable omenhubd.service >/dev/null 2>&1 || warn "无法设置 omenhubd 开机启动"
    if [ "$START" = 1 ]; then
        # 升级时重启以加载新版本
        if systemctl restart omenhubd.service && sleep 2 && systemctl is-active --quiet omenhubd.service; then
            info "omenhubd 已启动"
        else
            warn "omenhubd 启动失败，请查看日志：journalctl -u omenhubd -b"
        fi
    fi
else
    warn "未检测到 systemd，请通过系统的服务管理器以 root 身份运行 $BINDIR/omenhubd"
fi

# 托盘程序需要 Qt 的 Python 绑定
if ! "$PYTHON" -c 'import PyQt6' 2>/dev/null && ! "$PYTHON" -c 'import PySide6' 2>/dev/null \
        && ! "$PYTHON" -c 'import PyQt5' 2>/dev/null; then
    warn "没有找到 PyQt6 / PySide6 / PyQt5，托盘程序无法运行（命令行工具不受影响）。"
    cat <<'EOF'
      Debian / Ubuntu：sudo apt install python3-pyqt6      （较旧的系统用 python3-pyqt5）
      Arch Linux     ：sudo pacman -S python-pyqt6
      Fedora         ：sudo dnf install python3-pyqt6
      openSUSE       ：sudo zypper install python3-PyQt6
EOF
fi
if ! command -v nvidia-smi >/dev/null; then
    echo "    （提示：使用 NVIDIA 独显时需要安装官方驱动，显卡监控与超频依赖 nvidia-smi / NVML）"
fi

echo
info "安装完成"
echo "    托盘程序：在应用菜单中打开 OmenSuperHub，或运行 omenhub-gui"
echo "    命令行  ：omenhub status / omenhub --help"
echo "    问题反馈：附上 sudo omenhubd --probe 的输出"
if [ "$NEED_RELOGIN" = 1 ]; then
    echo "    用户 $SUDO_USER 已加入 omenhub 组，重新登录后生效"
fi
if [ "$ACPI_OK" = 0 ]; then
    echo "    当前处于受限模式（缺少 acpi_call），见上方说明"
fi
