import processor as p
from features import WINDOW


def sample(temp, cur):
    return {"temp": temp, "current": cur, "joints": [0.0] * 6, "mode": "auto"}


def test_model_loaded():
    assert p.MODEL is not None, "run with MODEL_PATH=services/processor/model.joblib"


def test_warming_up_before_window_full():
    assert p.classify([sample(40, 2)] * (WINDOW - 1))["label"] == "warming_up"


def test_output_is_valid_class_with_confidence():
    out = p.classify([sample(40 + i, 2) for i in range(WINDOW)])
    assert out["label"] in {"normal", "overtemp", "overcurrent"}
    assert 0.0 <= out["confidence"] <= 1.0


def test_overcurrent_window_is_flagged():
    out = p.classify([sample(45, 7.0 + 0.1 * i) for i in range(WINDOW)])
    assert out["label"] != "normal"