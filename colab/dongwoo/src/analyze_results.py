"""
학습된 모델의 오차 구조를 분석하고 리포트용 그래프를 만든다.

- 예측 지평(stop_gap)별 오차
- 시간대별 오차
- 실제 좌석 구간별 오차 (저잔여 구간이 왜 어려운지)
- 현재 좌석 -> 도착 좌석 전환 구조
- 퍼뮤테이션 기반 피처 중요도
"""
from __future__ import annotations

from pathlib import Path

import joblib
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"
MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
REPORT_DIR.mkdir(exist_ok=True)

plt.rcParams["axes.grid"] = True
plt.rcParams["grid.alpha"] = 0.3
plt.rcParams["figure.dpi"] = 130


def load():
    df = pd.read_csv(DATA_DIR / "arrival_pairs.csv", dtype={"route_id": str})
    df["route_code"] = df["route_id"].astype("category")
    return df


def predict(bundle, df):
    X = df[bundle["num_features"]].copy()
    X["route_code"] = df["route_code"]
    p = bundle["model"].predict(X)
    if bundle["kind"] == "delta":
        p = p + df["snap_seats"].to_numpy(float)
    return np.clip(p, 0, df["capacity"].to_numpy(float))


def event_mae(df, pred, by):
    t = pd.DataFrame({"event_id": df["event_id"].to_numpy(), by: df[by].to_numpy(),
                      "ae": np.abs(df["arrival_seats"].to_numpy(float) - pred)})
    per = t.groupby(["event_id", by], observed=True)["ae"].mean().reset_index()
    return per.groupby(by, observed=True)["ae"].agg(["mean", "size"])


