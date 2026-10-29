import asyncio
import os

import pytest

from omenhub import daemon as daemon_mod
from omenhub.config import Config
from omenhub.ipc import Client, IpcError


def run(tmp_path, body, restore=True):
    """在模拟硬件上创建守护进程并执行 body(d)。"""
    async def main():
        d = daemon_mod.Daemon(str(tmp_path / "config.json"), str(tmp_path / "omenhub.sock"), mock=True)
        d.loop = asyncio.get_running_loop()
        await d.init_hardware()
        if restore:
            await d.restore_config()
        try:
            return await body(d)
        finally:
            for task in list(d.tasks):
                task.cancel()
            d.bios_pool.shutdown(wait=True)
            d.io_pool.shutdown(wait=False)
    return asyncio.run(main())


def test_fresh_install_applies_extreme(tmp_path):
    async def body(d):
        bios = d.mock_bios
        assert d.current_preset == "extreme"
        assert bios.thermal == 0x31
        assert bios.power_limits[:2] == [160, 160]
        await asyncio.sleep(1.2)  # TPP 延迟 1 秒应用
        assert bios.power_limits[3] == 160
        assert os.path.exists(tmp_path / "cool.txt")
        assert Config(str(tmp_path / "config.json")).load().data["current_preset"] == "extreme"
    run(tmp_path, body)


def test_light_preset_hardware_state(tmp_path):
    async def body(d):
        await d.apply_preset("light")
        bios = d.mock_bios
        assert bios.power_limits[:2] == [33, 33]
        assert list(bios.gpu_modes) == [0, 0, 1, 87]
        assert d.fields["fan_table"] == "silent" and d.cpu_map[0] == (30, 0)
    run(tmp_path, body)


def test_custom_preset_is_independent_and_persistent(tmp_path):
    async def body(d):
        await d.apply_preset("gpu")
        await d.apply_preset("custom1")          # 首次使用继承当前设置
        assert d.fields["cpu_power"] == 55
        await d.set_field("cpu_power", 70)
        await d.set_field("monitor_fan", True)
        await d.apply_preset("extreme")
        assert d.fields["cpu_power"] == 160
        await d.apply_preset("custom1")
        assert d.fields["cpu_power"] == 70 and d.fields["monitor_fan"] is True
        assert d.mock_bios.power_limits[:2] == [70, 70]
    run(tmp_path, body)


def test_builtin_changes_survive_restart(tmp_path):
    async def first(d):
        await d.apply_preset("gpu")
        await d.set_field("cpu_power", 80)
        await d.set_field("fan_control", 3000)

    async def second(d):
        assert d.current_preset == "gpu"
        assert d.fields["cpu_power"] == 80 and d.fields["fan_control"] == 3000
        assert d.mock_bios.power_limits[:2] == [80, 80]
        assert d.mock_bios.fan_set[:2] == [30, 30]
    run(tmp_path, first)
    run(tmp_path, second)


@pytest.mark.parametrize("key,value", [("cpu_power", 5), ("cpu_power", 300), ("fan_table", "turbo"),
                                       ("fan_control", -1), ("tgp", "yes"), ("dstate", "medium"),
                                       ("gpu_clock", 100), ("refresh_rate", "fast")])
def test_set_field_validation(tmp_path, key, value):
    async def body(d):
        with pytest.raises(IpcError) as err:
            await d.set_field(key, value)
        assert err.value.code == "invalid"
    run(tmp_path, body)


def test_cannot_disable_all_monitoring_in_auto(tmp_path):
    async def body(d):
        await d.set_field("monitor_gpu", False)
        with pytest.raises(IpcError) as err:
            await d.set_field("monitor_cpu", False)
        assert err.value.code == "auto_fan_needs_monitor"
        await d.set_field("fan_control", 2500)
        await d.set_field("monitor_cpu", False)
        assert d.fields["monitor_cpu"] is False
    run(tmp_path, body)


def test_fan_tick_follows_curve(tmp_path):
    async def body(d):
        d.cpu_map, d.gpu_map = [(40, 2000), (80, 6000)], [(40, 1000), (80, 3000)]
        d.cpu["ready"], d.smoothed_cpu = True, 60.0
        d.gpu["fan_ready"], d.smoothed_gpu = True, 70.0
        await d.fan_tick()
        assert d.mock_bios.fan_set[:2] == [40, 40]
        d.smoothed_cpu = 60.9                   # 变化不足 100 RPM，不重复下发
        calls = len(d.mock_bios.calls)
        await d.fan_tick()
        assert len(d.mock_bios.calls) == calls
        d.fields["fan_control"] = "max"
        d.smoothed_cpu = 80.0
        await d.fan_tick()
        assert len(d.mock_bios.calls) == calls
    run(tmp_path, body)


def test_auto_fan_protect(tmp_path):
    async def body(d):
        await d.set_field("fan_table", "silent")
        await d.set_field("fan_control", 2000)
        d.cpu["ready"], d.smoothed_cpu = True, 99.0
        d.fan_levels = [20, 20, 0]
        await d.check_auto_fan_protect()
        assert d.fields["fan_control"] == "auto" and d.fields["fan_table"] == "cool"
        assert Config(str(tmp_path / "config.json")).load().data["builtin"]["fan_control"] == "auto"
    run(tmp_path, body)


