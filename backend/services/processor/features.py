"""Feature extraction shared by training (ml/train.py) and the live processor."""

WINDOW = 6   # samples = about 5 s at 1 Hz
FEATURE_NAMES = ["temp", "current", "temp_rate_5s", "speed", "current_residual",
                 "temp_mean", "current_mean", "current_std", "is_stopped"]


def make_features(win):
    """win: list of dicts {temp, current, joints[6], mode}, oldest first.
    Returns a feature list, or None until WINDOW samples are available."""
    if len(win) < WINDOW:
        return None
    win = win[-WINDOW:]
    n = len(win)
    speeds = [sum(abs(b - a) for a, b in zip(p["joints"], q["joints"]))
              for p, q in zip(win[:-1], win[1:])]
    temps = [w["temp"] for w in win]
    curs = [w["current"] for w in win]
    speed = speeds[-1]
    mean_c = sum(curs) / n
    std_c = (sum((c - mean_c) ** 2 for c in curs) / n) ** 0.5
    return [
        temps[-1],
        curs[-1],
        (temps[-1] - temps[0]) / (n - 1),          # deg C per sample
        speed,                                      # total deg/s over all joints
        curs[-1] - (1.0 + 0.02 * speed),            # current beyond what the motion explains
        sum(temps) / n,
        mean_c,
        std_c,
        1.0 if win[-1]["mode"] == "fault_stop" else 0.0,
    ]