# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
"""omenhub 命令行：通过 omenhubd 查看状态、切换预设、调节风扇与功率等。"""

import argparse
import json
import sys
import time

from . import __version__, paths
from .config import PRESET_ORDER, normalize_preset
from .display import (kb_type_name, monitor_lines, offset_text, preset_name, thermal_mode_name, undervolt_lines,
                      validation_name)
from .fancurve import FAN_TABLES, FanCurveProfile, InvalidFanCurve
from .hardware import GFX_MODE_NAMES
from .i18n import detect_system_language, set_language, tr
from .ipc import Client, IpcError
from .undervolt import MIN_OFFSET

GFX_ALIASES = {"hybrid": 0, "discrete": 1, "optimus": 2, "advanced-optimus": 2, "uma": 3}
ON_OFF = {"on": True, "off": False, "true": True, "false": False, "1": True, "0": False,
          "yes": True, "no": False, "enable": True, "disable": False}


def _on_off(text: str) -> bool:
    try:
        return ON_OFF[text.lower()]
    except KeyError:
        raise argparse.ArgumentTypeError(f"expected on/off, got {text!r}") from None


def _optional_int(text: str):
    if text.lower() in ("none", "null", "unset", "default", "-"):
        return None
    if text.lower() == "max":
        return "max"
    return int(text)


def _color(text: str):
    text = text.lstrip("#")
    if "," in text:
        values = [int(v) for v in text.split(",")]
    elif len(text) == 6:
        values = [int(text[i:i + 2], 16) for i in (0, 2, 4)]
    else:
        raise argparse.ArgumentTypeError("color must be RRGGBB or R,G,B")
    if len(values) != 3 or not all(0 <= v <= 255 for v in values):
        raise argparse.ArgumentTypeError("color components must be 0-255")
    return values


def _fmt(value, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}"


def print_info(client: Client) -> None:
    info = client.request("get_info")
    dev, caps, backend = info["device"], info["caps"], info["backend"]
    gpu = caps.get("gpu") or {}
    rows = [
        (tr("sys_model_name"), dev.get("product_name")),
        (tr("sys_model_validation"), validation_name(info.get("validation"))),
        (tr("sys_board"), dev.get("board")),
        (tr("sys_bios"), dev.get("bios_version")),
        (tr("sys_backend"), backend.get("name") + (f" ({backend.get('method')})" if backend.get("method") else "")
         + (f" — {backend['error']}" if backend.get("error") else "")),
        (tr("sys_kernel"), dev.get("kernel")),
        (tr("sys_cpu"), dev.get("cpu_model")),
        (tr("sys_cpu_tjmax"), _fmt(caps.get("cpu_temp_limit"), "°C")),
        (tr("sys_gpu"), gpu.get("name") or "--"),
        (tr("sys_nvidia_tjmax"), _fmt(caps.get("gpu_temp_limit"), "°C")),
        (tr("sys_nvidia_power"), " / ".join(f"{v:.0f}W" for v in gpu["power_limits"])
         if gpu.get("power_limits") else "--W / --W"),
        (tr("sys_kb_type"), kb_type_name(caps.get("kb_type"))),
        (tr("sys_adapter_power"), _fmt(caps.get("adapter_power") if (caps.get("adapter_power") or 0) > 0 else None,
                                       "W")),
        (tr("sys_thermal_policy"), f"V{caps.get('thermal_policy', 0)}" if caps.get("bios") else "--"),
        (tr("sys_max_fan"), _fmt(caps.get("max_fan_rpm"), " RPM")),
    ]
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        print(f"{label.ljust(width)}  {value or '--'}")
    print(f"omenhubd {info['version']} (OmenSuperHub {info['upstream_version']})")


