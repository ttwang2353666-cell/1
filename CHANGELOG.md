# 更新日志 / Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。
The format follows Keep a Changelog and the project uses Semantic Versioning.

## [1.0.0] - 2026-10-30

首个公开版本，移植自 Windows 版 [OmenSuperHub](https://github.com/breadeding/OmenSuperHub) v2026.7.12.0。
First public release, ported from OmenSuperHub v2026.7.12.0 for Windows.

### 新增 / Added

- 以 root 运行的后台服务 `omenhubd`：通过 acpi_call 调用 HP BIOS 的 WMI 接口，没有 acpi_call 时退回内核 hp-wmi 驱动；负责预设、风扇曲线、功率、灯光、BIOS 心跳、交流电源与睡眠恢复后重新应用设置。
  Root daemon `omenhubd` that calls the HP BIOS WMI interface through acpi_call (falling back to the kernel hp-wmi driver) and runs presets, fan curves, power limits, lighting, the BIOS heartbeat and re-applies settings after AC changes and resume.
- 与原版一致的预设（极致性能、GPU 优先、轻度使用、自定义 1~3）、风扇配置与曲线编辑器（曲线文件与 Windows 版通用）、风扇控制、高温自动保护、反转除尘。
  Presets, fan profiles with a curve editor (file format shared with Windows), fan control, high-temperature protection and Clean Creek, matching the original.
- 性能控制：BIOS 性能模式（新增“不接管”）、CPU 功率、PPab、IccMax、AC Load Line、显卡 CTGP / PPAB / D 状态、独显直连切换、NVIDIA 超频与频率限制、nvidia-powerd 开关、独显进程管理。
  Performance control: BIOS thermal mode (with a new “leave to system” option), CPU power, PPab, IccMax, AC load line, GPU CTGP / PPAB / D-state, MUX switching, NVIDIA overclocking and clock limits, nvidia-powerd and dGPU process management.
- CPU 降压（Linux 版新增）：通过 MSR 0x150 设置 Intel 核心与缓存的电压偏移，写入后读回校验，记录开机原始值，异常关机后暂停自动应用。
  CPU undervolting (new in the Linux version): Intel core and cache voltage offsets through MSR 0x150, with read-back verification, per-boot original values and automatic pause after an unclean shutdown.
- 四分区键盘与灯条灯光（basic 与 Dojo 协议）、Omen 键自定义（evdev + uinput）、硬件监控、动态托盘图标、监控浮窗、数据本地化。
  Four-zone keyboard and light bar lighting (basic and Dojo protocols), Omen key actions (evdev + uinput), hardware monitoring, dynamic tray icon, overlay and data localization.
- Qt 托盘程序 `omenhub-gui`（支持 PyQt6 / PySide6 / PyQt5）与控制面板、命令行工具 `omenhub`，界面提供简体中文、繁体中文与英文。
  Qt tray app `omenhub-gui` (PyQt6 / PySide6 / PyQt5) with a control panel, the `omenhub` command line tool, and Simplified Chinese, Traditional Chinese and English.
- 安装与卸载脚本、systemd 服务、桌面菜单项，以及交给 AI 助手执行的安装提示词。
  Install and uninstall scripts, systemd unit, desktop entry and an install prompt for AI assistants.

### 未移植 / Not ported

- DB 解锁、最大帧率、热切换、重启显卡与单键 RGB：依赖 Windows 专有驱动接口。
  DB unlock, max frame rate, hot switch, GPU restart and per-key RGB, which depend on proprietary Windows driver interfaces.

[1.0.0]: https://github.com/ttwang2353666-cell/1/releases/tag/v1.0.0
