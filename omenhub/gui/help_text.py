# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-FileCopyrightText: breaded <https://github.com/breadeding/OmenSuperHub>
# SPDX-License-Identifier: GPL-3.0-only
#
# Modified: ported from OmenSuperHub (C#, Windows) to Python for Linux, October 2026.
"""帮助内容（HelpForm.cs），按 Linux 版的实际行为改写。"""

from .. import UPSTREAM_VERSION, __version__
from ..i18n import current_language

UPSTREAM = "https://github.com/breadeding/OmenSuperHub"

STYLE = """
<style>
 body { font-size: 11pt; line-height: 1.5; color: #1e1e1e; }
 h2 { font-size: 13pt; color: #2980b9; margin-top: 14px; margin-bottom: 4px; }
 p { margin-top: 2px; margin-bottom: 2px; }
 code { background: #f0f2f5; }
 a { color: #3498db; }
</style>
"""

ZH_CN = """
<h2>📦 OmenSuperHub for Linux v{version}</h2>
<p>移植自 Windows 版 OmenSuperHub v{upstream}，原项目：<a href="{url}">{url}</a>（GPL-3.0）。</p>
<p>Linux 版由两部分组成：以 root 运行的后台服务 <code>omenhubd</code>（负责与 BIOS 通信、风扇曲线、预设），
以及当前的托盘程序与命令行工具 <code>omenhub</code>。退出托盘程序不会停止风扇控制。</p>
<hr/>
<h2>一. “风扇配置”菜单说明</h2>
<p>(1) 支持安静、降温和自定义三种温度-转速配置，分别保存在 <code>/var/lib/omenhub/silent.txt</code>、<code>cool.txt</code>、<code>custom.txt</code>，格式与 Windows 版相同，可直接复制使用；首次应用自定义时会先复制 cool.txt；</p>
<p>(2) 在“编辑曲线”中：左键增加节点、拖动调整节点、右键删除节点；“保存”只写入文件，“保存并应用”会写入并立即切换到该曲线，“加载”可载入其他 txt 配置；程序自动进行线性插值，精度为1℃；</p>
<p>(3) “实时”、“高”、“中”和“低”分别以从无到高的强度对温度进行平滑处理。</p>
<h2>二. “风扇控制”菜单说明</h2>
<p>(1) 选择“自动”则程序根据风扇配置和当前温度（CPU 与 GPU 查表取较大值）设定风扇转速；</p>
<p>(2) “最大风扇”为BIOS控制，不一定是最大转速；</p>
<p>(3) 选择固定转速时，若开启高温自动保护，CPU温度高于（温度墙-2）℃时将弹出通知并转为降温模式+自动风扇；</p>
<p>(4) 部分机型只有在BIOS设置中关闭“风扇始终启动”后才能低于2000转；</p>
<p>(5) 反转除尘需要 BIOS 支持，转速与时长可在配置文件的 platform 中调整。</p>
<h2>三. “性能控制”菜单说明</h2>
<p>(1) BIOS性能模式：OSH 固定使用释放全部性能的模式，并通过 CPU 功率、PPab 等选项控制功耗；若希望由 power-profiles-daemon / TuneD 控制，请选择“不接管”；</p>
<p>(2) 图形模式等同于BIOS设置中的独显直连切换（需重启）；</p>
<p>(3) PPab条件（Tpp）增加在CPU和GPU之间分配的总功率，为 NVIDIA Dynamic Boost 提供更大的功率容量；</p>
<p>(4) IccMax是CPU的最大电流限值（缺少 HP 平台数据库，默认隐藏，可在配置中开启）；AC Load Line通过调整CPU电压补偿负载下的电压降；</p>
<p>(5) 显卡功耗=BTGP+CTGP+DB/Ppab，开启CTGP和DB才能获得最大GPU性能；Linux 下的 Dynamic Boost 由 nvidia-powerd 服务提供；</p>
<p>(6) 修改CPU功率会同时修改PL1与PL2；“不设置”表示不修改 BIOS 当前值；</p>
<p>(7) GPU频率限制通过 nvidia-smi 锁定最高频率，不是超频；GPU核心/显存超频通过 NVML 设置，需要较新的驱动；</p>
<p>(8) Windows 版的“DB解锁”、“最大帧率”、“热切换”、“重启显卡”与单键RGB依赖 Windows 驱动接口，未移植；单键 RGB 键盘可使用 OpenRGB。</p>
<p>(9) CPU降压：与 ThrottleStop 相同，通过 MSR 0x150 同时调整核心与缓存的电压偏移。仅支持 Intel，并且需要 BIOS 没有锁定降压、内核没有开启 lockdown（开启安全启动时通常会开启）。“不设置”恢复本次开机时的原始值；降压只在后台服务运行期间生效。如果应用降压后系统没有正常关机，下次开机会暂停自动降压，确认稳定后重新选择数值即可。降压过度会导致死机，请从小幅度开始逐步加大，并做稳定性测试。</p>
<h2>四. “硬件监控”菜单说明</h2>
<p>(1) 可开启或关闭对应的监控信息。混合模式下独显休眠时显示“节能”，此时不会读取数据，避免把独显唤醒；独显空闲时也会暂停读取，让它有机会进入节能状态。</p>
<h2>五. “浮窗显示”菜单说明</h2>
<p>(1) 开启后，屏幕左上角或右上角将显示硬件监控信息；左键点击托盘图标可快速切换；</p>
<p>(2) Wayland 下浮窗通过 XWayland 显示（需要 XWayland），以便定位与置顶。</p>
<h2>六. “Omen键”菜单说明</h2>
<p>(1) Omen 键在 Linux 下由 hp-wmi 驱动上报为 XF86Launch2（KEY_PROG2）；“默认”时 OSH 不处理，可在桌面环境中自行绑定；</p>
<p>(2) 可选择切换浮窗显示、在候选预设之间循环切换、打开应用（命令或 .desktop 文件）、或模拟一个按键组合（通过 uinput，X11/Wayland 均可用）。</p>
<h2>七. “其他设置”菜单说明</h2>
<p>(1) “自定义图标”读取 <code>~/.config/omenhub/custom.png</code>；“动态图标”以当前CPU温度（无CPU温度时为GPU温度）作为图标；</p>
<p>(2) “数据本地化”开启后会把CPU、GPU温度及风扇转速写入 <code>/run/omenhub/cpu_temp.txt</code>、<code>gpu_temp.txt</code>、<code>fan_rpm.txt</code>；</p>
<p>(3) 后台服务由 systemd 开机启动；“开机自启”控制托盘程序在登录后自动启动；</p>
<p>(4) “语言”可切换简体中文、繁体中文和英语。</p>
<h2>八. 常见问题</h2>
<p>(1) 需要 acpi_call 内核模块才能使用全部功能；没有时会降级到内核 hp-wmi 驱动的接口，仅支持部分功能；</p>
<p>(2) 普通用户需要属于 omenhub、wheel、sudo 或 admin 组才能修改设置；</p>
<p>(3) 命令行：<code>omenhub status</code>、<code>omenhub preset gpu</code>、<code>omenhub fan 3000</code> 等，详见 <code>omenhub --help</code>；反馈问题时请附上 <code>sudo omenhubd --probe</code> 的输出。</p>
"""