def print_status(client: Client, watch: bool) -> None:
    info = client.request("get_info")
    while True:
        state = client.request("get_state")
        status = client.request("get_status")
        name = preset_name(state["current_preset"], state["custom_names"])
        fan = state["fields"]["fan_control"]
        fan_text = {"auto": tr("fan_auto"), "max": tr("fan_max")}.get(fan, f"{fan} RPM")
        print(f"{tr('active_preset')}{name}  |  {tr('fan_control')}: {fan_text}"
              + (f"  |  {tr('clean_creek')}: {status['cleaning']}s" if status.get("cleaning") else ""))
        fields = {**state["fields"], "monitor_fan": bool(info["caps"].get("bios"))}
        for line in monitor_lines(status, fields, info["caps"]):
            print("  " + line)
        if not watch:
            return
        time.sleep(1)
        print()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omenhub", description="OmenSuperHub for Linux")
    parser.add_argument("--socket", default=paths.SOCKET_PATH)
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("status", help="live sensors and fan state")
    p.add_argument("-w", "--watch", action="store_true")
    sub.add_parser("info", help="device information and capabilities")
    sub.add_parser("probe", help="dump BIOS capabilities (for bug reports)")

    p = sub.add_parser("preset", help="show or apply a preset")
    p.add_argument("name", nargs="?", help="extreme / gpu / light / custom1 / custom2 / custom3")
    p = sub.add_parser("rename-preset", help="rename a custom preset")
    p.add_argument("preset")
    p.add_argument("name")

    p = sub.add_parser("fan", help="fan control: auto / max / <rpm>")
    p.add_argument("mode")
    p = sub.add_parser("fan-table", help="fan curve: silent / cool / custom")
    p.add_argument("table", choices=FAN_TABLES)
    p = sub.add_parser("fan-response", help="temperature smoothing")
    p.add_argument("speed", choices=("realtime", "high", "medium", "low"))
    p = sub.add_parser("fan-protect", help="high temperature auto protection")
    p.add_argument("state", type=_on_off)
    p = sub.add_parser("fan-clean", help="reverse dust removal")
    p.add_argument("action", nargs="?", default="start", choices=("start", "stop"))
    p = sub.add_parser("curve", help="show / export / import / reset fan curve files")
    p.add_argument("action", choices=("show", "export", "import", "reset"))
    p.add_argument("table", nargs="?", choices=FAN_TABLES)
    p.add_argument("file", nargs="?", help="file to import")
    p.add_argument("--apply", action="store_true", help="switch to the imported table")

    p = sub.add_parser("thermal", help="BIOS thermal mode")
    p.add_argument("mode", choices=("unleash", "performance", "balance", "default", "cool", "quiet", "eco", "system"))
    p = sub.add_parser("cpu-power", help="CPU PL1+PL2 in W, or none")
    p.add_argument("watts", type=_optional_int)
    p = sub.add_parser("tpp", help="PPab condition (Tpp) in W, or none")
    p.add_argument("watts", type=_optional_int)
    p = sub.add_parser("iccmax", help="IccMax in A, or none")
    p.add_argument("ampere", type=_optional_int)
    p = sub.add_parser("loadline", help="AC load line level, or none")
    p.add_argument("level", type=_optional_int)
    p = sub.add_parser("undervolt", help="CPU core/cache voltage offset (Intel), like ThrottleStop")
    p.add_argument("mv", nargs="?", help=f"offset in mV from {MIN_OFFSET} to 0, or none to restore the boot value")
    p.add_argument("--read", action="store_true", help="read the current offsets from the CPU")
    p = sub.add_parser("gfx", help="graphics (MUX) mode, needs reboot")
    p.add_argument("mode", nargs="?", choices=tuple(GFX_ALIASES))

    p = sub.add_parser("gpu", help="GPU power & clocks")
    gsub = p.add_subparsers(dest="gpu_command", metavar="ACTION")
    for name, text in (("tgp", "CTGP on / off"), ("ppab", "PPAB (Dynamic Boost) on / off")):
        q = gsub.add_parser(name, help=text)
        q.add_argument("state", type=_on_off)
    q = gsub.add_parser("dstate", help="GPU D-state: normal / low")
    q.add_argument("state", choices=("normal", "low"))
    q = gsub.add_parser("core-oc", help="core clock offset in MHz, or none")
    q.add_argument("mhz", type=_optional_int)
    q = gsub.add_parser("mem-oc", help="memory clock offset in MHz, or none")
    q.add_argument("mhz", type=_optional_int)
    q = gsub.add_parser("clock", help="max GPU clock in MHz, 0 = unlimited")
    q.add_argument("mhz", type=int)
    gsub.add_parser("processes", help="processes using the dGPU")
    q = gsub.add_parser("kill", help="terminate a process using the dGPU")
    q.add_argument("pid", type=int)
    q = gsub.add_parser("powerd", help="nvidia-powerd (Dynamic Boost)")
    q.add_argument("state", type=_on_off)

    p = sub.add_parser("light", help="keyboard / light bar lighting")
    lsub = p.add_subparsers(dest="light_command", metavar="ACTION")
    lsub.add_parser("show", help="current lighting state")
    q = lsub.add_parser("brightness", help="0-100, or 100 = off / 228 = on on some models")
    q.add_argument("value", type=int)
    q.add_argument("--lightbar", action="store_true")
    q = lsub.add_parser("color", help="static color, e.g. ff0000 or 255,0,0")
    q.add_argument("color", type=_color)
    q.add_argument("--zone", type=int, default=-1, help="1-4, default all zones")
    q.add_argument("--lightbar", action="store_true")
    q = lsub.add_parser("anim", help="animation effect (the basic protocol only has 2 and 4)")
    q.add_argument("effect", type=int, help="2 color cycle, 3 starlight, 4 breathing, 6 wave, 7 raindrop, ...")
    q.add_argument("--speed", type=int, choices=(0, 1, 2))
    q.add_argument("--direction", type=int, choices=(0, 1))
    q.add_argument("--theme", type=int, choices=(0, 1, 2, 3, 4))
    q.add_argument("--lightbar", action="store_true")
    q = lsub.add_parser("backlight", help="keyboard backlight on / off")
    q.add_argument("state", type=_on_off)
    q = lsub.add_parser("protocol", help="lighting interface: basic / dojo")
    q.add_argument("protocol", choices=("basic", "dojo"))
    q.add_argument("--lightbar", action="store_true")

    p = sub.add_parser("monitor", help="hardware monitoring switches")
    p.add_argument("target", choices=("cpu", "gpu", "fan"))
    p.add_argument("state", type=_on_off)
    p = sub.add_parser("omen-key", help="Omen key action")
    p.add_argument("action", choices=("default", "overlay", "preset", "app", "shortcut"))
    p.add_argument("--command", help="command or .desktop file for 'app' (saved for the current user's tray)")
    p.add_argument("--shortcut", help="key combination for 'shortcut', e.g. Ctrl+Alt+T")
    p.add_argument("--presets", help="comma separated preset candidates for 'preset'")
    p = sub.add_parser("data-localize", help="write sensor values to /run/omenhub/*.txt")
    p.add_argument("state", type=_on_off)
    p = sub.add_parser("set", help="set any field: omenhub set KEY JSON_VALUE")
    p.add_argument("key")
    p.add_argument("value")
    return parser


