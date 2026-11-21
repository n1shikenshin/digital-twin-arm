"""Train and evaluate the fault classifier. Splits are by simulation RUN, never by random rows."""
import json
import sys
from pathlib import Path

import joblib
import matplotlib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "services" / "processor"))
from features import FEATURE_NAMES, WINDOW, make_features  # noqa: E402

CLASSES = ["normal", "overtemp", "overcurrent"]
OUT = HERE / "results"
OUT.mkdir(exist_ok=True)


def build(df):
    X, y, meta = [], [], []
    for run_id, g in df.groupby("run_id", sort=True):
        g = g.sort_values("t")
        rows = [{"temp": r.temp, "current": r.current,
                 "joints": [r.j1, r.j2, r.j3, r.j4, r.j5, r.j6], "mode": r.mode}
                for r in g.itertuples()]
        labels = g["label"].tolist()
        for i in range(WINDOW - 1, len(rows)):     # window never crosses a run boundary
            X.append(make_features(rows[i - WINDOW + 1:i + 1]))
            y.append(labels[i])
            meta.append((run_id, i))
    return np.array(X), np.array(y), meta


def threshold_baseline(X):
    """The simple alarm: temp >= 85 C or current >= 5 A."""
    return np.array(["overtemp" if t >= 85 else "overcurrent" if c >= 5.0 else "normal"
                     for t, c in zip(X[:, 0], X[:, 1])])


def detection_delay(y, pred, meta):
    """Seconds from the start of each true fault until it is first predicted."""
    by_run = {}
    for k, (rid, _i) in enumerate(meta):
        by_run.setdefault(rid, []).append(k)
    delays = {"overtemp": [], "overcurrent": []}
    missed = {"overtemp": 0, "overcurrent": 0}
    for ks in by_run.values():
        k = 0
        while k < len(ks):
            cls = y[ks[k]]
            if cls == "normal":
                k += 1
                continue
            start = k
            while k < len(ks) and y[ks[k]] == cls:
                k += 1
            hit = next((j - start for j in range(start, k) if pred[ks[j]] == cls), None)
            if hit is None:
                missed[cls] += 1
            else:
                delays[cls].append(hit)
    return {c: {"events": len(delays[c]) + missed[c], "missed": missed[c],
                "mean_delay_s": round(float(np.mean(delays[c])), 1) if delays[c] else None}
            for c in delays}


def plot_cm(rep, split):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for ax, (tag, r) in zip(axes, rep.items()):
        cm = np.array(r["confusion_matrix"])
        ax.imshow(cm, cmap="Blues")
        ax.set_xticks(range(3)); ax.set_xticklabels(CLASSES, rotation=30)
        ax.set_yticks(range(3)); ax.set_yticklabels(CLASSES)
        for i in range(3):
            for j in range(3):
                ax.text(j, i, int(cm[i, j]), ha="center", va="center")
        ax.set_title(f"{tag} ({split})")
        ax.set_xlabel("predicted"); ax.set_ylabel("true")
    fig.tight_layout()
    fig.savefig(OUT / f"confusion_{split}.png", dpi=150)
    plt.close(fig)


def evaluate(split, model, df):
    X, y, meta = build(df[df["split"] == split])
    rep = {}
    for tag, pred in [("random_forest", model.predict(X)), ("threshold_baseline", threshold_baseline(X))]:
        cm = confusion_matrix(y, pred, labels=CLASSES)
        cr = classification_report(y, pred, labels=CLASSES, output_dict=True, zero_division=0)
        rep[tag] = {
            "confusion_matrix": cm.tolist(),
            "per_class": {c: {k: round(cr[c][k], 3) for k in ("precision", "recall", "f1-score")} for c in CLASSES},
            "macro_f1": round(cr["macro avg"]["f1-score"], 3),
            "detection": detection_delay(y, pred, meta),
        }
    plot_cm(rep, split)
    print(f"\n=== {split.upper()} SET ({len(y)} windows) ===")
    for tag, r in rep.items():
        print(f"[{tag}] macro-F1 = {r['macro_f1']}")
        for c in CLASSES:
            p = r["per_class"][c]
            print(f"   {c:12s} precision={p['precision']:.2f} recall={p['recall']:.2f} F1={p['f1-score']:.2f}")
        print("   confusion matrix (rows = true, cols = predicted; order", CLASSES, ")")
        for row in r["confusion_matrix"]:
            print("      ", row)
        print("   detection delay:", r["detection"])
    return rep


def main():
    df = pd.read_csv(HERE / "data" / "runs.csv")
    Xtr, ytr, _ = build(df[df["split"] == "train"])
    model = RandomForestClassifier(n_estimators=200, max_depth=12, min_samples_leaf=3,
                                   class_weight="balanced", random_state=42, n_jobs=-1)
    model.fit(Xtr, ytr)
    print("trained on", len(ytr), "windows from", df[df["split"] == "train"]["run_id"].nunique(), "runs")

    results = {"test": evaluate("test", model, df), "heldout": evaluate("heldout", model, df)}
    imp = sorted(zip(FEATURE_NAMES, model.feature_importances_), key=lambda x: -x[1])
    results["feature_importance"] = {k: round(float(v), 3) for k, v in imp}
    print("\nfeature importance:", results["feature_importance"])

    with open(OUT / "metrics.json", "w") as f:
        json.dump(results, f, indent=2)
    joblib.dump(model, ROOT / "services" / "processor" / "model.joblib")
    print("saved model + results in", OUT)


if __name__ == "__main__":
    main()