import json
import os
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import pytest

HOST = os.getenv("MQTT_HOST", "host.docker.internal")
ASSET = "test_arm"


def _client():
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    try:
        c.connect(HOST, 1883, 10)
    except OSError as e:
        pytest.fail(f"broker not reachable at {HOST}:1883 ({e})")
    c.loop_start()
    return c


def _msg(seq, temp=42.0):
    return {"asset_id": ASSET,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "seq": seq, "joint_deg": [1, 2, 3, 4, 5, 6], "gripper_pct": 60.0, "mode": "auto",
            "motor_temp_c": temp, "motor_current_a": 2.0}


def _send_and_collect(payload, wait):
    got = []
    sub = _client()
    sub.on_message = lambda c, u, m: got.append(json.loads(m.payload))
    sub.subscribe(f"arm/{ASSET}/enriched")
    time.sleep(0.7)
    pub = _client()
    pub.publish(f"arm/{ASSET}/state", payload).wait_for_publish()
    deadline = time.time() + wait
    while time.time() < deadline and not got:
        time.sleep(0.2)
    time.sleep(0.5 if got else 0)
    for c in (sub, pub):
        c.loop_stop()
        c.disconnect()
    return got


def test_valid_message_flows_to_enriched_topic():
    seq = int(time.time())
    got = _send_and_collect(json.dumps(_msg(seq)), wait=6)
    assert got, "no enriched message received: is the processor running?"
    out = got[0]
    assert out["seq"] == seq
    assert "ai" in out and "agg" in out and "baseline_alarm" in out
    assert out["joint_deg"] == [1, 2, 3, 4, 5, 6]


def test_out_of_range_message_is_not_forwarded():
    got = _send_and_collect(json.dumps(_msg(int(time.time()), temp=500)), wait=3)
    assert got == []


def test_malformed_json_is_not_forwarded():
    assert _send_and_collect("hello", wait=3) == []