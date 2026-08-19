"""
저잔여 구간 정확도 개선 실험.

동기: 전체 MAE는 낮아도 실제로 사용자가 궁금한 구간(자리가 거의 없는 상황)의
오차가 가장 크다. 실제 도착 좌석 41석 이상 구간의 MAE는 1.30석인데
0석 구간은 6.16석으로 5배 가까이 나쁘다.

접근: 저잔여 사건에 학습 가중치를 더 주는 비용민감 학습(cost-sensitive learning).
만차 판별은 이미 별도 분류기가 담당하므로, 회귀 모델은 만차 F1을 신경 쓰지 않고
저잔여 MAE 최소화에만 집중할 수 있다. 이 분리가 이 실험의 전제다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from train_arrival_models import (
    NUM_FEATURES, REPORT_DIR, event_weights, fit_lgbm, load, make_matrix, metrics,
)


def main():
    df = load()
    tr, va, te = (df[df["split"] == s] for s in ("train", "val", "test"))
    Xtr, Xva, Xte = make_matrix(tr), make_matrix(va), make_matrix(te)
    ytr = tr["arrival_seats"].to_numpy(float)
    dtr = ytr - tr["snap_seats"].to_numpy(float)
    base_w = event_weights(tr)

    variants = {
        "w1_baseline": np.ones_like(ytr),
        "w2_low10": np.where(ytr <= 10, 2.0, 1.0),
        "w4_low10": np.where(ytr <= 10, 4.0, 1.0),
        "w8_low10": np.where(ytr <= 10, 8.0, 1.0),
        # 현재는 여유가 있는데 도착 시 소진되는 "전환" 사건에 집중
        "w4_transition": np.where(
            (ytr <= 10) & (tr["snap_seats"].to_numpy(float) > 10), 4.0, 1.0),
    }

    rows = []
    for name, extra in variants.items():
        model = fit_lgbm(Xtr, dtr, base_w * extra, n_estimators=700)
        for split, d, X in (("val", va, Xva), ("test", te, Xte)):
            p = model.predict(X) + d["snap_seats"].to_numpy(float)
            rows.append(metrics(d, p, name, split))
        print(f"완료: {name}")

    res = pd.DataFrame(rows)
    cols = ["split", "model", "event_mae", "low5_mae", "low10_mae", "within3", "within5"]
    for split in ("val", "test"):
        print(f"\n=== {split.upper()} ===")
        print(res[res["split"] == split][cols].to_string(
            index=False, float_format=lambda v: f"{v:.4f}"))
    res.to_csv(REPORT_DIR / "lowseat_experiment.csv", index=False)


if __name__ == "__main__":
    main()
