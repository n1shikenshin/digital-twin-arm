"""Generate labelled data from many separate simulated runs (1 Hz).
The physics mirrors services/simulator/simulator.py."""
import csv
import math
import random
from collections import Counter
from pathlib import Path

AMPL = [60, 40, 50, 80, 40, 90]
PERIOD = [20, 16, 14, 24, 12, 18]
OUT = Path(__file__).resolve().parent / "data"
COLS = ["run_id", "split", "scenario", "t", "j1", "j2", "j3", "j4", "j5", "j6",
        "mode", "temp", "current", "fault", "label"]


def simulate_run(run_id, split, scenario, ambient, payload, steps=900):
    rng = random.Random(run_id)
    amp_scale = rng.uniform(0.7, 1.15)     # varied motion per run
    per_scale = rng.uniform(0.8, 1.25)
    sev = rng.uniform(0.5, 1.0)            # fault severity per run (mild to full)

    schedule = ["none"] * steps            # injected fault at each second
    t = rng.randint(40, 120)
    while t < steps:
        fault = rng.choice(["overtemp", "overcurrent"])
        length = rng.randint(40, 110)
        for k in range(t, min(t + length, steps)):
            schedule[k] = fault
        t += length + rng.randint(60, 160)

    def pose(ph):
        return [round(amp_scale * AMPL[i] * math.sin(2 * math.pi * ph / (per_scale * PERIOD[i])), 2)
                for i in range(6)]

    phase = rng.uniform(0, 30)
    prev = pose(phase)
    temp = ambient + 13.0 * payload + rng.uniform(-2, 2)
    stopped = False
    rows = []
    for k in range(steps):
        fault = schedule[k]
        if temp >= 100.0:
            stopped = True
        elif stopped and temp < 70.0 and fault == "none":
            stopped = False
        if stopped:
            angles, current, mode = prev, 0.3 + rng.gauss(0, 0.03), "fault_stop"
        else:
            phase += 1.0
            angles = pose(phase)
            speed = sum(abs(angles[i] - prev[i]) for i in range(6))
            current = (1.0 + 0.02 * speed) * payload + rng.gauss(0, 0.08)
            if fault == "overcurrent":
                current += rng.uniform(3.5, 5.0) * sev
            mode = "auto"
        prev = angles
        if fault == "overtemp" and not stopped:
            temp += rng.uniform(0.7, 1.1) * sev
        else:
            temp += (ambient + 6.0 * current - temp) * 0.08 + rng.gauss(0, 0.1)
        temp = max(ambient, min(temp, 115.0))
        current = max(current, 0.0)
        # ground truth: a thermal stop counts as an overtemp condition
        label = "overtemp" if mode == "fault_stop" else {"none": "normal"}.get(fault, fault)
        rows.append([run_id, split, scenario, k, *angles, mode,
                     round(temp, 2), round(current, 3), fault, label])
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    plan = [  # split, run ids, scenario, ambient range, payload range
        ("train", range(0, 24), "nominal", (22, 30), (0.9, 1.1)),
        ("test", range(24, 30), "nominal", (22, 30), (0.9, 1.1)),
        ("heldout", range(100, 108), "heavy_payload_hot_room", (32, 38), (1.2, 1.4)),
    ]
    rows = []
    for split, ids, scenario, amb, pay in plan:
        for rid in ids:
            r = random.Random(rid * 7919)
            rows += simulate_run(rid, split, scenario, r.uniform(*amb), r.uniform(*pay))
    with open(OUT / "runs.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    for split, *_ in plan:
        c = Counter(r[-1] for r in rows if r[1] == split)
        print(split, dict(c))
    print("wrote", OUT / "runs.csv", len(rows), "rows")


if __name__ == "__main__":
    main()