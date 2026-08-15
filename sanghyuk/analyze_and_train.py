"""Ridge로 다음 정류장 도착 잔여좌석을 예측한다."""

from __future__ import annotations

import argparse
import json
import os
import pickle
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/10th-toy-team4-matplotlib")

import joblib
import matplotlib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    accuracy_score,
    explained_variance_score,
    f1_score,
    max_error,
    mean_absolute_error,
    mean_absolute_percentage_error,
    mean_squared_error,
    median_absolute_error,
    precision_score,
    r2_score,
    recall_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

matplotlib.use("Agg")
import matplotlib.pyplot as plt


TARGET = "arrival_remaining_seats"
LOW_SEAT_THRESHOLD = 10
FULL_BUS_THRESHOLDS = (0.5, 1.0, 1.5, 2.0)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
NUMERIC_FEATURES = [
    "remaining_seats",
    "station_seq",
    "next_station_seq",
    "stations_ahead",
    "recent_seat_change",
    "minutes_since_previous_station",
    "hour_sin",
    "hour_cos",
    "day_of_week",
    "is_weekend",
    "is_rush_hour",
    "temperature",
    "precipitation",
    "wind_speed",
    "weather_available",
]
CATEGORICAL_FEATURES = [
    "route_id",
    "vehicle_id",
    "route_type_code",
    "crowded",
    "low_plate",
    "state_code",
    "tagless_code",
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="다음 정류장 도착 잔여좌석 Ridge 예측"
    )
    parser.add_argument(
        "--history", type=Path, default=PROJECT_ROOT / "data/csv/history_all.csv"
    )
    parser.add_argument(
        "--weather", type=Path, default=PROJECT_ROOT / "data/csv/weather_log.csv"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data/analysis/models"
    )
    parser.add_argument("--test-ratio", type=float, default=0.2)
    parser.add_argument("--ridge-alpha", type=float, default=10.0)
    return parser.parse_args()


