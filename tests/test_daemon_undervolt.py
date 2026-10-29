import asyncio

import pytest

from omenhub import daemon as daemon_mod
from omenhub import undervolt
from omenhub.config import Config
from omenhub.ipc import IpcError


def boot(tmp_path, monkeypatch, boot_id, body, device):
    """在模拟硬件上“开机”一次：同一个 device 代表跨重启保留的 CPU 状态。"""
    monkeypatch.setattr(undervolt, "boot_id", lambda: boot_id)

    async def main():
        d = daemon_mod.Daemon(str(tmp_path / "config.json"), str(tmp_path / "omenhub.sock"), mock=True)
        d.loop = asyncio.get_running_loop()
        await d.init_hardware()
        d.uv_box = undervolt.Mailbox(device)
        await d.restore_config()
        await d.apply_undervolt()
        try:
            return await body(d)
        finally:
            for task in list(d.tasks):
                task.cancel()
            d.bios_pool.shutdown(wait=True)
            d.io_pool.shutdown(wait=False)
    return asyncio.run(main())


def offsets(device):
    return {name: round(device.offset(name)) for name in undervolt.PLANES}


def test_msr_untouched_without_setting(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice({"core": -110, "cache": -110})

    async def body(d):
        assert device.requests == []
        assert d.state_payload()["undervolt"]["supported"] is True
    boot(tmp_path, monkeypatch, "A", body, device)


def test_apply_and_restore_boot_value(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice({"core": -110, "cache": -110})

    async def body(d):
        await d.set_setting("undervolt", -80)
        assert offsets(device) == {"core": -80, "cache": -80}
        payload = d.undervolt_payload()
        assert payload["applied"] and round(payload["baseline"]["core"]) == -110
        assert round(payload["current"]["cache"]) == -80
        saved = undervolt.load_state(d.uv_state_path)
        assert saved["applied"] is True and saved["clean"] is False
        assert Config(str(tmp_path / "config.json")).load().settings["undervolt"] == -80
        await d.set_setting("undervolt", None)
        assert offsets(device) == {"core": -110, "cache": -110}
        assert not d.uv_state["applied"] and d.uv_state["clean"]
        assert d.settings["undervolt"] is None
    boot(tmp_path, monkeypatch, "A", body, device)


def test_locked_bios_is_rejected_and_not_saved(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice(locked=True)

    async def body(d):
        with pytest.raises(IpcError) as err:
            await d.set_setting("undervolt", -60)
        assert err.value.code == "undervolt_rejected"
        assert d.settings["undervolt"] is None and d.undervolt_payload()["error"] == "rejected"
        assert not d.uv_state["applied"] and d.uv_state["clean"]
        assert offsets(device) == {"core": 0, "cache": 0}
    boot(tmp_path, monkeypatch, "A", body, device)


@pytest.mark.parametrize("value", [10, undervolt.MIN_OFFSET - 1, "x", -50.5, True])
def test_invalid_values(tmp_path, monkeypatch, value):
    async def body(d):
        with pytest.raises(IpcError) as err:
            await d.set_setting("undervolt", value)
        assert err.value.code == "invalid"
    boot(tmp_path, monkeypatch, "A", body, undervolt.FakeMsrDevice())


def test_unavailable(tmp_path, monkeypatch):
    async def body(d):
        d.caps.update(undervolt=False, undervolt_reason="lockdown")
        with pytest.raises(IpcError) as err:
            await d.set_setting("undervolt", -50)
        assert err.value.code == "unsupported"
    boot(tmp_path, monkeypatch, "A", body, undervolt.FakeMsrDevice())


def test_crash_guard(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice({"core": -110, "cache": -110})

    async def crash_while_applied(d):
        await d.set_setting("undervolt", -90)            # 随后死机：没有调用 shutdown()

    async def after_crash(d):
        assert d.uv_state["suspended"] and d.undervolt_payload()["suspended"]
        assert device.requests == []                     # 没有自动应用
        await d.shutdown()                               # 这次正常关机，但用户没有重新设置

    async def still_suspended(d):
        assert d.uv_state["suspended"] and device.requests == []
        await d.set_setting("undervolt", -90)            # 用户确认后重新应用
        assert not d.uv_state["suspended"] and offsets(device)["core"] == -90
        await d.shutdown()
        assert offsets(device) == {"core": -110, "cache": -110}

    async def resumes_normally(d):
        assert not d.uv_state["suspended"] and offsets(device) == {"core": -90, "cache": -90}

    boot(tmp_path, monkeypatch, "A", crash_while_applied, device)
    device.raw = {plane: undervolt.encode_offset(-110) if plane in (0, 2) else 0 for plane in range(5)}
    device.requests.clear()
    boot(tmp_path, monkeypatch, "B", after_crash, device)
    device.requests.clear()
    boot(tmp_path, monkeypatch, "C", still_suspended, device)
    boot(tmp_path, monkeypatch, "D", resumes_normally, device)


def test_service_restart_keeps_boot_value(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice({"core": -110, "cache": -110})

    async def first(d):
        await d.set_setting("undervolt", -70)

    async def restarted(d):
        # 同一次开机内服务被重启：此时 CPU 上是 -70，但开机原始值仍是 -110
        assert offsets(device) == {"core": -70, "cache": -70}
        assert round(d.uv_state["baseline"]["core"]) == -110 and not d.uv_state["suspended"]
        await d.set_setting("undervolt", None)
        assert offsets(device) == {"core": -110, "cache": -110}

    boot(tmp_path, monkeypatch, "A", first, device)
    boot(tmp_path, monkeypatch, "A", restarted, device)


def test_reapplied_after_resume(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice()

    async def body(d):
        await d.set_setting("undervolt", -60)
        device.raw = {plane: 0 for plane in range(5)}    # 睡眠后 CPU 清零了偏移
        await d.apply_undervolt()
        assert offsets(device) == {"core": -60, "cache": -60}
    boot(tmp_path, monkeypatch, "A", body, device)


def test_read_command_records_boot_value(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice({"core": -110, "cache": -100})

    async def body(d):
        payload = await d.cmd_undervolt_read({})
        assert round(payload["current"]["core"]) == -110 and round(payload["current"]["cache"]) == -100
        assert payload["baseline"] == payload["current"]
        assert all(undervolt.read_request(p) in device.requests for p in (0, 2))
    boot(tmp_path, monkeypatch, "A", body, device)


class LimitedDevice(undervolt.FakeMsrDevice):
    """接受 -120 mV 以内的偏移，更大的幅度像被锁定一样忽略。"""

    def write(self, msr, value):
        if (value >> 32) & 0xFF == 0x11 and undervolt.decode_offset(value) < -120:
            value = (value & ~0xFFE00000 & ~(0xFF << 32)) | (0x10 << 32)  # 变成读取请求
        super().write(msr, value)


def test_rejected_value_falls_back_to_previous_undervolt(tmp_path, monkeypatch):
    device = LimitedDevice({"core": -110, "cache": -110})

    async def body(d):
        await d.set_setting("undervolt", -80)
        with pytest.raises(IpcError) as err:
            await d.set_setting("undervolt", -150)
        assert err.value.code == "undervolt_rejected"
        assert offsets(device) == {"core": -80, "cache": -80}           # 回到之前已应用的值
        assert d.settings["undervolt"] == -80
        assert d.uv_state["applied"] and not d.uv_state["clean"]        # 仍在降压，异常关机保护继续有效
        assert {k: round(v) for k, v in d.uv_current.items()} == {"core": -80, "cache": -80}
        await d.set_setting("undervolt", None)
        assert offsets(device) == {"core": -110, "cache": -110}
    boot(tmp_path, monkeypatch, "A", body, device)


def test_automatic_apply_uses_latest_setting(tmp_path, monkeypatch):
    device = undervolt.FakeMsrDevice()

    async def body(d):
        d.settings["undervolt"] = -40
        await d.apply_undervolt()
        assert offsets(device) == {"core": -40, "cache": -40}
        d.uv_state["suspended"] = True
        d.settings["undervolt"] = -60
        await d.apply_undervolt()
        assert offsets(device) == {"core": -40, "cache": -40}           # 暂停期间不自动应用
    boot(tmp_path, monkeypatch, "A", body, device)
