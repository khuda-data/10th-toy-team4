"""
이번 라운드에 추가한 두 피처군의 기여도를 분리 측정하는 절제 실험(ablation).

  base      : 기존 피처만
  +calendar : 공휴일/대체공휴일/연휴 전후 플래그 추가
  +prevbus  : 같은 정류장에 직전에 다녀간 버스의 도착 좌석 추가
  +both     : 둘 다

모델·하이퍼파라미터·가중치를 전부 고정하고 피처 집합만 바꾸므로,
성능 차이의 원인을 피처로 특정할 수 있다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from train_arrival_models import (
    CALENDAR_FEATURES, LOW_SEAT_WEIGHT, NUM_FEATURES, PREVBUS_FEATURES,
    REPORT_DIR, event_weights, fit_lgbm, load, metrics,
)

BASE = [f for f in NUM_FEATURES if f not in CALENDAR_FEATURES + PREVBUS_FEATURES]


def matrix(df, feats):
    X = df[feats].copy()
    X["route_code"] = df["route_code"]
    return X


def main():
    df = load()
    tr, va, te = (df[df["split"] == s] for s in ("train", "val", "test"))
    ho = df[df["split"] == "holiday"]
    ytr = tr["arrival_seats"].to_numpy(float)
    dtr = ytr - tr["snap_seats"].to_numpy(float)
    w = event_weights(tr) * np.where(ytr <= 10, LOW_SEAT_WEIGHT, 1.0)

    sets = {
        "base": BASE,
        "+calendar": BASE + CALENDAR_FEATURES,
        "+prevbus": BASE + PREVBUS_FEATURES,
        "+both": BASE + CALENDAR_FEATURES + PREVBUS_FEATURES,
    }

    rows = []
    for name, feats in sets.items():
        model = fit_lgbm(matrix(tr, feats), dtr, w, n_estimators=700)
        for split, d in (("val", va), ("test", te), ("holiday", ho)):
            if d.empty:
                continue
            p = model.predict(matrix(d, feats)) + d["snap_seats"].to_numpy(float)
            rows.append(metrics(d, p, name, split))
        print(f"완료: {name} ({len(feats)}개 피처)")

    res = pd.DataFrame(rows)
    cols = ["split", "model", "event_mae", "low5_mae", "low10_mae", "within3", "full_f1"]
    for split in ("val", "test", "holiday"):
        sub = res[res["split"] == split]
        if sub.empty:
            continue
        print(f"\n=== {split.upper()} ===")
        print(sub[cols].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    res.to_csv(REPORT_DIR / "feature_ablation.csv", index=False)


if __name__ == "__main__":
    main()