def load_data(history_path: Path, weather_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not history_path.exists():
        raise FileNotFoundError(f"버스 이력 파일이 없습니다: {history_path}")
    if not weather_path.exists():
        raise FileNotFoundError(f"날씨 파일이 없습니다: {weather_path}")
    history = pd.read_csv(history_path)
    weather = pd.read_csv(weather_path)
    return history, weather


def make_station_visits(history: pd.DataFrame) -> pd.DataFrame:
    """같은 차량이 같은 정류장에 머무는 반복 스냅샷을 한 방문으로 축약한다."""
    data = history.copy()
    data["observed_at"] = pd.to_datetime(data["observed_at"], errors="coerce", utc=True)
    data["remaining_seats"] = pd.to_numeric(data["remaining_seats"], errors="coerce")
    data["station_seq"] = pd.to_numeric(data["station_seq"], errors="coerce")
    data = data.dropna(subset=["observed_at", "vehicle_id", "station_seq", "remaining_seats"])
    data = data[data["remaining_seats"] >= 0].sort_values(["vehicle_id", "observed_at"])

    previous_station = data.groupby("vehicle_id")["station_seq"].shift()
    gap_minutes = (
        data["observed_at"] - data.groupby("vehicle_id")["observed_at"].shift()
    ).dt.total_seconds().div(60)
    new_visit = previous_station.ne(data["station_seq"]) | gap_minutes.gt(30) | gap_minutes.isna()
    data["visit_id"] = new_visit.groupby(data["vehicle_id"]).cumsum()
    visits = (
        data.groupby(["vehicle_id", "visit_id"], as_index=False, sort=False)
        .last()
        .sort_values(["vehicle_id", "observed_at"])
        .reset_index(drop=True)
    )
    return visits


def make_supervised_data(history: pd.DataFrame, weather: pd.DataFrame) -> pd.DataFrame:
    visits = make_station_visits(history)
    vehicle_groups = visits.groupby("vehicle_id", sort=False)
    visits["next_observed_at"] = vehicle_groups["observed_at"].shift(-1)
    visits["next_station_seq"] = vehicle_groups["station_seq"].shift(-1)
    visits[TARGET] = vehicle_groups["remaining_seats"].shift(-1)
    visits["previous_seats"] = vehicle_groups["remaining_seats"].shift(1)
    visits["previous_observed_at"] = vehicle_groups["observed_at"].shift(1)
    visits["recent_seat_change"] = visits["remaining_seats"] - visits["previous_seats"]
    visits["minutes_to_arrival"] = (
        visits["next_observed_at"] - visits["observed_at"]
    ).dt.total_seconds().div(60)
    visits["minutes_since_previous_station"] = (
        visits["observed_at"] - visits["previous_observed_at"]
    ).dt.total_seconds().div(60)
    visits["stations_ahead"] = (visits["next_station_seq"] - visits["station_seq"]).abs()

    # 밤새 끊긴 기록이나 운행 회차가 바뀐 행을 다음 정류장 이동으로 연결하지 않는다.
    visits = visits[
        visits[TARGET].notna()
        & visits["minutes_to_arrival"].between(0.1, 30)
        & visits["stations_ahead"].between(1, 5)
    ].copy()

    local_time = visits["observed_at"].dt.tz_convert("Asia/Seoul")
    hour = local_time.dt.hour + local_time.dt.minute / 60
    visits["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    visits["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    visits["day_of_week"] = local_time.dt.dayofweek
    visits["is_weekend"] = (local_time.dt.dayofweek >= 5).astype(int)
    visits["is_rush_hour"] = local_time.dt.hour.isin([6, 7, 8, 9, 16, 17, 18, 19, 20]).astype(int)

    weather_data = weather.copy()
    weather_data["weather_time"] = pd.to_datetime(weather_data["tm"], errors="coerce")
    weather_data["weather_time"] = weather_data["weather_time"].dt.tz_localize(
        "Asia/Seoul", ambiguous="NaT", nonexistent="shift_forward"
    ).dt.tz_convert("UTC")
    weather_data = weather_data.dropna(subset=["weather_time"]).sort_values("weather_time")
    visits = pd.merge_asof(
        visits.sort_values("observed_at"),
        weather_data[["weather_time", "temperature", "precipitation", "wind_speed"]],
        left_on="observed_at",
        right_on="weather_time",
        direction="nearest",
        tolerance=pd.Timedelta("31min"),
    )
    visits["weather_available"] = visits["weather_time"].notna().astype(int)
    for column in CATEGORICAL_FEATURES:
        visits[column] = visits[column].fillna("unknown").astype(str)
    return visits.sort_values("observed_at").reset_index(drop=True)


def make_model(regressor: object) -> Pipeline:
    numeric = Pipeline(
        [("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())]
    )
    categorical = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocessing = ColumnTransformer(
        [("numeric", numeric, NUMERIC_FEATURES), ("categorical", categorical, CATEGORICAL_FEATURES)]
    )
    return Pipeline([("preprocessing", preprocessing), ("regressor", regressor)])


def evaluate(y_true: pd.Series, prediction: np.ndarray) -> dict[str, float]:
    errors = np.asarray(y_true) - prediction
    nonzero = np.asarray(y_true) != 0
    result = {
        "MAE": mean_absolute_error(y_true, prediction),
        "MSE": mean_squared_error(y_true, prediction),
        "RMSE": mean_squared_error(y_true, prediction) ** 0.5,
        "R2": r2_score(y_true, prediction),
        "Explained variance": explained_variance_score(y_true, prediction),
        "Median absolute error": median_absolute_error(y_true, prediction),
        "Max error": max_error(y_true, prediction),
        "±3 seats accuracy": np.mean(np.abs(errors) <= 3),
        "±5 seats accuracy": np.mean(np.abs(errors) <= 5),
    }
    # 0석의 MAPE는 정의되지 않으므로 0이 아닌 실제값에 대해서만 별도 계산한다.
    result["MAPE (nonzero targets)"] = (
        mean_absolute_percentage_error(np.asarray(y_true)[nonzero], prediction[nonzero])
        if nonzero.any()
        else float("nan")
    )
    return {key: round(float(value), 6) for key, value in result.items()}


def evaluate_binary_classification(
    actual_positive: np.ndarray, predicted_positive: np.ndarray
) -> dict[str, float | int]:
    """두 boolean 배열로 Accuracy, Precision, Recall과 F1을 계산한다."""
    actual_positive = np.asarray(actual_positive, dtype=bool)
    predicted_positive = np.asarray(predicted_positive, dtype=bool)
    true_positive = int(np.sum(actual_positive & predicted_positive))
    true_negative = int(np.sum(~actual_positive & ~predicted_positive))
    false_positive = int(np.sum(~actual_positive & predicted_positive))
    false_negative = int(np.sum(actual_positive & ~predicted_positive))
    return {
        "Accuracy": round(float(accuracy_score(actual_positive, predicted_positive)), 6),
        "Precision": round(
            float(precision_score(actual_positive, predicted_positive, zero_division=0)), 6
        ),
        "Recall": round(
            float(recall_score(actual_positive, predicted_positive, zero_division=0)), 6
        ),
        "F1-score": round(
            float(f1_score(actual_positive, predicted_positive, zero_division=0)), 6
        ),
        "True positive": true_positive,
        "True negative": true_negative,
        "False positive": false_positive,
        "False negative": false_negative,
    }


def printable_params(model: Pipeline) -> dict[str, object]:
    params = model.named_steps["regressor"].get_params(deep=True)
    return {key: value for key, value in params.items() if not callable(value)}


def print_model_report(name: str, model: Pipeline, metrics: dict[str, float]) -> None:
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    print("모델 파라미터:")
    print(json.dumps(printable_params(model), ensure_ascii=False, indent=2, default=str))
    print("평가 지표:")
    for key, value in metrics.items():
        suffix = "%" if "accuracy" in key.lower() or "mape" in key.lower() else ""
        shown = value * 100 if suffix else value
        print(f"  {key:<28} {shown:>12.4f}{suffix}")


def main() -> int:
    args = parse_args()
    if not 0.05 <= args.test_ratio <= 0.5:
        raise ValueError("--test-ratio는 0.05~0.5 범위여야 합니다.")
    history, weather = load_data(args.history, args.weather)
    data = make_supervised_data(history, weather)
    if len(data) < 200:
        raise ValueError(f"학습 가능한 정류장 이동 데이터가 {len(data)}건뿐입니다.")

    timestamps = data["observed_at"].drop_duplicates().sort_values().reset_index(drop=True)
    cutoff_index = max(1, int(len(timestamps) * (1 - args.test_ratio))) - 1
    cutoff = timestamps.iloc[cutoff_index]
    train = data[data["observed_at"] <= cutoff].copy()
    test = data[data["observed_at"] > cutoff].copy()
    if test.empty:
        raise ValueError("테스트 데이터가 없습니다. 분할 비율을 확인하세요.")

    models = {"Ridge Regression": make_model(Ridge(alpha=args.ridge_alpha))}

    print("=" * 72)
    print("다음 정류장 도착 잔여좌석 예측")
    print("=" * 72)
    print(f"원본 버스 관측: {len(history):,}건")
    print(f"학습용 정류장 이동: {len(data):,}건")
    print(f"학습/테스트: {len(train):,}건 / {len(test):,}건")
    train_end_kst = train["observed_at"].max().tz_convert("Asia/Seoul")
    test_start_kst = test["observed_at"].min().tz_convert("Asia/Seoul")
    print(f"학습 종료(KST): {train_end_kst}")
    print(f"테스트 시작(KST): {test_start_kst}")
    print(f"날씨 결합률: {data['weather_available'].mean() * 100:.2f}%")
    print(f"입력 변수 ({len(FEATURES)}개): {', '.join(FEATURES)}")
    print(f"목표변수: {TARGET}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    all_metrics: dict[str, dict[str, float]] = {}
    within_10_metrics: dict[str, dict[str, float]] = {}
    low_seat_classification: dict[str, dict[str, float | int]] = {}
    full_bus_classification: dict[str, dict[str, float | int]] = {}
    full_bus_threshold_sweep: dict[str, dict[str, float | int]] = {}
    predictions = test[
        [
            "observed_at",
            "route_id",
            "vehicle_id",
            "station_seq",
            "next_station_seq",
            TARGET,
        ]
    ].copy()
    within_10_mask = test[TARGET].between(0, 10)
    print(
        f"저잔여석 테스트(실제 0~10석): {within_10_mask.sum():,}건 "
        f"({within_10_mask.mean() * 100:.2f}%)"
    )
    for name, model in models.items():
        model.fit(train[FEATURES], train[TARGET])
        prediction = np.clip(model.predict(test[FEATURES]), 0, None)
        metrics = evaluate(test[TARGET], prediction)
        all_metrics[name] = metrics
        within_10_metrics[name] = evaluate(
            test.loc[within_10_mask, TARGET], prediction[within_10_mask.to_numpy()]
        )
        low_seat_classification[name] = evaluate_binary_classification(
            np.asarray(test[TARGET]) <= LOW_SEAT_THRESHOLD,
            prediction <= LOW_SEAT_THRESHOLD,
        )
        full_bus_classification[name] = evaluate_binary_classification(
            np.asarray(test[TARGET]) == 0,
            prediction < 0.5,
        )
        if name.startswith("Ridge"):
            full_bus_threshold_sweep = {
                f"{threshold:.1f}": evaluate_binary_classification(
                    np.asarray(test[TARGET]) == 0,
                    prediction < threshold,
                )
                for threshold in FULL_BUS_THRESHOLDS
            }
        print_model_report(name, model, metrics)
        slug = "ridge"
        predictions[f"{slug}_prediction"] = prediction.round(3)
        predictions[f"{slug}_low_seat_prediction"] = (
            prediction <= LOW_SEAT_THRESHOLD
        ).astype(int)
        predictions[f"{slug}_full_bus_prediction"] = (prediction < 0.5).astype(int)
        joblib.dump(model, args.output_dir / f"{slug}_model.joblib")
        with (args.output_dir / f"{slug}_model.pkl").open("wb") as model_file:
            pickle.dump(model, model_file, protocol=pickle.HIGHEST_PROTOCOL)

    print(f"\n{'=' * 72}\n실제 잔여좌석 0~10석 테스트만 별도 평가\n{'=' * 72}")
    for name, metrics in within_10_metrics.items():
        print(f"\n{name}")
        for key, value in metrics.items():
            suffix = "%" if "accuracy" in key.lower() or "mape" in key.lower() else ""
            shown = value * 100 if suffix else value
            print(f"  {key:<28} {shown:>12.4f}{suffix}")

    classification_groups = (
        (
            f"잔여좌석 {LOW_SEAT_THRESHOLD}석 이하 위험 분류 평가",
            low_seat_classification,
        ),
        ("만석(실제 0석, 예측 0.5석 미만) 분류 평가", full_bus_classification),
    )
    for title, group_metrics in classification_groups:
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
        for name, metrics in group_metrics.items():
            print(f"\n{name}")
            for key, value in metrics.items():
                if key in {"Accuracy", "Precision", "Recall", "F1-score"}:
                    print(f"  {key:<28} {float(value) * 100:>11.4f}%")
                else:
                    print(f"  {key:<28} {int(value):>12,}")

    print(f"\n{'=' * 72}\nRidge 만석 경고 임계값 비교\n{'=' * 72}")
    for threshold, metrics in full_bus_threshold_sweep.items():
        print(f"\n예측값 < {threshold}석")
        for key, value in metrics.items():
            if key in {"Accuracy", "Precision", "Recall", "F1-score"}:
                print(f"  {key:<28} {float(value) * 100:>11.4f}%")
            else:
                print(f"  {key:<28} {int(value):>12,}")

    report = {
        "problem": "next-station arrival remaining seats regression",
        "features": FEATURES,
        "target": TARGET,
        "rows": {"raw": len(history), "supervised": len(data), "train": len(train), "test": len(test)},
        "split": {"train_end_kst": train_end_kst.isoformat(), "test_start_kst": test_start_kst.isoformat()},
        "weather_coverage": round(float(data["weather_available"].mean()), 6),
        "parameters": {name: printable_params(model) for name, model in models.items()},
        "metrics": all_metrics,
        "within_10_test": {
            "definition": "0 <= actual arrival_remaining_seats <= 10",
            "rows": int(within_10_mask.sum()),
            "rate": round(float(within_10_mask.mean()), 6),
            "metrics": within_10_metrics,
        },
        "low_seat_classification": {
            "definition": (
                f"positive when arrival_remaining_seats <= {LOW_SEAT_THRESHOLD}"
            ),
            "threshold": LOW_SEAT_THRESHOLD,
            "metrics": low_seat_classification,
        },
        "full_bus_classification": {
            "definition": (
                "positive when actual arrival_remaining_seats == 0; "
                "predicted positive when regression prediction < 0.5"
            ),
            "actual_threshold": 0,
            "prediction_threshold": 0.5,
            "metrics": full_bus_classification,
        },
        "full_bus_threshold_sweep": {
            "actual_positive": "arrival_remaining_seats == 0",
            "prediction_rule": "regression prediction < threshold",
            "thresholds": full_bus_threshold_sweep,
        },
    }
    (args.output_dir / "model_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    predictions["actual_low_seat"] = (
        predictions[TARGET] <= LOW_SEAT_THRESHOLD
    ).astype(int)
    predictions["actual_full_bus"] = (predictions[TARGET] == 0).astype(int)
    predictions.to_csv(args.output_dir / "predictions.csv", index=False, encoding="utf-8-sig")
    predictions.loc[within_10_mask].to_csv(
        args.output_dir / "predictions_within_10.csv", index=False, encoding="utf-8-sig"
    )

    fig, ax = plt.subplots(figsize=(11, 5))
    chart = predictions.tail(min(500, len(predictions)))
    ax.plot(chart[TARGET].to_numpy(), label="Actual", linewidth=1.2)
    ax.plot(chart["ridge_prediction"].to_numpy(), label="Ridge", linewidth=1)
    ax.set(title="Next-station remaining seats: actual vs Ridge", ylabel="Seats")
    ax.legend()
    fig.tight_layout()
    fig.savefig(args.output_dir / "model_comparison.png", dpi=150)
    plt.close(fig)
    print(f"\n결과 저장 위치: {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
