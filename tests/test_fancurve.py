# SPDX-FileCopyrightText: 2026 OmenSuperHub for Linux contributors
# SPDX-License-Identifier: GPL-3.0-only
import pytest

from omenhub.fancurve import FanCurveProfile, InvalidFanCurve, create_default_profile, interpolate

POINTS = [(45, 1600), (95, 6400), (100, 7040)]


@pytest.mark.parametrize("temp,expected", [(20, 1600), (45, 1600), (70, 4000), (95, 6400), (97.5, 6720),
                                           (100, 7040), (120, 7040)])
def test_interpolate(temp, expected):
    assert interpolate(POINTS, temp) == expected


def test_interpolate_truncates_like_csharp():
    assert interpolate([(0, 0), (3, 100)], 1) == 33


def test_default_cool_profile_matches_osh_formula():
    profile = create_default_profile(False, max_rpm=6400, max_cpu_temp=100, max_gpu_temp=87)
    assert profile.cpu == [(45, 1600), (95, 6400), (100, 7040)]
    assert profile.gpu == [(32, 1600), (82, 6400), (87, 7040)]


def test_default_silent_profile_matches_osh_formula():
    profile = create_default_profile(True, max_rpm=6400, max_cpu_temp=100, max_gpu_temp=87)
    assert profile.cpu == [(30, 0), (60, 2133), (87, 4266), (100, 5760)]
    assert profile.gpu == [(17, 0), (47, 2133), (74, 4266), (87, 5760)]


def test_default_profile_fallback_without_platform_data():
    profile = create_default_profile(False)
    assert profile.cpu == [(50, 1600), (60, 2000), (85, 4000), (100, 5600)]
    assert profile.gpu == [(40, 1600), (50, 2000), (75, 4000), (90, 5600)]


def test_roundtrip_key_value_format(tmp_path):
    profile = FanCurveProfile(POINTS, [(40, 1000), (80, 5000)])
    path = tmp_path / "cool.txt"
    profile.save(str(path))
    text = path.read_text()
    assert text.splitlines()[0] == "Fan_Table_CPU_Temperature_List=45,95,100"
    assert FanCurveProfile.load(str(path)) == profile


def test_load_windows_file_with_bom_and_crlf():
    text = "﻿Fan_Table_CPU_Temperature_List=45,95\r\nFan_Table_CPU_Fan_Speed_List=1600,6400\r\n" \
           "fan_table_gpu_temperature_list=40,85\r\nFan_Table_GPU_Fan_Speed_List=1600,6400\r\n"
    profile = FanCurveProfile.loads(text.lstrip("﻿"))
    assert profile.gpu == [(40, 1600), (85, 6400)]


def test_load_legacy_csv():
    text = "CPU,Fan1,Fan2,GPU,Fan1,Fan2\n50,20,20,40,20,20\n80,40,40,70,45,45\n"
    profile = FanCurveProfile.loads(text)
    assert profile.cpu == [(50, 20), (80, 40)] and profile.gpu == [(40, 20), (70, 45)]


@pytest.mark.parametrize("text", ["", "Fan_Table_CPU_Temperature_List=1,2\n",
                                  "Fan_Table_CPU_Temperature_List=1,1\nFan_Table_CPU_Fan_Speed_List=1,2\n"
                                  "Fan_Table_GPU_Temperature_List=1,2\nFan_Table_GPU_Fan_Speed_List=1,2\n",
                                  "Fan_Table_CPU_Temperature_List=a,b\n"])
def test_invalid_files(text):
    with pytest.raises(InvalidFanCurve):
        FanCurveProfile.loads(text)


def test_from_dict_validation():
    with pytest.raises(InvalidFanCurve):
        FanCurveProfile.from_dict({"cpu": [[10, 100]], "gpu": [[10, 100], [20, 200]]})
    assert FanCurveProfile.from_dict({"cpu": [[20, 2], [10, 1]], "gpu": [[1, 1], [2, 2]]}).cpu == [(10, 1), (20, 2)]
