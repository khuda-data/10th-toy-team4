"""
학습곡선(learning curve): 데이터를 더 모으면 성능이 더 오를까?

수집 일수를 2일 -> 11일로 늘려가며 같은 모델을 학습하고 같은 테스트셋(8/18)으로 채점한다.
곡선이 아직 가파르게 내려가고 있으면 "더 모으면 더 좋아진다",
평평해졌으면 "데이터를 더 모아도 소용없고 다른 방법이 필요하다"는 뜻이다.

공정성을 위해 매 지점마다 데이터셋을 처음부터 다시 만든다. 과거 통계 피처
(hist_*, expected_drop)는 학습기간 관측으로만 계산되므로, 학습기간이 바뀌면
피처값도 달라져야 하기 때문이다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

SRC = Path(__file__).resolve().parent
REPORT_DIR = SRC.parent / "reports"

# 학습에 쓸 수 있는 평일·주말 날짜(연휴 제외), 최신순
TRAIN_DAYS = ["2026-08-13", "2026-08-12", "2026-08-11", "2026-08-10", "2026-08-09",
              "2026-08-08", "2026-08-07", "2026-08-06", "2026-08-05", "2026-08-04",
              "2026-08-03"]
HOLIDAYS = ["2026-08-15", "2026-08-16", "2026-08-17"]
VAL, TEST, TODAY = "2026-08-14", "2026-08-18", "2026-08-19"


def build(n_days: int) -> None:
    """최신 n_days 일만 학습에 남기고 나머지는 제외한 데이터셋을 만든다."""
    dropped = TRAIN_DAYS[n_days:] + HOLIDAYS + [TODAY]
    subprocess.run(
        [sys.executable, str(SRC / "build_arrival_dataset.py"),
         "--val-dates", VAL, "--test-dates", TEST,
         "--holiday-dates", "", "--exclude-dates", ",".join(dropped)],
        check=True, capture_output=True, text=True,
    )


def evaluate() -> dict:
    import train_arrival_models as tm
    import importlib
    importlib.reload(tm)

    df = tm.load()
    tr, te = df[df["split"] == "train"], df[df["split"] == "test"]
    Xtr, Xte = tm.make_matrix(tr), tm.make_matrix(te)
    ytr = tr["arrival_seats"].to_numpy(float)
    dtr = ytr - tr["snap_seats"].to_numpy(float)
    w = tm.event_weights(tr) * np.where(ytr <= 10, tm.LOW_SEAT_WEIGHT, 1.0)

    model = tm.fit_lgbm(Xtr, dtr, w, n_estimators=900)
    pred = model.predict(Xte) + te["snap_seats"].to_numpy(float)
    m = tm.metrics(te, pred, "lgbm", "test")
    m["train_events"] = int(tr["event_id"].nunique())
    m["train_rows"] = len(tr)
    return m


def main():
    rows = []
    for n in (2, 4, 7, 11):
        print(f"\n{'='*50}\n수집 {n}일치로 학습 중...", flush=True)
        build(n)
        m = evaluate()
        m["days"] = n
        rows.append(m)
        print(f"  학습사건 {m['train_events']:,} | MAE {m['event_mae']:.4f} | "
              f"저잔여≤5 {m['low5_mae']:.4f} | ±3석 {m['within3']:.4f} | "
              f"만차F1 {m['full_f1']:.4f}", flush=True)

    res = pd.DataFrame(rows)
    cols = ["days", "train_events", "event_mae", "low5_mae", "low10_mae", "within3", "full_f1"]
    print("\n=== 학습곡선 (테스트: 2026-08-18 고정) ===")
    print(res[cols].to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    res.to_csv(REPORT_DIR / "learning_curve.csv", index=False)

    # 마지막 두 지점 사이의 개선폭으로 추가 수집의 한계효용을 가늠한다
    a, b = res.iloc[-2], res.iloc[-1]
    d_ev = b["train_events"] - a["train_events"]
    print(f"\n사건 {d_ev:,.0f}개(약 {b['days']-a['days']}일치)를 더 넣었을 때:")
    print(f"  전체 MAE   {a['event_mae']:.4f} -> {b['event_mae']:.4f} "
          f"({(b['event_mae']-a['event_mae'])/a['event_mae']*100:+.2f}%)")
    print(f"  저잔여 MAE {a['low5_mae']:.4f} -> {b['low5_mae']:.4f} "
          f"({(b['low5_mae']-a['low5_mae'])/a['low5_mae']*100:+.2f}%)")


if __name__ == "__main__":
    main()
