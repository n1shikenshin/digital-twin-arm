"""Robot arm simulator: publishes one state message per second over MQTT."""
import json
import math
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

HOST = os.getenv("MQTT_HOST", "localhost")
PORT = int(os.getenv("MQTT_PORT", "1883"))
ASSET = os.getenv("ASSET_ID", "arm_01")
RATE_HZ = float(os.getenv("RATE_HZ", "1"))
TOPIC_STATE = f"arm/{ASSET}/state"
TOPIC_CMD = f"arm/{ASSET}/cmd"

AMBIENT_C = 25.0
AMPL = [60, 40, 50, 80, 40, 90]      # deg, per joint
PERIOD = [20, 16, 14, 24, 12, 18]    # s per cycle, per joint

fault = "none"   # none | overtemp | overcurrent


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def on_connect(client, userdata, flags, reason_code, properties):
    print("connected to broker:", reason_code, flush=True)
    client.subscribe(TOPIC_CMD)


def on_message(client, userdata, msg):
    global fault
    text = msg.payload.decode(errors="ignore").strip()
    try:
        obj = json.loads(text)
        text = obj.get("fault", "none") if isinstance(obj, dict) else str(obj)
    except json.JSONDecodeError:
        pass
    text = text.strip().strip('"')
    if text == "normal":
        text = "none"
    if text in ("none", "overtemp", "overcurrent"):
        fault = text
        print("fault set to:", fault, flush=True)


def main():
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
    client.loop_start()

    dt = 1.0 / RATE_HZ
    phase = 0.0            # motion time, frozen while the arm is stopped
    temp = 38.0
    prev = [0.0] * 6
    seq = 0
    stopped = False

    while True:
        if temp >= 100.0:
            stopped = True
        elif stopped and temp < 70.0 and fault == "none":
            stopped = False

        if stopped:
            angles = prev
            speed = 0.0
            current = 0.3 + random.gauss(0, 0.03)
            mode = "fault_stop"
        else:
            phase += dt
            angles = [round(AMPL[i] * math.sin(2 * math.pi * phase / PERIOD[i]), 2) for i in range(6)]
            speed = sum(abs(angles[i] - prev[i]) for i in range(6)) / dt   # total deg/s
            current = 1.0 + 0.02 * speed + random.gauss(0, 0.08)
            if fault == "overcurrent":
                current += random.uniform(3.5, 5.0)
            mode = "auto"
        prev = angles

        # thermal model
        if fault == "overtemp" and not stopped:
            temp += random.uniform(0.7, 1.1)                      # cooling lost: steady rise
        else:
            target = AMBIENT_C + 6.0 * current
            temp += (target - temp) * 0.08 + random.gauss(0, 0.1)
        temp = max(AMBIENT_C, min(temp, 115.0))

        gripper = 50 + 40 * math.sin(2 * math.pi * phase / 10) if not stopped else 50.0

        seq += 1
        msg = {
            "asset_id": ASSET,
            "timestamp": now_iso(),
            "seq": seq,
            "joint_deg": angles,
            "gripper_pct": round(gripper, 1),
            "mode": mode,
            "motor_temp_c": round(temp, 2),
            "motor_current_a": round(max(current, 0.0), 3),
        }
        client.publish(TOPIC_STATE, json.dumps(msg))
        time.sleep(dt)


if __name__ == "__main__":
    main()