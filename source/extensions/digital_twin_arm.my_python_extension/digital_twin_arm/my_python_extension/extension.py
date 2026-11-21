import json
import queue
import threading
import time
import traceback
import urllib.request
from collections import deque
from datetime import datetime, timezone

import omni.ext
import omni.kit.app
import omni.ui as ui
import omni.usd

from . import arm_model

BROKER_HOST = "localhost"
BROKER_PORT = 1883
TOPIC = "arm/arm_01/enriched"
API_URL = "http://localhost:8080"
STALE_AFTER_S = 3.0
TREND_POINTS = 60
HISTORY_LIMIT = 180   # about 3 minutes at 1 Hz
SMOOTH_S = 1.0

# omni.ui colors are 0xAABBGGRR
GREEN, RED, ORANGE, GREY, WHITE = 0xFF50C850, 0xFF4040E0, 0xFF00A0FF, 0xFFAAAAAA, 0xFFEEEEEE


def _delay_s(ts):
    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - t).total_seconds()


class MyExtension(omni.ext.IExt):
    def on_startup(self, _ext_id):
        print("[RobotArm] extension started")
        self._handles = None
        self._q = queue.Queue()
        self._last_rx = None
        self._mqtt_connected = False
        self._bad_msgs = 0
        self._delays = []
        self._cache = {}
        self._labels = {}
        self._mqtt = None
        self._update_sub = None
        self._trend = deque([30.0] * TREND_POINTS, maxlen=TREND_POINTS)
        self._history = []
        self._hist_result = None      # written by a worker thread, read in the update loop
        self._replay_idx = None
        self._replay_next = 0.0
        self._anim = None
        self._shown_joints = [0.0] * 6
        self._shown_grip = 50.0
        self._shown_temp = 30.0

        self._build_ui()
        self._start_mqtt()
        stream = omni.kit.app.get_app().get_update_event_stream()
        self._update_sub = stream.create_subscription_to_pop(self._on_update, name="robot_arm_update")

    def on_shutdown(self):
        self._update_sub = None
        if self._mqtt:
            try:
                self._mqtt.loop_stop()
                self._mqtt.disconnect()
            except Exception:
                pass
            self._mqtt = None
        if self._window:
            self._window.destroy()
            self._window = None

    # ---------------- UI ----------------
    def _build_ui(self):
        self._window = ui.Window("Robot Arm Digital Twin", width=460, height=640)
        with self._window.frame:
            with ui.VStack(spacing=4):
                ui.Button("Build / Rebuild Arm", height=28, clicked_fn=self._build_scene)
                for key, title in [
                    ("link", "Connection"), ("joints", "Joints (deg)"), ("grip", "Gripper"),
                    ("mode", "Mode"), ("temp", "Motor temp"), ("cur", "Motor current"),
                    ("ai", "AI status"), ("alarm", "Threshold alarm"),
                    ("agg", "Aggregates"), ("delay", "Display delay"),
                ]:
                    with ui.HStack(height=22):
                        ui.Label(title, width=110)
                        self._labels[key] = ui.Label("-", word_wrap=True)

                ui.Label("Live motor temperature trend (last 60 s)", height=18)
                self._trend_plot = ui.Plot(ui.Type.LINE, 20.0, 120.0, *list(self._trend), height=70)

                ui.Label("History from database (temperature)", height=18)
                self._hist_plot = ui.Plot(ui.Type.LINE, 20.0, 120.0, 30.0, 30.0, height=70)
                with ui.HStack(height=28):
                    ui.Button("Load history (3 min)", clicked_fn=self._load_history)
                    ui.Button("Replay", clicked_fn=self._start_replay)
                    ui.Button("Back to live", clicked_fn=self._stop_replay)
                with ui.HStack(height=22):
                    ui.Label("History", width=110)
                    self._labels["hist"] = ui.Label("not loaded", word_wrap=True)

    def _set(self, key, text, color=WHITE):
        if self._cache.get(key) == (text, color):
            return
        self._cache[key] = (text, color)
        lbl = self._labels[key]
        lbl.text = text
        lbl.style = {"color": color}

    def _plot(self, plot, values):
        try:
            vals = list(values)
            if len(vals) < 2:
                vals = vals + [vals[-1] if vals else 30.0] * (2 - len(vals))
            lo, hi = min(vals) - 2.0, max(vals) + 2.0
            plot.scale_min = lo
            plot.scale_max = hi
            plot.set_data(*vals)
        except Exception as e:
            print("[RobotArm] plot update failed:", e)

    # ---------------- MQTT (background thread) ----------------
    def _start_mqtt(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            print("[RobotArm] paho-mqtt is not installed (check extension.toml)")
            self._set("link", "paho-mqtt missing", RED)
            return
        self._mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._mqtt.on_connect = self._on_connect
        self._mqtt.on_disconnect = self._on_disconnect
        self._mqtt.on_message = self._on_message
        self._mqtt.connect_async(BROKER_HOST, BROKER_PORT, 30)
        self._mqtt.loop_start()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        self._mqtt_connected = True
        client.subscribe(TOPIC)
        print("[RobotArm] connected to broker")

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._mqtt_connected = False
        print("[RobotArm] disconnected from broker")

    def _on_message(self, client, userdata, msg):
        # MQTT thread: parse and queue only. No USD or UI work here.
        try:
            data = json.loads(msg.payload)
            if not isinstance(data.get("joint_deg"), list) or len(data["joint_deg"]) != 6:
                raise ValueError("bad joint_deg")
            self._q.put(data)
        except Exception:
            self._bad_msgs += 1

    # ---------------- History (HTTP call runs in a worker thread) ----------------
    def _load_history(self):
        self._set("hist", "loading...", GREY)

        def work():
            try:
                with urllib.request.urlopen(f"{API_URL}/history?limit={HISTORY_LIMIT}", timeout=3) as r:
                    self._hist_result = ("ok", json.loads(r.read()))
            except Exception as e:
                self._hist_result = ("err", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _finish_load(self, res):
        kind, payload = res
        if kind != "ok":
            self._set("hist", f"load failed: {payload}", RED)
            return
        self._history = payload
        if not payload:
            self._set("hist", "database has no rows yet", ORANGE)
            return
        self._plot(self._hist_plot, [r["temp"] for r in payload])
        self._set("hist", f"{len(payload)} rows  {payload[0]['ts'][11:19]} to {payload[-1]['ts'][11:19]} UTC", GREEN)

    def _start_replay(self):
        if not self._history:
            self._set("hist", "press 'Load history' first", ORANGE)
            return
        self._replay_idx = 0
        self._replay_next = 0.0

    def _stop_replay(self):
        self._replay_idx = None
        self._last_rx = None   # status shows waiting until the next live message
        self._set("link", "back to live, waiting for data", GREY)

    @staticmethod
    def _row_to_state(r):
        return {"joint_deg": r["joints"], "gripper_pct": r["gripper"], "mode": r["mode"],
                "motor_temp_c": r["temp"], "motor_current_a": r["current"],
                "ai": {"label": r["ai"]}, "baseline_alarm": r["alarm"]}

    # ---------------- Kit update loop (main thread) ----------------
    def _on_update(self, _event):
        self._animate()
        res = self._hist_result
        if res is not None:
            self._hist_result = None
            self._finish_load(res)

        latest = None
        try:
            while True:
                latest = self._q.get_nowait()
        except queue.Empty:
            pass

        if self._replay_idx is not None:       # replay mode: ignore live data
            now = time.monotonic()
            if now >= self._replay_next:
                if self._replay_idx >= len(self._history):
                    self._stop_replay()
                    return
                row = self._history[self._replay_idx]
                try:
                    self._show_state(self._row_to_state(row), live=False)
                except Exception:
                    print("[RobotArm] replay error\n" + traceback.format_exc())
                self._set("link", f"REPLAY {self._replay_idx + 1}/{len(self._history)}   {row['ts'][11:19]} UTC", ORANGE)
                self._replay_idx += 1
                self._replay_next = now + 1.0
            return

        if latest is not None:
            try:
                self._show_state(latest, live=True)
            except Exception:
                print("[RobotArm] apply error\n" + traceback.format_exc())
        self._refresh_status()

    def _show_state(self, d, live):
        if self._handles is None:
            self._build_scene()
        if self._handles is None:
            return
        self._set_target(d)

        self._set("joints", "  ".join(f"{a:6.1f}" for a in d["joint_deg"]))
        self._set("grip", f"{d['gripper_pct']:.0f} %")
        self._set("mode", d["mode"], RED if d["mode"] == "fault_stop" else WHITE)
        self._set("temp", f"{d['motor_temp_c']:.1f} C", RED if d["motor_temp_c"] >= 85 else WHITE)
        self._set("cur", f"{d['motor_current_a']:.2f} A", RED if d["motor_current_a"] >= 5 else WHITE)
        label = d.get("ai", {}).get("label", "n/a")
        self._set("ai", label, GREEN if label == "normal"
                  else GREY if label in ("n/a", "warming_up", "unavailable") else RED)
        alarm = d.get("baseline_alarm")
        self._set("alarm", "ALARM" if alarm else "ok", RED if alarm else GREEN)

        if not live:
            self._set("agg", "(replay)")
            return
        self._last_rx = time.monotonic()
        agg = d.get("agg", {})
        self._set("agg", f"mean60s={agg.get('mean_temp_60s')}  max1h={agg.get('max_temp_1h')}  alarms1h={agg.get('alarms_1h')}")
        self._trend.append(d["motor_temp_c"])
        self._plot(self._trend_plot, self._trend)
        try:
            self._delays.append(_delay_s(d["timestamp"]))
            self._delays = self._delays[-300:]
            self._set("delay", f"last {self._delays[-1]:.2f}s  mean {sum(self._delays)/len(self._delays):.2f}s  max {max(self._delays):.2f}s")
        except Exception:
            pass

    def _refresh_status(self):
        if self._last_rx is None:
            self._set("link", "connected, waiting for data" if self._mqtt_connected else "WAITING FOR BROKER", GREY)
            return
        age = time.monotonic() - self._last_rx
        if age > STALE_AFTER_S:
            self._set("link", f"DISCONNECTED / STALE ({age:.0f}s without data)", ORANGE)
            self._set("ai", "unknown (stale)", ORANGE)
            self._set("mode", "unknown (stale)", ORANGE)
        else:
            self._set("link", f"LIVE  (bad messages ignored: {self._bad_msgs})", GREEN)

    def _set_target(self, d):
        """Start gliding from what is currently shown to the newly received state."""
        self._anim = {
            "t0": time.monotonic(),
            "j0": list(self._shown_joints), "j1": [float(a) for a in d["joint_deg"]],
            "g0": self._shown_grip, "g1": float(d["gripper_pct"]),
            "c0": self._shown_temp, "c1": float(d["motor_temp_c"]),
        }

    def _animate(self):
        """Runs every frame: move the arm a little closer to the latest target."""
        a = self._anim
        if a is None or self._handles is None:
            return
        k = min(1.0, (time.monotonic() - a["t0"]) / SMOOTH_S)
        self._shown_joints = [p + (q - p) * k for p, q in zip(a["j0"], a["j1"])]
        self._shown_grip = a["g0"] + (a["g1"] - a["g0"]) * k
        self._shown_temp = a["c0"] + (a["c1"] - a["c0"]) * k
        arm_model.apply_joint_angles(self._handles, self._shown_joints)
        arm_model.apply_gripper(self._handles, self._shown_grip)
        arm_model.set_motor_temp_color(self._handles, self._shown_temp)
        if k >= 1.0:
            self._anim = None
    # ---------------- Scene ----------------
    def _build_scene(self):
        try:
            ctx = omni.usd.get_context()
            stage = ctx.get_stage()
            if stage is None:
                ctx.new_stage()
                stage = ctx.get_stage()
            self._handles = arm_model.build_robot(stage)
            print("[RobotArm] arm built")
            ctx.get_selection().set_selected_prim_paths([arm_model.ROOT_PATH], True)
            try:
                from omni.kit.viewport.utility import frame_viewport_selection
                frame_viewport_selection()
            except Exception as e:
                print("[RobotArm] could not auto-frame:", e)
        except Exception:
            print("[RobotArm] BUILD ERROR\n" + traceback.format_exc())
            self._handles = None