"""Processor: validate -> store in SQLite -> aggregate -> publish enriched state."""
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import paho.mqtt.client as mqtt

HOST = os.getenv("MQTT_HOST", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
DB_PATH = os.getenv("DB_PATH", "arm.db")

JOINT_LIMITS = [170, 120, 135, 180, 120, 180]
MODES = {"idle", "auto", "manual", "fault_stop"}
REQUIRED = ("asset_id", "timestamp", "seq", "joint_deg", "gripper_pct",
            "mode", "motor_temp_c", "motor_current_a")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  asset_id TEXT, ts TEXT, recv_ts TEXT, seq INTEGER,
  j1 REAL, j2 REAL, j3 REAL, j4 REAL, j5 REAL, j6 REAL,
  gripper REAL, mode TEXT, temp REAL, current REAL,
  ai_label TEXT, ai_conf REAL, baseline_alarm INTEGER);
CREATE INDEX IF NOT EXISTS idx_readings_recv ON readings(asset_id, recv_ts);
CREATE TABLE IF NOT EXISTS rejects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  recv_ts TEXT, reason TEXT, payload TEXT);
"""


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate(m):
    """Return None if the message is valid, otherwise a reason string."""
    if not isinstance(m, dict):
        return "payload is not a JSON object"
    for k in REQUIRED:
        if k not in m:
            return f"missing field: {k}"
    if not isinstance(m["asset_id"], str) or not m["asset_id"]:
        return "bad asset_id"
    try:
        datetime.fromisoformat(str(m["timestamp"]).replace("Z", "+00:00"))
    except ValueError:
        return "bad timestamp"
    if not isinstance(m["seq"], int):
        return "bad seq"
    j = m["joint_deg"]
    if not isinstance(j, list) or len(j) != 6 or not all(is_num(x) for x in j):
        return "joint_deg must be a list of 6 numbers"
    for i, x in enumerate(j):
        if abs(x) > JOINT_LIMITS[i]:
            return f"joint {i + 1} out of range: {x}"
    if not is_num(m["gripper_pct"]) or not 0 <= m["gripper_pct"] <= 100:
        return "gripper_pct out of range 0-100"
    if m["mode"] not in MODES:
        return f"unknown mode: {m['mode']}"
    if not is_num(m["motor_temp_c"]) or not 15 <= m["motor_temp_c"] <= 120:
        return "motor_temp_c out of range 15-120"
    if not is_num(m["motor_current_a"]) or not 0 <= m["motor_current_a"] <= 10:
        return "motor_current_a out of range 0-10"
    return None


def baseline_alarm(m):
    """Simple threshold alarm (a BASELINE only - not the AI model)."""
    return int(m["motor_temp_c"] >= 85 or m["motor_current_a"] >= 5.0)


def aggregates(db, asset, now):
    c60 = iso(now - timedelta(seconds=60))
    c1h = iso(now - timedelta(hours=1))
    mean60 = db.execute("SELECT AVG(temp) FROM readings WHERE asset_id=? AND recv_ts>=?",
                        (asset, c60)).fetchone()[0]
    max1h, n1h = db.execute("SELECT MAX(temp), COUNT(*) FROM readings WHERE asset_id=? AND recv_ts>=?",
                            (asset, c1h)).fetchone()
    alarms1h = db.execute("SELECT COUNT(*) FROM readings WHERE asset_id=? AND baseline_alarm=1 AND recv_ts>=?",
                          (asset, c1h)).fetchone()[0]
    return {
        "mean_temp_60s": round(mean60, 2) if mean60 is not None else None,
        "max_temp_1h": max1h,
        "alarms_1h": alarms1h,
        "samples_1h": n1h,
    }


def main():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    db.commit()

    def on_connect(client, userdata, flags, reason_code, properties):
        print("connected to broker:", reason_code, flush=True)
        client.subscribe("arm/+/state")

    def on_message(client, userdata, msg):
        recv = datetime.now(timezone.utc)
        raw = msg.payload.decode(errors="replace")
        try:
            m = json.loads(raw)
        except json.JSONDecodeError:
            m = None
            reason = "malformed JSON"
        else:
            reason = validate(m)

        if reason is not None:
            db.execute("INSERT INTO rejects(recv_ts, reason, payload) VALUES (?,?,?)",
                       (iso(recv), reason, raw[:500]))
            db.commit()
            print("REJECTED:", reason, flush=True)
            return

        ai = {"label": "n/a", "confidence": None, "model": "none"}   # AI is added in a later step
        alarm = baseline_alarm(m)
        j = m["joint_deg"]
        db.execute(
            "INSERT INTO readings(asset_id, ts, recv_ts, seq, j1,j2,j3,j4,j5,j6, gripper, mode, temp, current,"
            " ai_label, ai_conf, baseline_alarm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (m["asset_id"], m["timestamp"], iso(recv), m["seq"], *j, m["gripper_pct"], m["mode"],
             m["motor_temp_c"], m["motor_current_a"], ai["label"], ai["confidence"], alarm))
        db.commit()

        out = dict(m)
        out["ai"] = ai
        out["baseline_alarm"] = alarm
        out["agg"] = aggregates(db, m["asset_id"], recv)
        out["sent_ts"] = iso(datetime.now(timezone.utc))
        client.publish(f"arm/{m['asset_id']}/enriched", json.dumps(out))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    while True:
        try:
            client.connect(HOST, PORT, 60)
            break
        except OSError:
            print("broker not ready, retrying...", flush=True)
            time.sleep(2)
    client.loop_forever()


if __name__ == "__main__":
    main()