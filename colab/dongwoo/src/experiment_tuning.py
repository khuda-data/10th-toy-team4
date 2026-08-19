"""
연휴 데이터 희석 문제를 해결하기 위한 가중치·용량 실험.

배경: 연휴(8/15~17)를 학습에 넣으면 공휴일 패턴을 배울 수 있지만, 평일과 수요
구조가 전혀 달라 평일 예측 신호를 희석시킨다. 실제로 연휴를 넣었더니 평일 저잔여
MAE가 3.754 -> 3.832로 나빠졌다.

아이디어: 연휴를 버리는 대신 '가중치를 낮춰서' 학습에 남긴다. 그러면 공휴일
패턴은 배우되 평일 예측을 흔들지 않는다. 이와 함께 저잔여 가중치와 모델 용량도
데이터가 늘어난 상태에서 다시 조정한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from train_arrival_models import (
    LOW_SEAT_WEIGHT, REPORT_DIR, event_weights, fit_lgbm, load, make_matrix, metrics,
)


def main():
    df = load()
    tr, va, te = (df[df["split"] == s] for s in ("train", "val", "test"))
    Xtr, Xva, Xte = make_matrix(tr), make_matrix(va), make_matrix(te)
    ytr = tr["arrival_seats"].to_numpy(float)
    dtr = ytr - tr["snap_seats"].to_numpy(float)
    ev_w = event_weights(tr)
    off = tr["is_off_day"].to_numpy(float)  # 주말·공휴일이면 1
    low = (ytr <= 10).astype(float)

    print(f"학습 사건 중 쉬는 날 비중: {tr.drop_duplicates('event_id')['is_off_day'].mean():.1%}")

    def w(low_mult: float, off_mult: float) -> np.ndarray:
        return ev_w * np.where(low > 0, low_mult, 1.0) * np.where(off > 0, off_mult, 1.0)

    configs = {
        # 현재 채택안
        "cur_low4_off1.0":      dict(sw=w(4.0, 1.0), kw={}),
        # 쉬는 날 가중치를 낮춰 평일 신호 희석을 막는다
        "low4_off0.3":          dict(sw=w(4.0, 0.3), kw={}),
        "low4_off0.1":          dict(sw=w(4.0, 0.1), kw={}),
        # 저잔여 가중치 재조정
        "low6_off0.3":          dict(sw=w(6.0, 0.3), kw={}),
        # 데이터가 늘었으니 모델 용량을 키운다
        "low4_off0.3_big":      dict(sw=w(4.0, 0.3),
                                     kw=dict(n_estimators=1800, learning_rate=0.03,
                                             num_leaves=95)),
    }

    rows = []
    for name, c in configs.items():
        m = fit_lgbm(Xtr, dtr, c["sw"], **c["kw"])
        for split, d, X in (("val", va, Xva), ("test", te, Xte)):
            p = m.predict(X) + d["snap_seats"].to_numpy(float)
            rows.append(metrics(d, p, name, split))
        print(f"완료: {name}")

    res = pd.DataFrame(rows)
    cols = ["split", "model", "event_mae", "low5_mae", "low10_mae", "within3", "full_f1"]
    for split in ("val", "test"):
        print(f"\n=== {split.upper()} ===")
        print(res[res["split"] == split][cols].to_string(
            index=False, float_format=lambda v: f"{v:.4f}"))
    res.to_csv(REPORT_DIR / "tuning_experiment.csv", index=False)


if __name__ == "__main__":
    main()
