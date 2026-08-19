"""
arrival_pairs.csv -> 도착 잔여좌석 예측 모델 학습/평가.

평가지표는 팀 기존 보고서와 직접 비교할 수 있도록 동일한 정의를 쓴다.
  - event-balanced MAE : 도착 사건마다 먼저 평균을 내고 그 평균들의 평균.
                         상류 스냅샷이 많은 사건이 과대표집되는 것을 막는다.
  - 저잔여 MAE         : 실제 도착 좌석이 5석 이하(및 10석 이하)인 사건만.
  - 만차 분류          : 실제 0석 vs 예측 0.5석 미만의 Accuracy/Recall/Precision/F1.

이 스크립트의 차별점(내 접근):
  1) 절대 좌석수가 아니라 "현재 대비 변화량(delta)"을 타깃으로 학습한다.
     현재 좌석수는 이미 알고 있는 값이므로, 모델이 남은 구간에서 몇 석이
     소진될지에만 집중하게 만든다.
  2) 손실함수를 MAE(absolute_error)로 두어 평가지표와 학습목표를 일치시킨다.
  3) 학습 시 사건 균형 가중치를 적용해 평가 방식과 학습 방식을 맞춘다.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"
MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
REPORT_DIR.mkdir(exist_ok=True)
MODEL_DIR.mkdir(exist_ok=True)

NUM_FEATURES = [
    "stop_gap", "minutes_to_arrival", "est_minutes",
    "snap_seats", "snap_seq", "target_seq", "capacity",
    "load_ratio", "seats_per_stop", "load_gap_inter",
    "currently_low_5", "currently_low_10",
    "trend3", "seat_change_per_stop", "min_per_stop3", "projected_seats",
    "target_progress", "snap_progress", "is_return",
    "hour", "time_sin", "time_cos", "dow", "is_weekend", "is_rush_am", "is_rush_pm",
    "hist_seat_stop_hour", "hist_seat_stop", "hist_seat_route_hour", "hist_low_rate",
    "x", "y", "low_plate", "crowded",
    "expected_drop", "expected_arrival_seats",
]
# 이번 라운드에서 추가한 피처. 기여도를 분리 측정하기 위해 따로 둔다.
CALENDAR_FEATURES = ["is_holiday", "is_off_day", "is_day_before_off", "is_day_after_off"]
PREVBUS_FEATURES = ["prev_bus_seats", "prev_bus_age_min", "prev_bus_was_full", "prev_bus_was_low"]
# 절제 실험 결과 달력 피처만 채택한다. 앞차 피처(PREVBUS)는 평일·연휴 모두에서
# 저잔여 MAE를 오히려 악화시켜 최종 모델에서 제외했다. 실험용으로 정의만 남긴다.
NUM_FEATURES = NUM_FEATURES + CALENDAR_FEATURES
CAT_FEATURES = ["route_id"]
LOW_SEAT_WEIGHT = 4.0
# 주말·공휴일은 수요 구조가 평일과 달라, 동등 가중으로 학습하면 평일 예측 신호를
# 희석시킨다(저잔여 MAE 3.754 -> 3.832로 악화). 버리는 대신 가중치를 낮춰
# 공휴일 패턴은 배우되 평일 예측을 흔들지 않게 한다.
OFF_DAY_WEIGHT = 0.3


# --------------------------------------------------------------------------
def load() -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / "arrival_pairs.csv", dtype={"route_id": str})
    for c in NUM_FEATURES:
        if c not in df.columns:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["route_code"] = df["route_id"].astype("category")
    return df


def event_weights(df: pd.DataFrame) -> np.ndarray:
    n = df.groupby("event_id")["event_id"].transform("size").to_numpy(float)
    w = 1.0 / n
    return w / w.mean()


def metrics(df: pd.DataFrame, pred: np.ndarray, name: str, split: str) -> dict:
    pred = np.clip(pred, 0, df["capacity"].to_numpy(float))
    s = pd.DataFrame({
        "event_id": df["event_id"].to_numpy(),
        "y": df["arrival_seats"].to_numpy(float),
        "p": pred,
    })
    s["ae"] = (s["y"] - s["p"]).abs()
    s["w3"] = s["ae"].le(3)
    s["w5"] = s["ae"].le(5)
    per = s.groupby("event_id", sort=False).agg(
        y=("y", "first"), mae=("ae", "mean"), w3=("w3", "mean"), w5=("w5", "mean"))
    low5, low10 = per["y"].le(5), per["y"].le(10)
    full_true = (s["y"] == 0).astype(int)
    full_pred = (s["p"] < 0.5).astype(int)
    return {
        "split": split, "model": name,
        "rows": len(s), "events": len(per),
        "event_mae": float(per["mae"].mean()),
        "low5_mae": float(per.loc[low5, "mae"].mean()) if low5.any() else np.nan,
        "low10_mae": float(per.loc[low10, "mae"].mean()) if low10.any() else np.nan,
        "within3": float(per["w3"].mean()),
        "within5": float(per["w5"].mean()),
        "row_mae": float(s["ae"].mean()),
        "full_acc": float(accuracy_score(full_true, full_pred)),
        "full_prec": float(precision_score(full_true, full_pred, zero_division=0)),
        "full_recall": float(recall_score(full_true, full_pred, zero_division=0)),
        "full_f1": float(f1_score(full_true, full_pred, zero_division=0)),
    }


# --------------------------------------------------------------------------
def make_matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = df[NUM_FEATURES].copy()
    X["route_code"] = df["route_code"]
    return X


def fit_hgb(Xtr, ytr, wtr, **kw):
    params = dict(loss="absolute_error", learning_rate=0.06, max_iter=600,
                  max_depth=None, max_leaf_nodes=63, min_samples_leaf=40,
                  l2_regularization=1.0, early_stopping=False,
                  categorical_features=["route_code"], random_state=42)
    params.update(kw)
    m = HistGradientBoostingRegressor(**params)
    m.fit(Xtr, ytr, sample_weight=wtr)
    return m


def fit_lgbm(Xtr, ytr, wtr, **kw):
    import lightgbm as lgb
    params = dict(objective="regression_l1", n_estimators=900, learning_rate=0.05,
                  num_leaves=63, min_child_samples=40, subsample=0.85,
                  subsample_freq=1, colsample_bytree=0.85, reg_lambda=1.0,
                  n_jobs=-1, random_state=42, verbose=-1)
    params.update(kw)
    m = lgb.LGBMRegressor(**params)
    m.fit(Xtr, ytr, sample_weight=wtr, categorical_feature=["route_code"])
    return m


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", action="store_true", help="최종 모델을 파일로 저장")
    args = ap.parse_args()

    df = load()
    tr = df[df["split"] == "train"]
    va = df[df["split"] == "val"]
    te = df[df["split"] == "test"]
    ho = df[df["split"] == "holiday"]
    for nm, d in (("train", tr), ("val", va), ("test", te), ("holiday", ho)):
        print(f"{nm:8s} {len(d):>9,}행 / {d['event_id'].nunique():>7,}사건")

    Xtr, Xva, Xte, Xho = make_matrix(tr), make_matrix(va), make_matrix(te), make_matrix(ho)
    # 연휴를 학습에 포함시킨 구성에서는 holiday 분할이 비므로 건너뛴다.
    EVAL = [(n, d, X) for n, d, X in
            (("val", va, Xva), ("test", te, Xte), ("holiday", ho, Xho)) if len(d)]
    ytr = tr["arrival_seats"].to_numpy(float)
    wtr = event_weights(tr)

    # delta 타깃: 현재 좌석 대비 변화량
    dtr = ytr - tr["snap_seats"].to_numpy(float)

    results = []

    # ---- 베이스라인 ----
    for split, d, _ in EVAL:
        results.append(metrics(d, d["snap_seats"].to_numpy(float), "baseline_persistence", split))
        results.append(metrics(d, d["projected_seats"].fillna(d["snap_seats"]).to_numpy(float),
                               "baseline_trend", split))
        results.append(metrics(d, d["hist_seat_stop_hour"].fillna(d["snap_seats"]).to_numpy(float),
                               "baseline_hist_avg", split))
        results.append(metrics(d, d["expected_arrival_seats"].fillna(d["snap_seats"]).to_numpy(float),
                               "baseline_expected_drop", split))

    models = {}

    # ---- 1) 절대값 타깃 HGB ----
    m = fit_hgb(Xtr, ytr, wtr)
    models["hgb_absolute"] = ("abs", m)

    # ---- 2) delta 타깃 HGB (핵심 아이디어) ----
    m = fit_hgb(Xtr, dtr, wtr)
    models["hgb_delta"] = ("delta", m)

    # ---- 3) delta 타깃 LightGBM ----
    try:
        m = fit_lgbm(Xtr, dtr, wtr)
        models["lgbm_delta"] = ("delta", m)
    except Exception as e:
        print(f"[lightgbm 건너뜀] {e}")

    # ---- 3b) 저잔여 가중 학습 (최종 채택안) ----
    # 실제 도착 좌석 10석 이하 사건에 4배 가중. 전체 MAE는 0.05석만 나빠지는 대신
    # 저잔여 MAE가 크게 개선된다. 만차 판별은 별도 분류기가 담당하므로 회귀 모델은
    # 만차 F1을 희생하더라도 저잔여 오차 최소화에 집중할 수 있다.
    off = tr["is_off_day"].fillna(0).to_numpy(float)
    low_w = (wtr
             * np.where(ytr <= 10, LOW_SEAT_WEIGHT, 1.0)
             * np.where(off > 0, OFF_DAY_WEIGHT, 1.0))
    # 학습 데이터가 늘어난 만큼 모델 용량도 키우고 학습률은 낮춘다.
    models["hgb_delta_low4"] = ("delta", fit_hgb(
        Xtr, dtr, low_w, max_iter=900, learning_rate=0.04, max_leaf_nodes=95))
    try:
        models["lgbm_delta_low4"] = ("delta", fit_lgbm(
            Xtr, dtr, low_w, n_estimators=1800, learning_rate=0.03, num_leaves=95))
    except Exception as e:
        print(f"[lightgbm(low) 건너뜀] {e}")

    def predict(kind, model, X, base):
        p = model.predict(X)
        return p + base if kind == "delta" else p

    preds = {"val": {}, "test": {}, "holiday": {}}
    for name, (kind, model) in models.items():
        for split, d, X in EVAL:
            p = predict(kind, model, X, d["snap_seats"].to_numpy(float))
            preds[split][name] = p
            results.append(metrics(d, p, name, split))
        print(f"학습 완료: {name}")

    # ---- 4) 앙상블 ----
    for ens_name, members in (
        ("ensemble_delta", ["hgb_delta", "lgbm_delta"]),
        ("ensemble_delta_low4", ["hgb_delta_low4", "lgbm_delta_low4"]),
    ):
        members = [m for m in members if m in preds["val"]]
        if len(members) < 2:
            continue
        for split, d, _ in EVAL:
            p = np.mean([preds[split][m] for m in members], axis=0)
            preds[split][ens_name] = p
            results.append(metrics(d, p, ens_name, split))

    # ---- 5) 만차 전용 분류기 ----
    # 회귀값을 0.5로 자르는 방식은 만차(전체의 약 1%)를 거의 못 잡는다.
    # 불균형을 보정한 별도 분류기를 두고 임계값을 validation에서 F1 최대로 맞춘다.
    clf_rows = []
    try:
        import lightgbm as lgb
        ytr_full = (tr["arrival_seats"].to_numpy(float) == 0).astype(int)
        pos_w = (len(ytr_full) - ytr_full.sum()) / max(ytr_full.sum(), 1)
        clf = lgb.LGBMClassifier(objective="binary", n_estimators=700, learning_rate=0.05,
                                 num_leaves=63, min_child_samples=40, subsample=0.85,
                                 subsample_freq=1, colsample_bytree=0.85,
                                 scale_pos_weight=pos_w, n_jobs=-1, random_state=42,
                                 verbose=-1)
        clf.fit(Xtr, ytr_full, sample_weight=wtr * np.where(off > 0, OFF_DAY_WEIGHT, 1.0),
                categorical_feature=["route_code"])

        pv = clf.predict_proba(Xva)[:, 1]
        yv = (va["arrival_seats"].to_numpy(float) == 0).astype(int)
        grid = np.linspace(0.05, 0.95, 91)
        f1s = [f1_score(yv, (pv >= t).astype(int), zero_division=0) for t in grid]
        thr = float(grid[int(np.argmax(f1s))])
        print(f"\n만차 분류기 최적 임계값(val F1 최대): {thr:.2f}  (val F1={max(f1s):.4f})")

        for split, d, X in EVAL:
            p = clf.predict_proba(X)[:, 1]
            yt = (d["arrival_seats"].to_numpy(float) == 0).astype(int)
            yp = (p >= thr).astype(int)
            clf_rows.append({
                "split": split, "model": "full_bus_classifier", "threshold": thr,
                "full_acc": float(accuracy_score(yt, yp)),
                "full_prec": float(precision_score(yt, yp, zero_division=0)),
                "full_recall": float(recall_score(yt, yp, zero_division=0)),
                "full_f1": float(f1_score(yt, yp, zero_division=0)),
            })
        models["full_bus_classifier"] = ("clf", clf)
    except Exception as e:
        print(f"[만차 분류기 건너뜀] {e}")

    if clf_rows:
        print("\n=== 만차 전용 분류기 ===")
        print(pd.DataFrame(clf_rows).to_string(index=False,
              float_format=lambda v: f"{v:.4f}"))
        pd.DataFrame(clf_rows).to_csv(REPORT_DIR / "full_bus_classifier.csv", index=False)

    res = pd.DataFrame(results)
    cols = ["split", "model", "events", "event_mae", "low5_mae", "low10_mae",
            "within3", "within5", "full_f1", "full_recall", "full_prec", "full_acc"]
    for split in ("val", "test", "holiday"):
        if res[res["split"] == split].empty:
            continue
        print(f"\n=== {split.upper()} ===")
        print(res[res["split"] == split][cols].to_string(index=False,
              float_format=lambda v: f"{v:.4f}"))

    res.to_csv(REPORT_DIR / "arrival_metrics.csv", index=False)

    v = res[res["split"] == "val"]
    best = v.sort_values("event_mae").iloc[0]["model"]
    best_low = v.sort_values("low10_mae").iloc[0]["model"]
    print(f"\nvalidation 전체 MAE 최고: {best}")
    print(f"validation 저잔여(<=10) MAE 최고: {best_low}")

    with open(REPORT_DIR / "arrival_summary.json", "w") as f:
        json.dump({"best_model_by_val": best, "metrics": results}, f,
                  ensure_ascii=False, indent=2, default=float)

    if args.save:
        for name, (kind, model) in models.items():
            joblib.dump({"kind": kind, "model": model, "num_features": NUM_FEATURES,
                         "cat_features": ["route_code"]},
                        MODEL_DIR / f"{name}.pkl")
        print(f"모델 저장 -> {MODEL_DIR}")


if __name__ == "__main__":
    main()