def run(args, client: Client):
    cmd = args.command

    def set_value(key, value):
        return client.request("set", key=key, value=value)

    if cmd == "status":
        if args.json:
            return client.request("get_status")
        print_status(client, args.watch)
    elif cmd == "info":
        if args.json:
            return client.request("get_info")
        print_info(client)
    elif cmd == "probe":
        return client.request("probe")
    elif cmd == "preset":
        if args.name:
            state = client.request("apply_preset", preset=normalize_preset(args.name))
        else:
            state = client.request("get_state")
        if args.json:
            return state
        for preset in PRESET_ORDER:
            if preset not in state["presets_available"]:
                continue
            mark = "*" if preset == state["current_preset"] else " "
            print(f"{mark} {preset:<8} {preset_name(preset, state['custom_names'])}")
    elif cmd == "rename-preset":
        client.request("rename_preset", preset=args.preset, name=args.name)
    elif cmd == "fan":
        mode = args.mode.lower()
        if mode not in ("auto", "max"):
            mode = int(mode.replace("rpm", ""))
        set_value("fan_control", mode)
    elif cmd == "fan-table":
        set_value("fan_table", args.table)
    elif cmd == "fan-response":
        set_value("temp_sensitivity", args.speed)
    elif cmd == "fan-protect":
        set_value("auto_fan_protect", args.state)
    elif cmd == "fan-clean":
        result = client.request("fan_clean", action=args.action)
        if args.action == "start":
            print(tr("clean_creek_progress", result["seconds"]))
    elif cmd == "curve":
        return run_curve(args, client)
    elif cmd == "thermal":
        set_value("thermal_mode", args.mode)
        print(thermal_mode_name(args.mode))
    elif cmd == "cpu-power":
        set_value("cpu_power", args.watts)
    elif cmd == "tpp":
        set_value("tpp", args.watts)
    elif cmd == "iccmax":
        set_value("iccmax", args.ampere)
    elif cmd == "loadline":
        set_value("ac_loadline", args.level)
    elif cmd == "undervolt":
        return run_undervolt(args, client, set_value)
    elif cmd == "gfx":
        if args.mode:
            result = client.request("gfx_mode_set", mode=GFX_ALIASES[args.mode])
            print(tr("gfx_switched_to", mode=result["name"]))
        else:
            mode = client.request("get_state").get("gfx_mode")
            print(GFX_MODE_NAMES.get(mode, tr("unknown")))
    elif cmd == "gpu":
        return run_gpu(args, client, set_value)
    elif cmd == "light":
        return run_light(args, client, set_value)
    elif cmd == "monitor":
        set_value(f"monitor_{args.target}", args.state)
    elif cmd == "omen-key":
        if args.command is not None:
            from .gui.settings import GuiSettings  # 不依赖 Qt
            gui = GuiSettings().load()
            gui.data["omen_key_app"] = args.command
            gui.data["omen_key_app_name"] = ""
            gui.save()
        if args.shortcut is not None:
            set_value("omen_key_shortcut", args.shortcut)
        if args.presets:
            set_value("omen_key_presets", [p.strip() for p in args.presets.split(",") if p.strip()])
        set_value("omen_key", args.action)
    elif cmd == "data-localize":
        set_value("data_localize", args.state)
    elif cmd == "set":
        try:
            value = json.loads(args.value)
        except ValueError:
            value = args.value
        return set_value(args.key, value)
    return None


