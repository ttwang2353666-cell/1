# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
import pytest

from omenhub import undervolt as uv


@pytest.mark.parametrize("mv,field", [(-110, 0xF1E00000), (-10, 0xFEC00000), (-100, 0xF3400000),
                                      (-125, 0xF0000000), (0, 0)])
def test_encode_offset_known_values(mv, field):
    # f1e00000 / fec00000 是在暗影精灵 9 上实际读到的值
    assert uv.encode_offset(mv) == field


def test_decode_offset():
    assert round(uv.decode_offset(0xF1E00000), 2) == -110.35
    assert round(uv.decode_offset(0xFEC00000), 2) == -9.77
    assert uv.decode_offset(0) == 0
    assert round(uv.decode_offset(uv.encode_offset(50))) == 50


def test_round_trip_whole_range():
    for mv in range(uv.MIN_OFFSET, uv.MAX_OFFSET + 1):
        decoded = uv.decode_offset(uv.encode_offset(mv))
        assert round(decoded) == mv and uv.same_offset(decoded, mv)


def test_requests():
    assert uv.read_request(0) == 0x8000001000000000
    assert uv.read_request(2) == 0x8000021000000000
    assert uv.write_request(0, -10) == 0x80000011FEC00000
    assert uv.write_request(0, -110) == 0x80000011F1E00000
    assert uv.write_request(2, -100) == 0x80000211F3400000
    assert uv.write_request(0, 0) == 0x8000001100000000


def test_valid_offset():
    assert uv.valid_offset(-80) and uv.valid_offset(0) and uv.valid_offset(uv.MIN_OFFSET)
    assert not uv.valid_offset(5) and not uv.valid_offset(uv.MIN_OFFSET - 1)
    assert not uv.valid_offset(-50.5) and not uv.valid_offset(True) and not uv.valid_offset(None)


def test_mailbox_read_write():
    device = uv.FakeMsrDevice({"core": -110, "cache": -110})
    box = uv.Mailbox(device)
    assert {k: round(v) for k, v in box.read_all().items()} == {"core": -110, "cache": -110}
    actual = box.write_all({"core": -80, "cache": -80})
    assert {k: round(v) for k, v in actual.items()} == {"core": -80, "cache": -80}
    assert round(device.offset("core")) == -80 and round(device.offset("cache")) == -80
    assert uv.write_request(0, -80) in device.requests and uv.write_request(2, -80) in device.requests


def test_locked_bios_is_detected_by_read_back():
    device = uv.FakeMsrDevice(locked=True)
    with pytest.raises(uv.UndervoltError) as err:
        uv.Mailbox(device).write_offset(0, -50)
    assert err.value.code == "rejected"
    assert device.offset("core") == 0


class StuckDevice:
    def __init__(self, response):
        self.value = response

    def write(self, msr, value):
        pass

    def read(self, msr):
        return self.value


def test_mailbox_errors():
    with pytest.raises(uv.UndervoltError) as err:
        uv.Mailbox(StuckDevice(1 << 63)).read_offset(0)
    assert err.value.code == "msr_error"
    with pytest.raises(uv.UndervoltError) as err:
        uv.Mailbox(StuckDevice(0x01 << 32)).write_offset(0, -20)
    assert err.value.code == "rejected"

    class Broken(StuckDevice):
        def write(self, msr, value):
            raise OSError(5, "Input/output error")

    with pytest.raises(uv.UndervoltError) as err:
        uv.Mailbox(Broken(0)).read_offset(0)
    assert err.value.code == "msr_error"


def test_msr_device_uses_msr_number_as_offset(tmp_path):
    path = tmp_path / "msr"
    path.write_bytes(bytes(0x200))
    device = uv.MsrDevice()
    device.path = str(path)
    device.write(0x150, 0x80000011FEC00000)
    assert path.read_bytes()[0x150:0x158] == bytes.fromhex("0000c0fe11000080")
    assert device.read(0x150) == 0x80000011FEC00000


@pytest.mark.parametrize("text,mode", [("[none] integrity confidentiality", "none"),
                                       ("none [integrity] confidentiality", "integrity"),
                                       ("none integrity [confidentiality]", "confidentiality")])
def test_lockdown_mode(tmp_path, monkeypatch, text, mode):
    path = tmp_path / "lockdown"
    path.write_text(text + "\n")
    monkeypatch.setattr(uv, "LOCKDOWN_PATH", str(path))
    assert uv.lockdown_mode() == mode


def test_unsupported_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(uv, "LOCKDOWN_PATH", str(tmp_path / "missing"))
    assert uv.lockdown_mode() == "none"
    assert uv.unsupported_reason("GenuineIntel") is None
    assert uv.unsupported_reason("AuthenticAMD") == "not_intel"
    (tmp_path / "lockdown").write_text("none [integrity] confidentiality")
    monkeypatch.setattr(uv, "LOCKDOWN_PATH", str(tmp_path / "lockdown"))
    assert uv.unsupported_reason("GenuineIntel") == "lockdown"


def test_state_file(tmp_path):
    path = str(tmp_path / "state" / "undervolt.json")
    assert uv.load_state(path) is None
    uv.save_state(path, {"boot_id": "a", "applied": True})
    assert uv.load_state(path) == {"boot_id": "a", "applied": True}
    (tmp_path / "state" / "undervolt.json").write_text("{broken")
    assert uv.load_state(path) is None
