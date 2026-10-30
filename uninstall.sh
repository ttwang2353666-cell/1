#!/usr/bin/env bash
# OmenSuperHub for Linux 卸载脚本
#
#   sudo ./uninstall.sh [--prefix /usr/local] [--purge]
#
# 默认保留 /var/lib/omenhub 中的配置与风扇曲线；--purge 同时删除它们和 omenhub 用户组。
# 各用户的 ~/.config/omenhub 与自启动项需要用户自行删除。
#
# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/usr/local
PURGE=0
ARGS=("$@")

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="${2:?--prefix 需要参数}"; shift 2 ;;
        --prefix=*) PREFIX="${1#*=}"; shift ;;
        --purge) PURGE=1; shift ;;
        -h|--help) sed -n '2,7p' "$SRC/uninstall.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" -ne 0 ]; then
    echo "需要 root 权限，正在通过 sudo 重新运行……"
    exec sudo -- bash "$SRC/uninstall.sh" ${ARGS[@]+"${ARGS[@]}"}
fi

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }

if [ -d /run/systemd/system ]; then
    info "停止并禁用 omenhubd"
    systemctl disable --now omenhubd.service >/dev/null 2>&1 || true
fi
rm -f /etc/systemd/system/omenhubd.service /etc/modules-load.d/omenhub.conf
if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
fi

info "删除程序文件"
rm -f "$PREFIX/bin/omenhubd" "$PREFIX/bin/omenhub" "$PREFIX/bin/omenhub-gui"
rm -rf "$PREFIX/lib/omenhub"
rm -f "$PREFIX/share/applications/omenhub-gui.desktop" "$PREFIX/share/icons/hicolor/scalable/apps/omenhub.svg"
if command -v update-desktop-database >/dev/null; then
    update-desktop-database -q "$PREFIX/share/applications" 2>/dev/null || true
fi
if [ -f "$PREFIX/share/icons/hicolor/icon-theme.cache" ] && command -v gtk-update-icon-cache >/dev/null; then
    gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

if [ "$PURGE" = 1 ]; then
    info "删除配置与 omenhub 用户组"
    rm -rf /var/lib/omenhub
    if getent group omenhub >/dev/null; then
        groupdel omenhub 2>/dev/null || delgroup omenhub 2>/dev/null || true
    fi
else
    echo "    配置保留在 /var/lib/omenhub（使用 --purge 一并删除）"
fi
echo "    如不再需要 acpi_call 模块，可用发行版的包管理器卸载（如 acpi-call-dkms）"
info "卸载完成"