def run_curve(args, client: Client):
    table = args.table or client.request("get_state")["fields"]["fan_table"]
    if args.action in ("show", "export"):
        result = client.request("fan_curve_get", table=table)
        profile = FanCurveProfile.from_dict(result["profile"])
        if args.json:
            return result
        if args.action == "export":
            sys.stdout.write(profile.dumps())
        else:
            for label, points in (("CPU", profile.cpu), ("GPU", profile.gpu)):
                print(f"{label}: " + "  ".join(f"{t}°C→{s}" for t, s in points))
    elif args.action == "import":
        if not args.file:
            raise SystemExit("curve import needs a file")
        try:
            profile = FanCurveProfile.load(args.file)
        except (OSError, InvalidFanCurve) as exc:
            raise SystemExit(f"{tr('fan_curve_load_failed')} {exc}") from exc
        client.request("fan_curve_set", table=table, profile=profile.to_dict(), apply=args.apply)
    elif args.action == "reset":
        client.request("fan_curve_reset", table=table)
    return None


def run_gpu(args, client: Client, set_value):
    action = args.gpu_command
    if action in ("tgp", "ppab", "dstate"):
        set_value(action, args.state)
    elif action == "core-oc":
        set_value("gpu_core_oc", args.mhz)
    elif action == "mem-oc":
        set_value("gpu_mem_oc", args.mhz)
    elif action == "clock":
        set_value("gpu_clock", args.mhz)
    elif action == "processes":
        processes = client.request("gpu_processes")
        if args.json:
            return processes
        if not processes:
            print(tr("gpu_apps_none"))
        for proc in processes:
            print(f"{proc['pid']:>7}  {proc['name']:<20} {proc['cmdline']}")
    elif action == "kill":
        client.request("kill_process", pid=args.pid)
    elif action == "powerd":
        print(client.request("nvidia_powerd", enable=args.state)["nvidia_powerd"])
    else:
        raise SystemExit("usage: omenhub gpu {tgp,ppab,dstate,core-oc,mem-oc,clock,processes,kill,powerd} ...")
    return None


def run_undervolt(args, client: Client, set_value):
    if args.mv is not None:
        text = args.mv.strip().lower()
        if text in ("none", "off", "unset", "default"):
            value = None
        else:
            try:
                value = int(text.replace("mv", ""))
            except ValueError:
                raise SystemExit(f"invalid offset {args.mv!r}: expected {MIN_OFFSET}..0 or none") from None
        set_value("undervolt", value)
    if args.read:
        client.request("undervolt_read")
    state = client.request("get_state")
    payload = state["undervolt"]
    if args.json:
        return payload
    configured = state["settings"].get("undervolt")
    if payload.get("supported"):
        print(tr("undervolt_setting", value=tr("not_set") if configured is None else offset_text(configured)))
    for line in undervolt_lines(payload):
        print(line)
    return None


def run_light(args, client: Client, set_value):
    action = args.light_command
    device = "lightbar" if getattr(args, "lightbar", False) else "keyboard"
    if action == "show" or action is None:
        result = client.request("lighting_get")
        if args.json:
            return result
        print(f"{tr('lighting_brightness_status')}: {result['brightness']}")
        print(f"{tr('lighting_animation_status')}: {result['animation']}")
        for i, color in enumerate(result.get("colors") or []):
            print(f"{tr('lighting_zone_tag')}{i + 1}: #{color[0]:02X}{color[1]:02X}{color[2]:02X}")
    elif action == "brightness":
        client.request("lighting_brightness", device=device, value=args.value)
    elif action == "color":
        zone = args.zone - 1 if args.zone > 0 else -1
        client.request("lighting_color", device=device, zone=zone, color=args.color)
    elif action == "anim":
        params = {k: getattr(args, k) for k in ("speed", "direction", "theme") if getattr(args, k) is not None}
        client.request("lighting_animation", device=device, effect=args.effect, **params)
    elif action == "backlight":
        client.request("lighting_backlight", on=args.state)
    elif action == "protocol":
        set_value("lb_protocol" if args.lightbar else "kb_protocol", args.protocol)
    return None


def main(argv=None) -> int:
    set_language(detect_system_language())
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args.command = "status"
        args.watch = False
    try:
        with Client(args.socket) as client:
            result = run(args, client)
    except IpcError as exc:
        if exc.code == "offline":
            print(f"{tr('daemon_offline')}: {exc}\n{tr('daemon_offline_tip')}", file=sys.stderr)
        elif exc.code == "denied":
            print(tr("daemon_denied"), file=sys.stderr)
        else:
            print(tr("operation_failed", msg=exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    if result is not None and (args.json or args.command in ("probe", "set")):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