def test_omen_key_preset_cycle(tmp_path):
    async def body(d):
        await d.set_setting("omen_key_presets", ["extreme", "PresetCustom2"])
        assert d.preset_candidates() == ["extreme", "custom2"]
        await d.switch_preset_by_omen_key()
        assert d.current_preset == "custom2"
        await d.switch_preset_by_omen_key()
        assert d.current_preset == "extreme"
        with pytest.raises(IpcError):
            await d.set_setting("omen_key_presets", [])
    run(tmp_path, body)


def test_clean_creek_start_stop(tmp_path):
    async def body(d):
        seconds = await d.start_cleaning()
        assert seconds == 30 and d.cleaning_until
        level = d.caps["clean_creek_level"]      # min(风扇表最高 64, 50)
        assert level == 50 and d.mock_bios.fan_set[:2] == [level + 128] * 2
        await d.fan_tick()                       # 除尘期间自动控制暂停
        assert d.mock_bios.fan_set[:2] == [level + 128] * 2
        await d.stop_cleaning()
        assert not d.cleaning_until and d.mock_bios.max_fan is False
        d.cpu["ready"], d.smoothed_cpu = True, 50.0
        await d.fan_tick()
        assert d.mock_bios.fan_set[0] < 128
    run(tmp_path, body)


def test_fan_curve_files(tmp_path):
    (tmp_path / "custom.txt").write_text("CPU,Fan1,Fan2,GPU,Fan1,Fan2\n50,2000,2000,40,2000,2000\n"
                                         "90,6000,6000,80,6000,6000\n")

    async def body(d):
        result = await d.cmd_fan_curve_get({"table": "custom"})
        assert result["profile"]["cpu"] == [[50, 2000], [90, 6000]]
        await d.set_field("fan_table", "custom")     # 旧 CSV 格式会被转换
        assert (tmp_path / "custom.txt").read_text().startswith("Fan_Table_CPU_Temperature_List=50,90")
        profile = {"cpu": [[30, 1000], [90, 5000]], "gpu": [[30, 1000], [80, 5000]]}
        await d.cmd_fan_curve_set({"table": "silent", "profile": profile, "apply": True})
        assert d.fields["fan_table"] == "silent" and d.cpu_map == [(30, 1000), (90, 5000)]
        with pytest.raises(IpcError):
            await d.cmd_fan_curve_set({"table": "silent", "profile": {"cpu": [[1, 1]], "gpu": []}})
    run(tmp_path, body)


def test_thermal_mode_and_lighting_commands(tmp_path):
    async def body(d):
        await d.set_setting("thermal_mode", "cool")
        assert d.mock_bios.thermal == 0x50
        await d.cmd_lighting_color({"zone": 2, "color": [1, 2, 3]})
        state = await d.cmd_lighting_get({})
        assert state["colors"][2] == [1, 2, 3] and state["colors"][0] == [255, 0, 0]
        await d.set_setting("kb_protocol", "dojo")
        anim = await d.cmd_lighting_animation({"effect": 6, "speed": 2})
        assert anim["effect"] == 6 and d.mock_bios.animation == 6
        with pytest.raises(IpcError):
            await d.cmd_gfx_mode_set({"mode": 1})    # 模拟机型不支持独显直连
        assert (await d.cmd_gfx_mode_set({"mode": 3}))["name"] == "UMA"
    run(tmp_path, body)


def test_ipc_roundtrip_and_authorization(tmp_path, monkeypatch):
    async def body(d):
        server = await d.start_server()
        loop = asyncio.get_running_loop()

        def client_work():
            with Client(d.socket_path) as client:
                assert client.request("ping")["version"]
                state = client.request("set", key="fan_control", value=2500)
                assert state["fields"]["fan_control"] == 2500
                try:
                    client.request("no_such_command")
                except IpcError as exc:
                    assert exc.code == "unknown_command"
                monkeypatch.setattr(daemon_mod, "is_authorized", lambda uid, groups: False)
            with Client(d.socket_path) as client:
                assert client.request("get_status")["preset"] == "extreme"
                try:
                    client.request("set", key="fan_control", value="max")
                    raise AssertionError("expected denial")
                except IpcError as exc:
                    assert exc.code == "denied"
            sub = Client(d.socket_path).connect()
            snapshot = sub.subscribe()
            assert snapshot["state"]["fields"]["fan_control"] == 2500
            return sub

        sub = await loop.run_in_executor(None, client_work)
        d.broadcast("notify", {"kind": "test"})
        message = await loop.run_in_executor(None, lambda: sub.read_message(timeout=5))
        assert message == {"event": "notify", "data": {"kind": "test"}}
        sub.close()
        await asyncio.sleep(0.2)  # 让连接处理协程先结束
        server.close()
        await server.wait_closed()
    run(tmp_path, body)


def test_second_daemon_refuses_same_socket(tmp_path):
    async def body(d):
        server = await d.start_server()
        other = daemon_mod.Daemon(str(tmp_path / "config.json"), d.socket_path, mock=True)
        with pytest.raises(RuntimeError, match="already running"):
            await other.start_server()
        await asyncio.sleep(0.1)
        server.close()
        await server.wait_closed()
    run(tmp_path, body)


def test_shortcut_validation(tmp_path):
    async def body(d):
        await d.set_setting("omen_key_shortcut", "Ctrl+Alt+T")
        with pytest.raises(IpcError):
            await d.set_setting("omen_key_shortcut", "Ctrl+Wat")
        with pytest.raises(IpcError):
            await d.set_setting("omen_key", "launch_missiles")
    run(tmp_path, body)