ZH_TW = """
<h2>📦 OmenSuperHub for Linux v{version}</h2>
<p>移植自 Windows 版 OmenSuperHub v{upstream}，原專案：<a href="{url}">{url}</a>（GPL-3.0）。</p>
<p>Linux 版由兩部分組成：以 root 執行的後台服務 <code>omenhubd</code>（負責與 BIOS 通訊、風扇曲線、預設），
以及目前的托盤程式與命令列工具 <code>omenhub</code>。結束托盤程式不會停止風扇控制。</p>
<hr/>
<h2>一. 「風扇配置」選單說明</h2>
<p>(1) 支援安靜、降溫和自訂三種溫度-轉速設定，分別儲存在 <code>/var/lib/omenhub/silent.txt</code>、<code>cool.txt</code>、<code>custom.txt</code>，格式與 Windows 版相同，可直接複製使用；首次套用自訂時會先複製 cool.txt；</p>
<p>(2) 在「編輯曲線」中：左鍵新增節點、拖曳調整節點、右鍵刪除節點；「儲存」只寫入檔案，「儲存並套用」會寫入並立即切換到該曲線，「載入」可載入其他 txt 設定；程式自動進行線性插值，精度為1℃；</p>
<p>(3) 「即時」、「高」、「中」、「低」分別以從無到高的強度對溫度進行平滑處理。</p>
<h2>二. 「風扇控制」選單說明</h2>
<p>(1) 選擇「自動」則程式根據風扇配置和目前溫度（CPU 與 GPU 查表取較大值）設定風扇轉速；</p>
<p>(2) 「最大風扇」為BIOS控制，不一定是最大轉速；</p>
<p>(3) 選擇固定轉速時，若開啟高溫自動保護，CPU溫度高於（溫度上限-2）℃時將彈出通知並轉為降溫模式+自動風扇；</p>
<p>(4) 部分機型只有在BIOS設定中關閉「風扇始終啟動」後才能低於2000轉；</p>
<p>(5) 反轉除塵需要 BIOS 支援，轉速與時長可在設定檔的 platform 中調整。</p>
<h2>三. 「效能控制」選單說明</h2>
<p>(1) BIOS效能模式：OSH 固定使用釋放全部效能的模式，並透過 CPU 功率、PPab 等選項控制功耗；若希望由 power-profiles-daemon / TuneD 控制，請選擇「不接管」；</p>
<p>(2) 圖形模式等同於BIOS設定中的獨顯直連切換（需重啟）；</p>
<p>(3) PPab條件（Tpp）增加在CPU和GPU之間分配的總功率，為 NVIDIA Dynamic Boost 提供更大的功率容量；</p>
<p>(4) IccMax是CPU的最大電流限值（缺少 HP 平台資料庫，預設隱藏，可在設定中開啟）；AC Load Line透過調整CPU電壓補償負載下的電壓降；</p>
<p>(5) 顯示卡功耗=BTGP+CTGP+DB/Ppab，開啟CTGP和DB才能獲得最大GPU效能；Linux 下的 Dynamic Boost 由 nvidia-powerd 服務提供；</p>
<p>(6) 修改CPU功率會同時修改PL1與PL2；「不設定」表示不修改 BIOS 目前值；</p>
<p>(7) GPU頻率限制透過 nvidia-smi 鎖定最高頻率，不是超頻；GPU核心/顯存超頻透過 NVML 設定，需要較新的驅動；</p>
<p>(8) Windows 版的「DB解鎖」、「最大幀率」、「熱切換」、「重啟顯示卡」與單鍵RGB依賴 Windows 驅動介面，未移植；單鍵 RGB 鍵盤可使用 OpenRGB。</p>
<p>(9) CPU降壓：與 ThrottleStop 相同，透過 MSR 0x150 同時調整核心與快取的電壓偏移。僅支援 Intel，並且需要 BIOS 沒有鎖定降壓、核心沒有開啟 lockdown（開啟安全開機時通常會開啟）。「不設定」恢復本次開機時的原始值；降壓只在後台服務執行期間生效。如果套用降壓後系統沒有正常關機，下次開機會暫停自動降壓，確認穩定後重新選擇數值即可。降壓過度會導致當機，請從小幅度開始逐步加大，並做穩定性測試。</p>
<h2>四. 「硬體監控」選單說明</h2>
<p>(1) 可開啟或關閉對應的監控資訊。混合模式下獨顯休眠時顯示「節能」，此時不會讀取資料，避免把獨顯喚醒；獨顯閒置時也會暫停讀取，讓它有機會進入節能狀態。</p>
<h2>五. 「浮窗顯示」選單說明</h2>
<p>(1) 開啟後，螢幕左上角或右上角將顯示硬體監控資訊；左鍵點擊托盤圖示可快速切換；</p>
<p>(2) Wayland 下浮窗透過 XWayland 顯示（需要 XWayland），以便定位與置頂。</p>
<h2>六. 「Omen鍵」選單說明</h2>
<p>(1) Omen 鍵在 Linux 下由 hp-wmi 驅動回報為 XF86Launch2（KEY_PROG2）；「預設」時 OSH 不處理，可在桌面環境中自行綁定；</p>
<p>(2) 可選擇切換浮窗顯示、在候選預設之間循環切換、開啟應用程式（命令或 .desktop 檔案）、或模擬一個按鍵組合（透過 uinput，X11/Wayland 均可用）。</p>
<h2>七. 「其他設定」選單說明</h2>
<p>(1) 「自訂圖示」讀取 <code>~/.config/omenhub/custom.png</code>；「動態圖示」以目前CPU溫度（無CPU溫度時為GPU溫度）作為圖示；</p>
<p>(2) 「資料本地化」開啟後會把CPU、GPU溫度及風扇轉速寫入 <code>/run/omenhub/cpu_temp.txt</code>、<code>gpu_temp.txt</code>、<code>fan_rpm.txt</code>；</p>
<p>(3) 後台服務由 systemd 開機啟動；「開機自啟」控制托盤程式在登入後自動啟動；</p>
<p>(4) 「語言」可切換簡體中文、繁體中文和英文。</p>
<h2>八. 常見問題</h2>
<p>(1) 需要 acpi_call 核心模組才能使用全部功能；沒有時會降級到核心 hp-wmi 驅動的介面，僅支援部分功能；</p>
<p>(2) 一般使用者需要屬於 omenhub、wheel、sudo 或 admin 群組才能修改設定；</p>
<p>(3) 命令列：<code>omenhub status</code>、<code>omenhub preset gpu</code>、<code>omenhub fan 3000</code> 等，詳見 <code>omenhub --help</code>；回報問題時請附上 <code>sudo omenhubd --probe</code> 的輸出。</p>
"""

