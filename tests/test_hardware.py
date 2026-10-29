import pytest

from omenhub import hardware as hw
from omenhub.wmi import CMD_GAMING, CMD_LIGHTING, CMD_READ, CMD_WRITE, MockBios


@pytest.fixture
def bios():
    return hw.OmenBios(MockBios(), "8BAD")


def last_call(b):
    return b.backend.calls[-1]


def test_design_data_parsing(bios):
    design = bios.parse_system_design_data()
    assert design["adapter_power"] == 280
    assert design["thermal_policy_version"] == 1
    assert design["sw_fan_control"] and design["turbo_mode_support"]
    assert design["loadline_levels"] == 4 and design["default_loadline"] == 1
    assert bios.get_supported_performance_modes() == ["eco", "balance", "performance", "unleash"]


def test_performance_mode_values_v1(bios):
    assert bios.performance_mode_value("unleash") == 0x31
    assert bios.performance_mode_value("balance") == 0x30
    assert bios.performance_mode_value("cool") == 0x50
    bios.set_performance_mode("unleash")
    assert last_call(bios) == (CMD_GAMING, 0x1A, bytes([0xFF, 0x31]), 0)


def test_performance_mode_values_forced_v0():
    bios = hw.OmenBios(MockBios(), "8746")
    assert bios.get_thermal_policy_version() == 0
    assert bios.performance_mode_value("unleash") == 0x01
    assert bios.performance_mode_value("eco") == 0x00
    assert bios.performance_mode_value("cool") == 0x02


def test_power_limit_commands(bios):
    bios.set_cpu_power_limit(90)
    assert last_call(bios) == (CMD_GAMING, 0x29, bytes([90, 90, 0xFF, 0xFF]), 0)
    bios.set_concurrent_tdp(120)
    assert last_call(bios) == (CMD_GAMING, 0x29, bytes([0xFF, 0xFF, 0xFF, 120]), 0)
    bios.set_cpu_power_limit4(150)
    assert last_call(bios) == (CMD_GAMING, 0x29, bytes([0xFF, 0xFF, 150, 0xFF]), 0)


def test_gpu_power_state_keeps_slowdown_temperature(bios):
    bios.set_gpu_power_state(True, False, 2)
    assert last_call(bios) == (CMD_GAMING, 0x22, bytes([1, 0, 2, 87]), 0)
    bios.set_gpu_power_state(False, True, 1, gps=0)
    assert last_call(bios) == (CMD_GAMING, 0x22, bytes([0, 1, 1, 0]), 0)


def test_fan_commands(bios):
    bios.set_fan_level(30, 32)
    assert last_call(bios) == (CMD_GAMING, 0x2E, bytes([30, 32]), 0)
    bios.set_fan_level(30, 40, fan3=True)
    assert last_call(bios)[2] == bytes([30, 40, 35])
    bios.set_fan_level(40, 40, clean=True)
    assert last_call(bios)[2] == bytes([168, 168])
    bios.set_max_fan_speed(True)
    assert last_call(bios) == (CMD_GAMING, 0x27, bytes([1]), 0)
    assert bios.get_fan_table_range() == (20, 64)
    assert bios.get_fan_count() == (True, False, False)


def test_fan_type_and_clean_creek(bios):
    types, caps = bios.get_fan_type()
    assert types[:3] == [1, 2, 0] and len(types) == 7
    assert caps[:2] == [True, True]
    assert bios.is_clean_creek_supported() and not bios.is_three_fan_supported()
    assert not bios.is_legacy_clean_creek_supported()


def test_gfx_mode(bios):
    assert bios.get_gfx_mode() == hw.GFX_HYBRID
    assert bios.get_supported_gfx_modes() == 0x0B
    assert bios.set_gfx_mode(hw.GFX_OPTIMUS)
    assert last_call(bios) == (CMD_WRITE, 82, bytes([2, 0, 0, 0]), 0)
    assert bios.get_gfx_mode() == hw.GFX_OPTIMUS
    assert bios.backend.calls[-1][0] == CMD_READ


def test_loadline_and_iccmax(bios):
    bios.set_loadline(3)
    command, ctype, data, outsize = last_call(bios)
    assert (ctype, data[:3], len(data)) == (0x37, bytes([0, 13, 3]), 128)
    assert bios.get_loadline() == 3
    bios.set_iccmax(250)
    assert last_call(bios)[2][:4] == bytes([0, 15, 250, 0])


def test_lighting_basic_and_dojo(bios):
    colors = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12)]
    assert bios.set_zone_static_color("keyboard", colors, 100, hw.IFACE_BASIC)
    command, ctype, data, _ = last_call(bios)
    assert (command, ctype) == (CMD_LIGHTING, 3)
    assert data[25:37] == bytes(range(1, 13))
    assert bios.get_zone_static_color() == colors
    bios.set_zone_static_color("lightbar", colors, 50, hw.IFACE_DOJO)
    command, ctype, data, _ = last_call(bios)
    assert (ctype, data[0], data[1], data[3], data[6]) == (11, 0, 0, 50, 4)
    assert data[7:19] == bytes(range(1, 13))


def test_lighting_animation_encoding(bios):
    bios.set_zone_animation("keyboard", 6, 2, 1, 3, None, 80, hw.IFACE_DOJO)
    data = last_call(bios)[2]
    assert (data[0], data[1], data[2], data[3]) == (1, 6, 0x40 | 0x08 | 0x02, 80)
    bios.set_zone_static_color("keyboard", [(9, 8, 7)] * 4, 100, hw.IFACE_BASIC)
    assert bios.set_zone_animation("keyboard", 4, 0, 0, 0, None, 100, hw.IFACE_BASIC)
    assert last_call(bios)[1:3] == (7, bytes([0, 1, 10, 100, 1, 9, 8, 7]))
    assert not bios.set_zone_animation("keyboard", 6, 0, 0, 0, None, 100, hw.IFACE_BASIC)


@pytest.mark.parametrize("name,vendor,board,fbyte,expected", [
    ("OMEN by HP Gaming Laptop 16-wf0xxx", "HP", "8BAD", "", "gaming"),
    ("OMEN by HP Laptop 15-ek0xxx", "HP", "8600", "", "old_omen"),
    ("Victus by HP Laptop 16-d0xxx", "HP", "88F8", "", "gaming"),
    ("HP Pavilion Gaming Laptop 15", "HP", "8743", "3X7K6b7Bfd", "gaming"),
    ("HP Pavilion Gaming Laptop 15", "HP", "8743", "3X6b7B", "unsupported_hp"),
    ("HP EliteBook 840", "HP", "8723", "", "unsupported_hp"),
    ("ThinkPad X1", "LENOVO", "20XW", "", "unsupported"),
])
def test_validate_product(name, vendor, board, fbyte, expected):
    assert hw.validate_product(name, vendor, board, fbyte) == expected


def test_send_returns_none_on_bios_error(bios):
    assert bios.send(0x99, bytes(4), 4) is None
