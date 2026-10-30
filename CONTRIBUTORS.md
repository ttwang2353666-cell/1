# 贡献者 / Contributors

## 维护者 / Maintainer

- **ttwang2353666-cell**（[GitHub](https://github.com/ttwang2353666-cell)）：项目发起人与维护者，负责需求、真机测试与发布。
  Project initiator and maintainer: requirements, testing on real hardware and releases.

## Linux 移植版作者 / Authors of the Linux port

- **Claude**（Anthropic 的 AI 助手）：在维护者的指导下编写了 Linux 移植版的代码、测试与文档，相关提交带有 `Co-Authored-By: Claude` 标注。
  Claude, Anthropic's AI assistant, wrote the code, tests and documentation of the Linux port under the maintainer's direction; its commits carry a `Co-Authored-By: Claude` trailer.

## 原版作者 / Original work

- **breaded**（[@breadeding](https://github.com/breadeding)）：Windows 版 [OmenSuperHub](https://github.com/breadeding/OmenSuperHub) 的作者（原版标注为 “Copyright © breaded”）。本项目移植自 OmenSuperHub v2026.7.12.0 的源码，原版以 GPL-3.0 发布，原有部分的版权归原作者所有。
  Author of OmenSuperHub for Windows (“Copyright © breaded”). This project is a port of the OmenSuperHub v2026.7.12.0 source code, which is released under GPL-3.0; the original author keeps the copyright of the original work.

## 借鉴与参考的项目 / Referenced projects

| 项目 / Project | 作者 / Authors | 借鉴的内容 / What was used |
| --- | --- | --- |
| [OmenMon](https://github.com/OmenMon/OmenMon)、[OmenHwCtl](https://github.com/GeographicCone/OmenHwCtl) | GeographicCone | 对 HP BIOS WMI 命令的研究，是原版 OmenSuperHub 的主要灵感来源 / Research on the HP BIOS WMI commands, the main inspiration of the original OmenSuperHub |
| Linux 内核 [hp-wmi](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/hp/hp-wmi.c) 驱动 / Linux kernel hp-wmi driver | Matthew Garrett (Red Hat)、Anssi Hannula 及其他内核开发者 / and other kernel developers | BIOS 调用格式与 hp-wmi 降级接口的参考 / Reference for the BIOS call format and the hp-wmi fallback |
| Linux 内核 [WMI](https://github.com/torvalds/linux/blob/master/drivers/platform/x86/wmi.c) 驱动 / Linux kernel WMI driver | Carlos Corbacho 及其他内核开发者 / and other kernel developers | WMI 设备在 sysfs 中的结构 / sysfs layout of WMI devices |
| [acpi_call](https://github.com/nix-community/acpi_call) | Michal Kottman，现由 nix-community 维护 / maintained by nix-community | 调用 BIOS 方法的内核模块（运行时依赖）/ Kernel module used to call the BIOS methods (runtime dependency) |
| ThrottleStop | Kevin Glynn (unclewebb) | CPU 降压功能的灵感来源；ThrottleStop 是闭源软件，未使用其代码 / Inspiration for CPU undervolting; ThrottleStop is closed source and none of its code is used |
| [intel-undervolt](https://github.com/kitsunyan/intel-undervolt)、[undervolt](https://github.com/georgewhewell/undervolt) | kitsunyan、George Whewell | MSR 0x150 电压偏移接口的公开说明 / Public documentation of the MSR 0x150 voltage offset interface |

以上借鉴项目的作者没有直接参与本项目，列出是为了致谢并说明来源；本项目的问题请在本仓库反馈，不要发给他们。

The authors of the referenced projects did not take part in this project; they are listed for credit and provenance. Please report problems with this project here, not to them.
