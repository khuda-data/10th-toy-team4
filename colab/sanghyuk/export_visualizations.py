"""GitHub 가이드에 삽입할 Ridge 분석 그래프를 PNG로 저장한다."""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/10th-toy-team4-matplotlib")

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = PROJECT_ROOT / "data/analysis/models"
OUTPUT_DIR = Path(__file__).resolve().parent / "visualizations"
TARGET = "arrival_remaining_seats"


def save(fig: plt.Figure, filename: str) -> None:
    fig.tight_layout()
    path = OUTPUT_DIR / filename
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"saved: {path}")


def load_data() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    predictions = pd.read_csv(MODEL_DIR / "predictions.csv")
    predictions["observed_at"] = pd.to_datetime(
        predictions["observed_at"], errors="coerce", utc=True
    )
    predictions["observed_at_kst"] = predictions["observed_at"].dt.tz_convert(
        "Asia/Seoul"
    )
    predictions["hour"] = predictions["observed_at_kst"].dt.hour
    predictions["error"] = predictions[TARGET] - predictions["ridge_prediction"]
    predictions["absolute_error"] = predictions["error"].abs()

    route_names = pd.read_csv(
        PROJECT_ROOT / "sanghyuk/route_names.csv", dtype={"route_id": str}
    ).dropna(subset=["route_name"])
    name_map = route_names.set_index("route_id")["route_name"].astype(str)
    history = pd.read_csv(
        PROJECT_ROOT / "data/csv/history_all.csv",
        usecols=["observed_at", "route_id", "remaining_seats"],
        dtype={"route_id": str},
    )
    history["observed_at"] = pd.to_datetime(
        history["observed_at"], errors="coerce", utc=True
    )
    history["remaining_seats"] = pd.to_numeric(
        history["remaining_seats"], errors="coerce"
    )
    history = history.dropna(subset=["observed_at", "route_id", "remaining_seats"])
    history = history[history["remaining_seats"] >= 0].copy()
    history["route_name"] = history["route_id"].map(name_map)
    if history["route_name"].isna().any():
        missing = sorted(history.loc[history["route_name"].isna(), "route_id"].unique())
        raise ValueError(f"route_name 매핑 누락: {', '.join(missing)}")
    history["hour"] = history["observed_at"].dt.tz_convert("Asia/Seoul").dt.hour
    history["low_seat"] = history["remaining_seats"].le(10).astype(int)

    report = json.loads(
        (MODEL_DIR / "model_report.json").read_text(encoding="utf-8")
    )
    return predictions, history, report


