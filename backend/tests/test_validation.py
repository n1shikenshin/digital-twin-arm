import copy

import pytest

import processor as p

GOOD = {
    "asset_id": "arm_01", "timestamp": "2026-11-21T07:51:37.078Z", "seq": 1,
    "joint_deg": [0, 10, -20, 30, 40, -50], "gripper_pct": 50.0, "mode": "auto",
    "motor_temp_c": 40.0, "motor_current_a": 2.0,
}


def test_valid_message_accepted():
    assert p.validate(GOOD) is None


def test_non_object_rejected():
    assert p.validate("hello") is not None
    assert p.validate([1, 2, 3]) is not None


@pytest.mark.parametrize("field", list(GOOD))
def test_missing_field_rejected(field):
    m = copy.deepcopy(GOOD)
    del m[field]
    assert "missing field" in p.validate(m)


@pytest.mark.parametrize("field,value", [
    ("motor_temp_c", 500),
    ("motor_temp_c", 5),
    ("motor_current_a", 50),
    ("motor_current_a", -1),
    ("gripper_pct", 150),
    ("mode", "exploding"),
    ("joint_deg", [0, 0, 0]),
    ("joint_deg", [0, 0, 0, 0, 0, "x"]),
    ("joint_deg", [999, 0, 0, 0, 0, 0]),
    ("timestamp", "not-a-time"),
    ("seq", "one"),
    ("motor_temp_c", True),
])
def test_bad_values_rejected(field, value):
    m = copy.deepcopy(GOOD)
    m[field] = value
    assert p.validate(m) is not None


def test_baseline_alarm_thresholds():
    hot = dict(GOOD, motor_temp_c=90.0)
    amps = dict(GOOD, motor_current_a=6.0)
    assert p.baseline_alarm(GOOD) == 0
    assert p.baseline_alarm(hot) == 1
    assert p.baseline_alarm(amps) == 1