EN = """
<h2>📦 OmenSuperHub for Linux v{version}</h2>
<p>Ported from OmenSuperHub v{upstream} for Windows, original project: <a href="{url}">{url}</a> (GPL-3.0).</p>
<p>The Linux version has two parts: the root daemon <code>omenhubd</code> (BIOS communication, fan curves, presets)
and this tray app plus the <code>omenhub</code> command line tool. Quitting the tray does not stop fan control.</p>
<hr/>
<h2>1. Fan Config menu</h2>
<p>(1) Silent, Cool and Custom profiles are stored in <code>/var/lib/omenhub/silent.txt</code>, <code>cool.txt</code> and <code>custom.txt</code>, in the same format as the Windows version (you can copy your files). When Custom is first applied, cool.txt is copied;</p>
<p>(2) In “Edit Curves”: left-click to add a point, drag to adjust, right-click to delete. Save only writes the file; Save and Apply also switches to it. Load imports another txt profile. Curves use linear interpolation at 1°C precision;</p>
<p>(3) Realtime / High / Medium / Low apply increasing temperature smoothing.</p>
<h2>2. Fan Control menu</h2>
<p>(1) Auto: the speed follows the fan profile (the higher of the CPU and GPU lookups);</p>
<p>(2) Max Fan is controlled by the BIOS and is not necessarily the highest RPM;</p>
<p>(3) With a fixed speed and High-Temp Auto-Protect enabled, OSH switches to the Cool profile and Auto when the CPU exceeds (Tjmax − 2)°C;</p>
<p>(4) Some models only go below 2000 RPM after disabling “Fan Always On” in the BIOS;</p>
<p>(5) Clean Creek needs BIOS support; speed and duration can be changed in the platform section of the configuration.</p>
<h2>3. Performance menu</h2>
<p>(1) BIOS Thermal Mode: OSH pins the BIOS to its unleashed mode and limits power with CPU power, PPab, etc. Choose “Leave to system” to let power-profiles-daemon / TuneD manage it;</p>
<p>(2) Graphics mode is the BIOS MUX switch (reboot required);</p>
<p>(3) PPab (Tpp) raises the total power budget shared by CPU and GPU for NVIDIA Dynamic Boost;</p>
<p>(4) IccMax is the CPU current limit (hidden by default because HP's platform database is not available; enable it in the configuration). AC Load Line compensates voltage droop under load;</p>
<p>(5) GPU TDP = BTGP + CTGP + DB/Ppab. On Linux Dynamic Boost is provided by the nvidia-powerd service;</p>
<p>(6) CPU power sets both PL1 and PL2; “Not Set” leaves the BIOS value untouched;</p>
<p>(7) GPU clock limit locks the maximum clock with nvidia-smi (not overclocking); core/memory overclock uses NVML and needs a recent driver;</p>
<p>(8) DB unlock, max frame rate, hot switch, GPU restart and per-key RGB depend on Windows driver interfaces and were not ported; use OpenRGB for per-key keyboards.</p>
<p>(9) CPU Undervolt sets the core and cache voltage offset through MSR 0x150, the same interface ThrottleStop uses. It needs an Intel CPU, a BIOS that does not lock undervolting, and a kernel that is not in lockdown mode (usually enabled together with Secure Boot). “Not Set” restores the value the CPU had at boot, and the undervolt is only active while the daemon runs. If the system does not shut down cleanly while undervolted, the undervolt is paused at the next boot until you select a value again. Too much undervolt freezes the system: start small, increase gradually and run a stability test.</p>
<h2>4. HW Monitor menu</h2>
<p>(1) Enable or disable each item. In hybrid mode a sleeping dGPU is shown as “PoweredOff” and is not touched so it stays asleep; polling also pauses while the dGPU is idle.</p>
<h2>5. Overlay menu</h2>
<p>(1) Shows hardware info in the top-left or top-right corner; left-click the tray icon to toggle it;</p>
<p>(2) On Wayland the overlay runs through XWayland so it can be positioned and kept on top.</p>
<h2>6. Omen Key menu</h2>
<p>(1) On Linux the hp-wmi driver reports the Omen key as XF86Launch2 (KEY_PROG2). With “Default” OSH ignores it and you can bind it in your desktop settings;</p>
<p>(2) Other actions: toggle the overlay, cycle through preset candidates, open an app (command or .desktop file) or simulate a shortcut (through uinput, works on X11 and Wayland).</p>
<h2>7. Settings menu</h2>
<p>(1) The custom icon is read from <code>~/.config/omenhub/custom.png</code>; the dynamic icon shows the CPU (or GPU) temperature;</p>
<p>(2) Data Localize writes CPU/GPU temperature and fan RPM to <code>/run/omenhub/cpu_temp.txt</code>, <code>gpu_temp.txt</code> and <code>fan_rpm.txt</code>;</p>
<p>(3) The daemon is started by systemd at boot; Autostart controls whether the tray starts at login;</p>
<p>(4) Language switches between Simplified Chinese, Traditional Chinese and English.</p>
<h2>8. FAQ</h2>
<p>(1) Full functionality needs the acpi_call kernel module; without it OSH falls back to the hp-wmi driver interface with fewer features;</p>
<p>(2) Regular users must be in the omenhub, wheel, sudo or admin group to change settings;</p>
<p>(3) Command line: <code>omenhub status</code>, <code>omenhub preset gpu</code>, <code>omenhub fan 3000</code> … see <code>omenhub --help</code>. Please attach the output of <code>sudo omenhubd --probe</code> when reporting problems.</p>
"""


def help_html() -> str:
    body = {"zh-TW": ZH_TW, "en": EN}.get(current_language(), ZH_CN)
    return STYLE + body.format(version=__version__, upstream=UPSTREAM_VERSION, url=UPSTREAM)