def heatmap(table: pd.DataFrame, *, title: str, colorbar: str, filename: str,
            vmin: float | None = None, vmax: float | None = None) -> None:
    values = np.ma.masked_invalid(table.to_numpy(dtype=float))
    fig, ax = plt.subplots(figsize=(14, max(6, 0.42 * len(table))))
    image = ax.imshow(values, aspect="auto", cmap="YlOrRd", vmin=vmin, vmax=vmax)
    ax.set(
        title=title,
        xlabel="Hour (KST)",
        ylabel="Route name",
        xticks=np.arange(24),
        yticks=np.arange(len(table)),
        yticklabels=table.index.astype(str),
    )
    fig.colorbar(image, ax=ax, label=colorbar)
    save(fig, filename)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    predictions, history, report = load_data()

    sample = predictions.sample(min(20_000, len(predictions)), random_state=42)
    limit = max(sample[TARGET].max(), sample["ridge_prediction"].max())
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.scatter(sample[TARGET], sample["ridge_prediction"], s=8, alpha=0.2)
    ax.plot([0, limit], [0, limit], "r--", label="Perfect prediction")
    ax.set(title="Actual vs Ridge prediction", xlabel="Actual seats", ylabel="Predicted seats")
    ax.legend()
    ax.grid(alpha=0.2)
    save(fig, "01_actual_vs_prediction.png")

    predictions["seat_range"] = pd.cut(
        predictions[TARGET], [-0.001, 5, 10, 20, np.inf], labels=["0-5", "6-10", "11-20", "21+"]
    )
    range_mae = predictions.groupby("seat_range", observed=False)["absolute_error"].mean()
    fig, ax = plt.subplots(figsize=(8, 4))
    range_mae.plot(kind="bar", ax=ax, color="#4C78A8", rot=0)
    ax.set(title="MAE by actual remaining-seat range", xlabel="Actual seat range", ylabel="MAE")
    ax.grid(axis="y", alpha=0.2)
    save(fig, "02_mae_by_seat_range.png")

    sweep = pd.DataFrame(report["full_bus_threshold_sweep"]["thresholds"]).T.astype(float)
    sweep.index = sweep.index.astype(float)
    fig, ax = plt.subplots(figsize=(8, 5))
    (sweep[["Precision", "Recall", "F1-score"]] * 100).plot(marker="o", ax=ax)
    ax.set(title="Full-bus warning scores", xlabel="Prediction threshold", ylabel="Score (%)",
           xticks=sweep.index, ylim=(0, 100))
    ax.grid(alpha=0.2)
    save(fig, "03_threshold_scores.png")

    fig, ax = plt.subplots(figsize=(8, 5))
    sweep[["False positive", "False negative"]].rename(
        columns={"False positive": "False alarm (FP)", "False negative": "Missed full bus (FN)"}
    ).plot(kind="bar", ax=ax, rot=0, color=["#F58518", "#E45756"])
    ax.set(title="False alarms and missed full buses", xlabel="Prediction threshold", ylabel="Cases")
    ax.grid(axis="y", alpha=0.2)
    save(fig, "04_threshold_fp_fn.png")

    timeline = predictions.sort_values("observed_at_kst").tail(500)
    fig, ax = plt.subplots(figsize=(14, 5))
    ax.plot(timeline["observed_at_kst"], timeline[TARGET], label="Actual", linewidth=1.2)
    ax.plot(timeline["observed_at_kst"], timeline["ridge_prediction"], label="Ridge", linewidth=1)
    ax.set(title="Actual and predicted seats over time", xlabel="Observed time (KST)", ylabel="Seats")
    ax.legend()
    ax.grid(alpha=0.2)
    fig.autofmt_xdate()
    save(fig, "05_prediction_timeline.png")

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.hist(predictions["error"], bins=80, color="#4C78A8", alpha=0.85)
    ax.axvline(0, color="red", linestyle="--")
    ax.set(title="Prediction error distribution", xlabel="Actual - prediction", ylabel="Rows")
    ax.grid(axis="y", alpha=0.2)
    save(fig, "06_error_distribution.png")

    hourly_mae = predictions.groupby("hour")["absolute_error"].mean()
    fig, ax = plt.subplots(figsize=(10, 4))
    hourly_mae.plot(marker="o", ax=ax)
    ax.set(title="MAE by hour", xlabel="Hour (KST)", ylabel="MAE", xticks=range(24))
    ax.grid(alpha=0.2)
    save(fig, "07_hourly_mae.png")

    grouped = history.groupby(["route_name", "hour"])["low_seat"].agg(["sum", "count"])
    counts = grouped["sum"].unstack(fill_value=0).reindex(columns=range(24), fill_value=0)
    rates = (grouped["sum"] / grouped["count"] * 100).unstack().reindex(columns=range(24))
    heatmap(counts, title="Low-seat observations by route and hour",
            colorbar="Low-seat rows", filename="08_route_hour_low_seat_count.png")
    heatmap(rates, title="Low-seat rate by route and hour",
            colorbar="Low-seat rate (%)", filename="09_route_hour_low_seat_rate.png", vmin=0, vmax=100)

    route_names = list(rates.index)
    columns = 3
    rows = int(np.ceil(len(route_names) / columns))
    fig, axes = plt.subplots(
        rows,
        columns,
        figsize=(15, max(4, rows * 3)),
        sharex=True,
        sharey=True,
    )
    axes = np.atleast_1d(axes).ravel()
    for ax, route_name in zip(axes, route_names):
        values = rates.loc[route_name]
        ax.plot(values.index, values.values, marker="o", markersize=3, linewidth=1.2)
        ax.set_title(str(route_name))
        ax.set_xticks(range(0, 24, 3))
        ax.set_ylim(0, 100)
        ax.grid(alpha=0.2)
    for ax in axes[len(route_names):]:
        ax.set_visible(False)
    fig.supxlabel("Hour (KST)")
    fig.supylabel("Low-seat rate (%)")
    fig.suptitle("Hourly low-seat rate for each route", y=1.01)
    save(fig, "10_route_hour_low_seat_lines.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
