# 任务：在这台电脑上安装并验证 OmenSuperHub for Linux

OmenSuperHub for Linux 是惠普暗影精灵（OMEN）/ 光影精灵（Victus）游戏本的风扇、功率与灯光控制工具，从 Windows 版 OmenSuperHub 移植而来。它由三部分组成：以 root 运行的 systemd 服务 omenhubd、命令行工具 omenhub、Qt 托盘程序 omenhub-gui。

- 代码：https://github.com/ttwang2353666-cell/1 （公开仓库）的 claude/eager-turing-xhg5nn 分支；如果这个分支已经不存在，说明已合并，改用默认分支
- 仓库里的 README.md 有完整说明，install.sh 负责安装
- 这个移植版还没有在真机上验证过，请谨慎操作：每一步确认结果后再继续；出错时先停下来分析原因，不要修改项目源码来绕过问题

## 工作方式

- 全程用中文和我交流。
- 能直接执行的命令就自己执行。需要 root 时，先运行 `sudo -n true` 检查能否免密码使用 sudo；不能的话，把需要 root 的命令整理成一段交给我，我在自己的终端里执行后把输出贴给你。
- 安装软件包、编译内核模块、修改系统配置之前，先用一两句话说明要做什么。

## 第 1 步：检查环境（只读）

收集并汇报：

1. 发行版和版本（/etc/os-release）、内核版本（uname -r）、Python 版本（需要 3.8 或更高）
2. 机型：/sys/class/dmi/id/ 下的 sys_vendor、product_name、board_name、bios_version
3. HP BIOS 的 WMI 接口是否存在：`ls /sys/bus/wmi/devices/ | grep -i 5FB7F034-2C63-45E9-BE91-3D44E2C707E4`
4. hp_wmi 驱动是否已加载（lsmod），acpi_call 模块是否已安装（modinfo acpi_call）
5. 安全启动状态（mokutil --sb-state）
6. 桌面环境和会话类型（$XDG_CURRENT_DESKTOP、$XDG_SESSION_TYPE）
7. 显卡（`lspci | grep -iE "vga|3d"`），nvidia-smi 是否可用
8. 是否有可能冲突的程序：power-profiles-daemon、tuned、thermald，以及 nbfc、omen-fan 等其他风扇控制工具

遇到下面的情况先停下来问我：

- 不是 HP 的 OMEN / Victus 游戏本
- 产品名含 OMEN，但主板号（board_name）以 84、85、86 开头：这是暗影精灵 6 及更早的机型，不支持
- 找不到上面的 WMI 设备

## 第 2 步：安装依赖

1. git、python3
2. acpi_call 内核模块（必需；没有它只能使用功能很少的 hp-wmi 降级模式）：
   - Debian / Ubuntu / Linux Mint：acpi-call-dkms 和 linux-headers-$(uname -r)
   - Arch / Manjaro：acpi_call-dkms 加上当前内核对应的 headers 包（标准内核也可以直接装 acpi_call）
   - 其他发行版：先在软件源里搜索 acpi_call；没有的话，按 https://github.com/nix-community/acpi_call 的 README 用 DKMS 编译安装（需要 dkms、当前内核的开发包、gcc、make）
3. Qt 的 Python 绑定（托盘程序需要）：Debian / Ubuntu 装 python3-pyqt6（Ubuntu 20.04、Debian 11 等旧版本装 python3-pyqt5）；Arch 装 python-pyqt6；Fedora 装 python3-pyqt6；openSUSE 装 python3-PyQt6。包名不对时用包管理器搜索 pyqt
4. 桌面是 GNOME 且不是 Ubuntu 时，还需要托盘扩展 gnome-shell-extension-appindicator；装好后要重新登录，再用 `gnome-extensions enable appindicatorsupport@rgcjonas.gmail.com` 启用

装好后运行 `sudo modprobe acpi_call`，确认 /proc/acpi/call 存在。

安全启动开启时，DKMS 编译的模块需要登记签名密钥（MOK）。安装过程中如果出现设置 MOK 密码的界面，按提示设置即可。如果加载模块时报 “Key was rejected by service”：找到 DKMS 的签名公钥（Ubuntu 一般是 /var/lib/shim-signed/mok/MOK.der，其他发行版一般是 /var/lib/dkms/mok.pub），让我运行 `sudo mokutil --import <公钥文件>` 设置一次性密码；重启时在蓝色的 MOK 管理界面依次选择 Enroll MOK → Continue → Yes，再输入这个密码。不要替我关闭安全启动；需要重启时先征得我同意，并告诉我重启后如何继续。

