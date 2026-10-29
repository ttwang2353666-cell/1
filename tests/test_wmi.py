import os
import struct

import pytest

from omenhub import wmi


def test_build_bios_args_layout():
    args = wmi.build_bios_args(wmi.CMD_GAMING, 0x29, bytes([90, 90, 0xFF, 0xFF]))
    assert len(args) == 16 + 128
    sign, command, ctype, size = struct.unpack_from("<4sIII", args)
    assert (sign, command, ctype, size) == (b"SECU", 0x20008, 0x29, 4)
    assert args[16:20] == bytes([90, 90, 0xFF, 0xFF])
    assert args[20:] == bytes(124)


def test_build_bios_args_without_data_and_large_data():
    assert struct.unpack_from("<I", wmi.build_bios_args(1, 0x52, None), 12)[0] == 0
    big = wmi.build_bios_args(1, 2, bytes(200))
    assert len(big) == 16 + 200


@pytest.mark.parametrize("outsize,mid", [(0, 1), (4, 2), (5, 3), (128, 3), (129, 4), (1024, 4), (4096, 5)])
def test_method_id_for_outsize(outsize, mid):
    assert wmi.method_id_for_outsize(outsize) == mid


def test_parse_acpi_call_output_buffer():
    raw = wmi.parse_acpi_call_output("{0x53, 0x45, 0x43, 0x55, 0x00, 0x00, 0x00, 0x00, 0x2c, 0x01}\x00")
    result = wmi.parse_bios_return(raw, 4)
    assert result.ok and result.data == bytes([0x2C, 0x01, 0, 0])


def test_parse_bios_return_error_code():
    raw = wmi.parse_acpi_call_output("{0x53, 0x45, 0x43, 0x55, 0x04, 0x00, 0x00, 0x00}")
    result = wmi.parse_bios_return(raw, 128)
    assert result.return_code == wmi.RET_UNKNOWN_CMDTYPE and result.data == b""


@pytest.mark.parametrize("text", ["Error: AE_NOT_FOUND", "not called", "", "0x5"])
def test_parse_acpi_call_output_errors(text):
    with pytest.raises(wmi.WmiError):
        wmi.parse_acpi_call_output(text)


def test_acpi_call_backend_line(monkeypatch):
    backend = wmi.AcpiCallBackend(method_path="\\_SB_.WMID.WMAA")
    seen = []
    monkeypatch.setattr(backend, "raw_call", lambda line: seen.append(line) or
                        "{0x53, 0x45, 0x43, 0x55, 0x00, 0x00, 0x00, 0x00, 0x05, 0x06, 0x07}")
    result = backend.call(wmi.CMD_GAMING, 0x2D, bytes(4), 128)
    path, instance, mid, buffer = seen[0].split(" ")
    assert (path, instance, mid) == ("\\_SB_.WMID.WMAA", "0x0", "0x3")
    assert buffer.startswith("b534543550800020" + "02d000000" + "04000000")
    assert len(buffer) == 1 + (16 + 128) * 2
    assert result.data[:3] == bytes([5, 6, 7]) and len(result.data) == 128


def test_find_wmi_method_path(tmp_path):
    guid = wmi.HP_BIOS_GUID
    platform = tmp_path / "devices" / "platform" / "PNP0C14:00"
    device = platform / "wmi_bus" / "wmi_bus-PNP0C14:00" / guid
    device.mkdir(parents=True)
    (device / "guid").write_text(guid + "\n")
    (device / "object_id").write_text("AA\n")
    acpi = tmp_path / "acpi" / "PNP0C14:00"
    acpi.mkdir(parents=True)
    (acpi / "path").write_text("\\_SB_.WMID\n")
    os.symlink(acpi, platform / "firmware_node")
    bus = tmp_path / "bus"
    bus.mkdir()
    os.symlink(device, bus / guid)
    other = tmp_path / "other"
    other.mkdir()
    (other / "guid").write_text("95F24279-4D7B-4334-9387-ACCDC67EF61C\n")
    os.symlink(other, bus / "95F24279-4D7B-4334-9387-ACCDC67EF61C")
    assert wmi.find_wmi_method_path(sysfs=str(bus)) == "\\_SB_.WMID.WMAA"
    assert wmi.find_wmi_method_path(sysfs=str(tmp_path / "missing")) is None


def test_create_backend_mock():
    assert isinstance(wmi.create_backend("mock"), wmi.MockBios)


def test_acpi_call_verify_failure_falls_back(monkeypatch):
    monkeypatch.setattr(wmi.AcpiCallBackend, "ensure_module", staticmethod(lambda *a: True))
    monkeypatch.setattr(wmi.AcpiCallBackend, "raw_call", lambda self, line: "Error: AE_NOT_FOUND")
    monkeypatch.setattr(wmi.HpWmiSysfsBackend, "available", classmethod(lambda cls: False))
    with pytest.raises(wmi.WmiError, match="not usable"):
        wmi.create_backend("auto", "\\_SB.WMID.WMAA")
