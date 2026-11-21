from features import FEATURE_NAMES, WINDOW, make_features


def sample(temp, cur, j=0.0, mode="auto"):
    return {"temp": temp, "current": cur, "joints": [j] * 6, "mode": mode}


def test_none_until_window_full():
    assert make_features([sample(40, 2)] * (WINDOW - 1)) is None


def test_feature_vector_length():
    x = make_features([sample(40, 2)] * WINDOW)
    assert len(x) == len(FEATURE_NAMES)


def test_temp_rate_positive_when_heating():
    win = [sample(40 + i, 2) for i in range(WINDOW)]
    x = make_features(win)
    assert abs(x[FEATURE_NAMES.index("temp_rate_5s")] - 1.0) < 1e-9


def test_stopped_flag():
    win = [sample(40, 2)] * (WINDOW - 1) + [sample(40, 0.3, mode="fault_stop")]
    assert make_features(win)[FEATURE_NAMES.index("is_stopped")] == 1.0