## 第 3 步：获取代码并安装（先不启动服务）

    git clone -b claude/eager-turing-xhg5nn https://github.com/ttwang2353666-cell/1.git ~/omensuperhub-linux
    cd ~/omensuperhub-linux
    sudo ./install.sh --no-start

目录已存在时，改为在该目录里运行 git pull。把 install.sh 的输出给我看。它会把当前用户加入 omenhub 组，重新登录后生效；在此之前，sudo、wheel 组的成员同样可以修改设置。

## 第 4 步：首次启动使用保守配置

默认情况下，首次启动会应用“极致性能”预设：CPU PL1/PL2 设为 160W，GPU 核心 +120MHz、显存 +400MHz 超频。Windows 版从 HP 的平台数据库读取每个机型的参数，Linux 下读不到，只能使用这组后备值。为了先安全地验证，在第一次启动前写入一个只接管风扇、不改功率也不超频的配置（只在配置文件还不存在时写入）：

    sudo install -d -m 0755 /var/lib/omenhub
    [ -e /var/lib/omenhub/config.json ] || echo '{"current_preset": "custom1", "presets": {"custom1": {}}}' | sudo tee /var/lib/omenhub/config.json
    sudo systemctl start omenhubd

这样启动后只会：把 BIOS 性能模式设为“释放全部性能”（与 Windows 版一致）、按降温曲线自动控制风扇、开启 CTGP 与 PPAB。不会修改 CPU 功率限制，也不会超频。

## 第 5 步：验证

1. `systemctl status omenhubd --no-pager` 和 `journalctl -u omenhubd -b --no-pager | tail -n 50`：服务应为 active，没有报错
2. `omenhub info`：“BIOS 接口”应为 acpi_call（hp-wmi 表示 acpi_call 没有加载，none 表示没有可用的 BIOS 接口），“机型支持情况”应为“完全支持”
3. `omenhub status`：应能看到 CPU 温度、功率和风扇转速
4. 风扇测试：依次运行 `omenhub fan max`、`omenhub fan 3000`，每次等大约 15 秒，再用 `omenhub status` 看转速有没有变化；最后一定要运行 `omenhub fan auto` 恢复自动
5. 保存诊断信息：`omenhub probe > ~/omenhub-probe.json`。服务运行时不要用 `sudo omenhubd --probe`，否则两个进程会同时访问 BIOS
6. 托盘：让我从应用菜单打开 OmenSuperHub（或在图形界面的终端里运行 omenhub-gui），确认托盘图标出现、右键菜单能打开。如果我希望登录后自动启动托盘，在托盘菜单“其他设置 → 开机自启”里开启

## 除非我明确要求，不要做这些

- 切换显卡模式（omenhub gfx 加模式名）：需要重启，配置不当可能黑屏
- 修改 CPU 功率、PPab、IccMax、Load Line 或 GPU 超频 / 频率限制（cpu-power、tpp、iccmax、loadline、gpu core-oc / mem-oc / clock）
- 切换到内置预设（extreme、gpu、light）
- 修改 BIOS 设置、关闭安全启动、停用或卸载其他系统服务、重启或注销

## 最后给我一份总结

1. 机型、发行版、内核、正在使用的 BIOS 接口
2. 每一步做了什么、结果如何（成功 / 失败 / 跳过），以及风扇测试的结果
3. 还需要我做的事（例如重新登录让用户组生效、登记 MOK、启用 GNOME 托盘扩展）
4. 如果 power-profiles-daemon 或 tuned 在运行：说明 OmenSuperHub 默认会把 BIOS 设为“释放全部性能”模式；如果我想继续使用系统自带的电源模式，可以运行 `omenhub thermal system`
5. 以后如何切换预设（托盘菜单“预设”，或 omenhub preset 加预设名），以及各预设会改动什么：
   - 极致性能：CPU 160W、Tpp 160W、GPU 核心 +120MHz、显存 +400MHz
   - GPU 优先：CPU 55W、Tpp 160W、GPU 核心 +120MHz
   - 轻度使用：CPU 33W、安静风扇曲线、关闭 CTGP 与 PPAB、GPU 核心 +120MHz
   - 160W 和 55W 是后备值，可以在 /var/lib/omenhub/config.json 的 platform 里用 pl1_performance、pl1_default 改成适合本机的值，然后运行 `sudo systemctl restart omenhubd`
6. 出问题时需要提供的诊断信息：`journalctl -u omenhubd -b` 的输出和 ~/omenhub-probe.json