def main():
    df = load()
    te = df[df["split"] == "test"].copy()
    bundles = {p.stem: joblib.load(p) for p in MODEL_DIR.glob("*.pkl")}
    if not bundles:
        raise SystemExit("models/ 에 모델 파일이 없습니다. train_arrival_models.py --save 먼저 실행하세요.")

    # 최종 채택 모델: 저잔여 가중 delta 앙상블
    members = [n for n in ("hgb_delta_low4", "lgbm_delta_low4") if n in bundles]
    if not members:
        members = [n for n in ("hgb_delta", "lgbm_delta") if n in bundles]
    name = "ensemble_delta_low4" if len(members) > 1 else members[0]
    pred = np.mean([predict(bundles[m], te) for m in members], axis=0)
    te["pred"] = pred
    te["ae"] = np.abs(te["arrival_seats"] - te["pred"])
    print(f"분석 모델: {name} | test 행 {len(te):,} / 사건 {te.event_id.nunique():,}")

    # ---------------- 그래프 1: 오차 구조 4분면 ----------------
    fig, ax = plt.subplots(2, 2, figsize=(12, 8))

    g = event_mae(te, pred, "stop_gap")
    ax[0, 0].plot(g.index, g["mean"], marker="o", ms=3, color="#1B4B8F")
    base = event_mae(te, te["snap_seats"].to_numpy(float), "stop_gap")
    ax[0, 0].plot(base.index, base["mean"], marker="s", ms=3, ls="--",
                  color="#A93A3A", label="baseline (current seats)")
    ax[0, 0].plot(g.index, g["mean"], marker="o", ms=3, color="#1B4B8F", label="model")
    ax[0, 0].set_xlabel("stops to go"); ax[0, 0].set_ylabel("event-balanced MAE (seats)")
    ax[0, 0].set_title("Error grows with prediction horizon"); ax[0, 0].legend()

    h = event_mae(te, pred, "hour")
    ax[0, 1].bar(h.index, h["mean"], color="#1B4B8F")
    ax[0, 1].set_xlabel("hour of day"); ax[0, 1].set_ylabel("event-balanced MAE")
    ax[0, 1].set_title("Error by time of day")

    ev = te.groupby("event_id").agg(y=("arrival_seats", "first"), mae=("ae", "mean"))
    bins = [-0.1, 0, 5, 10, 20, 30, 40, 100]
    labels = ["0", "1-5", "6-10", "11-20", "21-30", "31-40", "41+"]
    ev["band"] = pd.cut(ev["y"], bins=bins, labels=labels)
    bm = ev.groupby("band", observed=True)["mae"].agg(["mean", "size"])
    ax[1, 0].bar(range(len(bm)), bm["mean"], color="#9C6510")
    ax[1, 0].set_xticks(range(len(bm))); ax[1, 0].set_xticklabels(bm.index, rotation=0)
    ax[1, 0].set_xlabel("actual arrival seats"); ax[1, 0].set_ylabel("event-balanced MAE")
    ax[1, 0].set_title("Low-seat events are the hard cases")
    for i, (m, n) in enumerate(zip(bm["mean"], bm["size"])):
        ax[1, 0].text(i, m, f"n={n}", ha="center", va="bottom", fontsize=7)

    sample = te.sample(min(30000, len(te)), random_state=0)
    ax[1, 1].scatter(sample["arrival_seats"], sample["pred"], s=2, alpha=0.08, color="#1B4B8F")
    lim = [0, te["capacity"].max()]
    ax[1, 1].plot(lim, lim, color="#A93A3A", lw=1)
    ax[1, 1].set_xlabel("actual arrival seats"); ax[1, 1].set_ylabel("predicted")
    ax[1, 1].set_title("Predicted vs actual (test)")

    fig.tight_layout()
    fig.savefig(REPORT_DIR / "error_structure.png")
    print("saved -> reports/error_structure.png")

    # ---------------- 그래프 2: 전환 사례 분석 ----------------
    fig2, ax2 = plt.subplots(1, 2, figsize=(11, 4.2))
    ev2 = te.groupby("event_id").agg(
        y=("arrival_seats", "first"), first_snap=("snap_seats", "last"), mae=("ae", "mean"))
    ev2["drop"] = ev2["first_snap"] - ev2["y"]
    ax2[0].hist(ev2["drop"], bins=60, range=(-20, 40), color="#1F7A5C")
    ax2[0].set_xlabel("seats consumed between last snapshot and arrival")
    ax2[0].set_ylabel("events"); ax2[0].set_title("How much changes after the app's number")

    cur = pd.cut(ev2["first_snap"], [-0.1, 5, 10, 15, 20, 25, 30, 40, 100],
                 labels=["0-5", "6-10", "11-15", "16-20", "21-25", "26-30", "31-40", "41+"])
    cm = ev2.groupby(cur, observed=True)["mae"].mean()
    ax2[1].bar(range(len(cm)), cm.values, color="#9C6510")
    ax2[1].set_xticks(range(len(cm))); ax2[1].set_xticklabels(cm.index, rotation=30)
    ax2[1].set_xlabel("current seats at last snapshot"); ax2[1].set_ylabel("event-balanced MAE")
    ax2[1].set_title("Error by current seat level")
    fig2.tight_layout()
    fig2.savefig(REPORT_DIR / "transition_analysis.png")
    print("saved -> reports/transition_analysis.png")

    # ---------------- 피처 중요도 (퍼뮤테이션) ----------------
    rng = np.random.default_rng(0)
    idx = rng.choice(len(te), size=min(60000, len(te)), replace=False)
    sub = te.iloc[idx].copy()
    def ens(d):
        return np.mean([predict(bundles[m], d) for m in members], axis=0)

    base_mae = np.abs(sub["arrival_seats"].to_numpy(float) - ens(sub)).mean()
    rows = []
    for col in bundles[members[0]]["num_features"]:
        if sub[col].isna().all():
            continue
        keep = sub[col].copy()
        sub[col] = rng.permutation(sub[col].to_numpy())
        mae = np.abs(sub["arrival_seats"].to_numpy(float) - ens(sub)).mean()
        sub[col] = keep
        rows.append({"feature": col, "mae_increase": mae - base_mae})
    imp = pd.DataFrame(rows).sort_values("mae_increase", ascending=False)
    imp.to_csv(REPORT_DIR / "permutation_importance.csv", index=False)

    fig3, ax3 = plt.subplots(figsize=(7, 6))
    top = imp.head(15)[::-1]
    ax3.barh(top["feature"], top["mae_increase"], color="#1B4B8F")
    ax3.set_xlabel("MAE increase when feature is shuffled (seats)")
    ax3.set_title(f"Permutation importance — {name} (test)")
    fig3.tight_layout()
    fig3.savefig(REPORT_DIR / "feature_importance_perm.png")
    print("saved -> reports/feature_importance_perm.png")
    print("\n=== 피처 중요도 상위 15 ===")
    print(imp.head(15).to_string(index=False))

    # ---------------- 표 저장 ----------------
    g.to_csv(REPORT_DIR / "mae_by_horizon.csv")
    bm.to_csv(REPORT_DIR / "mae_by_seat_band.csv")
    print("\n=== 예측 지평별 MAE ===")
    print(g.head(20).to_string())
    print("\n=== 실제 좌석 구간별 MAE ===")
    print(bm.to_string())


if __name__ == "__main__":
    main()
