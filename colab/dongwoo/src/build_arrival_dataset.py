"""
팀 실수집 이력(team_history.csv) -> 도착 잔여좌석 예측용 학습 테이블.

라벨 정의(중요):
  사용자가 정류장에서 실제로 마주치는 좌석은 "승객을 태운 뒤 출발 좌석"이 아니라
  "태우기 전 도착 좌석"이다. GBIS stateCd는 0=운행중, 1=정류장 도착, 2=정류장 출발이므로
    A등급: 목표 정류장 방문 중 stateCd==1 의 첫 관측 잔여좌석   (직접 관측)
    B등급: A가 누락된 경우, 같은 운행의 바로 직전 정류장 stateCd==2 출발 좌석 (10분 이내)
  로 arrival_seats를 만든다. 두 등급만 회귀 학습/평가에 사용한다.

한 도착 사건(event)에 대해, 그 사건 이전의 상류 정류장 스냅샷마다 한 행을 만든다.
즉 "지금 N정거장 전에서 보고 있다" 라는 모든 시점이 학습 샘플이 된다.

출력: data/arrival_pairs.csv
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 대한민국 공휴일(수집 기간 전후). 대체공휴일 포함.
# 8/15 광복절이 토요일이라 8/17 월요일이 대체공휴일이 되었고, 이 날의 출퇴근 수요는
# 평일과 전혀 달라 별도 피처 없이는 모델이 구분할 방법이 없다.
PUBLIC_HOLIDAYS = {
    "2026-08-15",  # 광복절
    "2026-08-17",  # 광복절 대체공휴일
    "2026-09-24", "2026-09-25", "2026-09-26",  # 추석 연휴
    "2026-10-03", "2026-10-05",  # 개천절 + 대체공휴일
    "2026-10-09",  # 한글날
}

VISIT_GAP_MIN = 20      # 같은 정류장 방문으로 묶는 최대 공백
TRIP_GAP_MIN = 30       # 새 운행으로 끊는 공백
FALLBACK_GAP_MIN = 10   # B등급 라벨 허용 공백
MAX_STOP_GAP = 20       # 최대 예측 지평(정거장)


# --------------------------------------------------------------------------
# 1. 적재
# --------------------------------------------------------------------------
def load_raw() -> tuple[pd.DataFrame, pd.DataFrame]:
    hist = pd.read_csv(
        DATA_DIR / "team_history.csv",
        dtype={"route_id": str, "vehicle_id": str, "station_id": str, "plate_no": str},
    )
    stations = pd.read_csv(DATA_DIR / "team_stations.csv", dtype={"route_id": str, "station_id": str})
    hist["observed_at"] = pd.to_datetime(hist["observed_at"], format="ISO8601")
    for col in ("station_seq", "remaining_seats", "state_code", "low_plate", "crowded"):
        hist[col] = pd.to_numeric(hist[col], errors="coerce")
    hist = hist.dropna(subset=["station_seq", "vehicle_id", "observed_at"])
    hist["station_seq"] = hist["station_seq"].astype(int)
    # 중복 폴링 제거(같은 차량·같은 시각)
    hist = hist.drop_duplicates(subset=["route_id", "vehicle_id", "observed_at"])
    return hist, stations


def infer_turnaround(stations_of_route: pd.DataFrame) -> int:
    """기점에서 직선거리가 가장 먼 정류장을 회차점으로 근사한다."""
    ordered = stations_of_route.sort_values("station_seq")
    valid = ordered.dropna(subset=["x", "y"])
    if len(valid) < 3:
        return int(round(float(ordered["station_seq"].max()) / 2))
    lat0, lon0 = math.radians(float(valid.iloc[0]["y"])), math.radians(float(valid.iloc[0]["x"]))
    lat = np.radians(valid["y"].astype(float).to_numpy())
    lon = np.radians(valid["x"].astype(float).to_numpy())
    hav = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat0) * np.cos(lat) * np.sin((lon - lon0) / 2) ** 2
    return int(valid.iloc[int(np.argmax(hav))]["station_seq"])


# --------------------------------------------------------------------------
# 2. 정류장 방문(visit) 단위로 압축 + 도착 라벨 생성
# --------------------------------------------------------------------------
def build_visits(hist: pd.DataFrame) -> pd.DataFrame:
    rows = hist.sort_values(["route_id", "vehicle_id", "observed_at"]).copy()
    g = rows.groupby(["route_id", "vehicle_id"], sort=False)
    prev_seq = g["station_seq"].shift()
    prev_time = g["observed_at"].shift()
    gap = (rows["observed_at"] - prev_time).dt.total_seconds() / 60
    rows["new_visit"] = prev_seq.isna() | rows["station_seq"].ne(prev_seq) | gap.gt(VISIT_GAP_MIN)
    rows["visit_no"] = rows.groupby(["route_id", "vehicle_id"], sort=False)["new_visit"].cumsum()

    keys = ["route_id", "vehicle_id", "visit_no"]
    visits = rows.groupby(keys, sort=False).agg(
        station_id=("station_id", "first"),
        station_seq=("station_seq", "first"),
        first_seen=("observed_at", "first"),
        last_seen=("observed_at", "last"),
        first_seats=("remaining_seats", "first"),
        last_seats=("remaining_seats", "last"),
        low_plate=("low_plate", "first"),
        crowded=("crowded", "first"),
        samples=("observed_at", "size"),
    ).reset_index()

    # stateCd==1(도착) 첫 관측 / stateCd==2(출발) 마지막 관측
    arr = (rows[(rows["state_code"] == 1) & (rows["remaining_seats"] >= 0)]
           .groupby(keys, sort=False).head(1)[keys + ["remaining_seats", "observed_at"]]
           .rename(columns={"remaining_seats": "obs_arrival_seats", "observed_at": "arrival_seen"}))
    dep = (rows[(rows["state_code"] == 2) & (rows["remaining_seats"] >= 0)]
           .groupby(keys, sort=False).tail(1)[keys + ["remaining_seats", "observed_at"]]
           .rename(columns={"remaining_seats": "departure_seats", "observed_at": "departure_seen"}))
    visits = visits.merge(arr, on=keys, how="left").merge(dep, on=keys, how="left")
    visits = visits.sort_values(["route_id", "vehicle_id", "visit_no"]).reset_index(drop=True)

    # 운행(trip) 분리
    gv = visits.groupby(["route_id", "vehicle_id"], sort=False)
    p_seq = gv["station_seq"].shift()
    p_time = gv["last_seen"].shift()
    trip_gap = (visits["first_seen"] - p_time).dt.total_seconds() / 60
    new_trip = p_seq.isna() | visits["station_seq"].le(p_seq) | trip_gap.gt(TRIP_GAP_MIN)
    visits["trip_no"] = new_trip.groupby([visits["route_id"], visits["vehicle_id"]], sort=False).cumsum()
    visits["trip_id"] = (visits["route_id"] + "-" + visits["vehicle_id"]
                         + "-" + visits["trip_no"].astype(int).astype(str))

    # 도착 좌석 라벨 (A: 직접 관측, B: 직전 정류장 출발좌석으로 보완)
    gv = visits.groupby(["route_id", "vehicle_id"], sort=False)
    prev_trip = gv["trip_id"].shift()
    prev_seq2 = gv["station_seq"].shift()
    prev_dep = gv["departure_seats"].shift()
    prev_dep_seen = gv["departure_seen"].shift()
    prev_gap = (visits["first_seen"] - prev_dep_seen).dt.total_seconds() / 60

    direct = visits["obs_arrival_seats"].notna()
    inferred = (~direct) & prev_trip.eq(visits["trip_id"]) \
        & visits["station_seq"].eq(prev_seq2 + 1) & prev_dep.ge(0) \
        & prev_gap.between(0, FALLBACK_GAP_MIN)

    visits["arrival_seats"] = np.where(direct, visits["obs_arrival_seats"],
                                       np.where(inferred, prev_dep, np.nan))
    visits["label_quality"] = pd.Series(
        np.where(direct, "A", np.where(inferred, "B", None)), index=visits.index, dtype="object")
    # tz-aware 컬럼끼리 합쳐야 dtype이 깨지지 않는다.
    visits["event_time"] = visits["arrival_seen"].where(direct).fillna(
        visits["first_seen"].where(inferred))
    return visits


# --------------------------------------------------------------------------
# 3. (상류 스냅샷 x 도착 사건) 쌍 생성 — 벡터화 merge 방식
# --------------------------------------------------------------------------
def build_pairs(visits: pd.DataFrame, capacity: pd.Series,
                qualities: tuple[str, ...] = ("A", "B")) -> pd.DataFrame:
    """같은 trip 안에서 station_seq가 더 작은 모든 방문을 스냅샷으로 붙인다.

    B등급 라벨은 '직전 정류장의 출발 좌석'을 그대로 정답으로 쓰므로, 그 직전
    정류장 스냅샷(stop_gap==1)을 입력으로 주면 정답을 그대로 보고 맞추는
    누수가 된다. 해당 조합은 명시적으로 제거한다.
    """
    events = visits[visits["label_quality"].isin(qualities)].copy()
    events = events.dropna(subset=["event_time", "arrival_seats"])
    events["event_id"] = events["trip_id"] + ":" + events["station_seq"].astype(str)

    ev = events[["event_id", "route_id", "trip_id", "vehicle_id", "station_seq",
                 "event_time", "arrival_seats", "label_quality"]].rename(
        columns={"station_seq": "target_seq"})

    snap = visits[["trip_id", "station_seq", "last_seen", "last_seats",
                   "low_plate", "crowded", "samples"]].rename(
        columns={"station_seq": "snap_seq", "last_seen": "snap_time",
                 "last_seats": "snap_seats"})
    snap = snap[snap["snap_seats"].ge(0)]

    # 노선별로 나눠 merge 해서 중간 결과 메모리를 억제한다.
    out = []
    for rid, ev_r in ev.groupby("route_id", sort=False):
        snap_r = snap[snap["trip_id"].isin(ev_r["trip_id"].unique())]
        p = ev_r.merge(snap_r, on="trip_id", how="inner")
        gap = p["target_seq"] - p["snap_seq"]
        p = p[gap.gt(0) & gap.le(MAX_STOP_GAP) & p["snap_time"].lt(p["event_time"])]
        out.append(p)
    pairs = pd.concat(out, ignore_index=True)

    pairs["stop_gap"] = pairs["target_seq"] - pairs["snap_seq"]
    # B등급 라벨의 정답 출처가 되는 스냅샷 제거 (누수 차단)
    pairs = pairs[~(pairs["label_quality"].eq("B") & pairs["stop_gap"].eq(1))]

    pairs["minutes_to_arrival"] = (pairs["event_time"] - pairs["snap_time"]).dt.total_seconds() / 60
    pairs = pairs[pairs["minutes_to_arrival"].between(0.1, 180)]
    pairs["capacity"] = pairs["vehicle_id"].map(capacity)
    pairs = pairs.dropna(subset=["capacity"])
    pairs = pairs[pairs["arrival_seats"].le(pairs["capacity"])]
    return pairs.sort_values(["event_time", "event_id", "snap_time"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# 4. 피처
# --------------------------------------------------------------------------
def add_features(pairs: pd.DataFrame, stations: pd.DataFrame,
                 turnaround: dict, max_seq: dict) -> pd.DataFrame:
    df = pairs
    df["date"] = df["event_time"].dt.date.astype(str)
    df["hour"] = df["snap_time"].dt.hour
    minute = df["snap_time"].dt.hour * 60 + df["snap_time"].dt.minute
    ang = 2 * np.pi * minute / 1440
    df["time_sin"], df["time_cos"] = np.sin(ang), np.cos(ang)
    df["dow"] = df["snap_time"].dt.dayofweek
    df["is_weekend"] = df["dow"].ge(5).astype(int)

    # --- 달력 피처 ---
    # 연휴에는 출퇴근 수요 자체가 사라져 좌석 소진 패턴이 평일과 완전히 다르다.
    # is_weekend 만으로는 대체공휴일(월요일)을 평일과 구분할 수 없다.
    d = pd.to_datetime(df["date"])
    hol = pd.to_datetime(sorted(PUBLIC_HOLIDAYS))
    df["is_holiday"] = df["date"].isin(PUBLIC_HOLIDAYS).astype(int)
    # 평일처럼 보이지만 실제로는 쉬는 날 (주말 + 공휴일)
    df["is_off_day"] = (df["is_weekend"] | df["is_holiday"]).astype(int)
    # 연휴 전후는 조기 퇴근·여행 수요로 평소와 다르다
    df["is_day_before_off"] = (d + pd.Timedelta(days=1)).isin(
        list(hol) + list(d[df["is_weekend"].eq(1)].unique())).astype(int)
    df["is_day_after_off"] = (d - pd.Timedelta(days=1)).isin(
        list(hol) + list(d[df["is_weekend"].eq(1)].unique())).astype(int)

    # 러시아워는 쉬는 날에는 존재하지 않으므로 off_day와 결합해 정의한다
    df["is_rush_am"] = (df["hour"].between(6, 9) & df["is_off_day"].eq(0)).astype(int)
    df["is_rush_pm"] = (df["hour"].between(17, 20) & df["is_off_day"].eq(0)).astype(int)

    df["turnaround"] = df["route_id"].map(turnaround)
    df["max_seq"] = df["route_id"].map(max_seq)
    df["is_return"] = df["target_seq"].gt(df["turnaround"]).astype(int)
    to_city = df["target_seq"] / df["turnaround"].clip(lower=1)
    ret = (df["max_seq"] - df["target_seq"]) / (df["max_seq"] - df["turnaround"]).clip(lower=1)
    df["target_progress"] = np.where(df["is_return"] == 1, ret, to_city).clip(0, 1)
    df["snap_progress"] = (df["snap_seq"] / df["max_seq"]).clip(0, 1)

    # 좌석/적재 관련
    df["load_ratio"] = 1 - df["snap_seats"] / df["capacity"]
    df["seats_per_stop"] = df["snap_seats"] / df["stop_gap"]
    df["load_gap_inter"] = df["load_ratio"] * df["stop_gap"]
    df["currently_low_5"] = df["snap_seats"].le(5).astype(int)
    df["currently_low_10"] = df["snap_seats"].le(10).astype(int)

    # 같은 사건 내 최근 추세(스냅샷 시퀀스 기반)
    grp = df.groupby("event_id", sort=False)
    df["seat_delta"] = grp["snap_seats"].diff()
    moved = grp["snap_seq"].diff()
    df["seat_change_per_stop"] = df["seat_delta"] / moved.where(moved.gt(0))
    df["min_per_stop"] = (grp["snap_time"].diff().dt.total_seconds() / 60) / moved.where(moved.gt(0))
    # groupby.rolling 은 Cython 구현이라 transform(lambda) 보다 훨씬 빠르다.
    df["trend3"] = (df.groupby("event_id", sort=False)["seat_change_per_stop"]
                    .rolling(3, min_periods=1).mean().reset_index(level=0, drop=True))
    df["min_per_stop3"] = (df.groupby("event_id", sort=False)["min_per_stop"]
                           .rolling(3, min_periods=1).median().reset_index(level=0, drop=True))
    df["est_minutes"] = df["min_per_stop3"] * df["stop_gap"]
    df["projected_seats"] = (df["snap_seats"] + df["trend3"] * df["stop_gap"]).clip(0, None)

    # 정류장 좌표
    st = stations[["route_id", "station_seq", "x", "y"]].rename(columns={"station_seq": "target_seq"})
    st = st.drop_duplicates(subset=["route_id", "target_seq"])
    df = df.merge(st, on=["route_id", "target_seq"], how="left")
    return df


def add_historical_features(df: pd.DataFrame, train_mask: pd.Series) -> pd.DataFrame:
    """학습기간 데이터만으로 과거 통계 피처를 만들어 누수를 막는다."""
    tr = df.loc[train_mask].drop_duplicates(subset=["event_id"])
    tr = tr.assign(tb=tr["hour"])

    def _agg(keys, name):
        g = tr.groupby(keys)["arrival_seats"].agg(["mean", "size"]).reset_index()
        g = g[g["size"] >= 5]
        return g.rename(columns={"mean": name})[keys + [name]]

    df["tb"] = df["hour"]
    for keys, name in [
        (["route_id", "target_seq", "tb"], "hist_seat_stop_hour"),
        (["route_id", "target_seq"], "hist_seat_stop"),
        (["route_id", "tb"], "hist_seat_route_hour"),
    ]:
        df = df.merge(_agg(keys, name), on=keys, how="left")

    # 저잔여(<=5) 발생률
    tr2 = tr.assign(low=tr["arrival_seats"].le(5).astype(float))
    g = tr2.groupby(["route_id", "target_seq", "tb"])["low"].agg(["mean", "size"]).reset_index()
    g = g[g["size"] >= 5].rename(columns={"mean": "hist_low_rate"})
    df = df.merge(g[["route_id", "target_seq", "tb", "hist_low_rate"]],
                  on=["route_id", "target_seq", "tb"], how="left")

    df["hist_seat_stop_hour"] = df["hist_seat_stop_hour"].fillna(df["hist_seat_stop"])
    df["hist_seat_stop_hour"] = df["hist_seat_stop_hour"].fillna(df["hist_seat_route_hour"])
    return df.drop(columns=["tb"])


def add_previous_bus(df: pd.DataFrame, visits: pd.DataFrame) -> pd.DataFrame:
    """목표 정류장에 '직전에 다녀간 다른 버스'의 도착 좌석.

    앞차가 만석으로 지나갔다면 그 정류장에는 못 탄 승객이 남아 있고, 다음 차는
    평소보다 빨리 찬다. 이 이월 효과를 모델에 알려주는 피처다.

    누수 방지: 스냅샷 시각(snap_time)보다 '엄격히 이전'에 발생한 도착만 참조한다.
    실제 서비스에서도 그 시점에 이미 관측된 정보이므로 사용해도 정당하다.
    """
    lookup = visits.loc[
        visits["label_quality"].isin(["A", "B"]),
        ["route_id", "station_seq", "event_time", "arrival_seats"],
    ].dropna(subset=["event_time", "arrival_seats"]).copy()
    lookup = lookup.rename(columns={
        "station_seq": "target_seq",
        "event_time": "prev_bus_time",
        "arrival_seats": "prev_bus_seats",
    }).sort_values("prev_bus_time")

    left = df.sort_values("snap_time").reset_index(drop=True)
    merged = pd.merge_asof(
        left, lookup,
        left_on="snap_time", right_on="prev_bus_time",
        by=["route_id", "target_seq"],
        direction="backward", allow_exact_matches=False,
    )
    merged["prev_bus_age_min"] = (
        merged["snap_time"] - merged["prev_bus_time"]
    ).dt.total_seconds() / 60
    # 너무 오래된 참조는 무의미하므로 버린다(배차 간격을 크게 넘는 경우)
    stale = merged["prev_bus_age_min"].gt(90)
    merged.loc[stale, ["prev_bus_seats", "prev_bus_age_min"]] = np.nan
    merged["prev_bus_was_full"] = merged["prev_bus_seats"].le(0).astype(float)
    merged.loc[merged["prev_bus_seats"].isna(), "prev_bus_was_full"] = np.nan
    merged["prev_bus_was_low"] = merged["prev_bus_seats"].le(5).astype(float)
    merged.loc[merged["prev_bus_seats"].isna(), "prev_bus_was_low"] = np.nan
    return merged.drop(columns=["prev_bus_time"])


def add_segment_consumption(df: pd.DataFrame, visits: pd.DataFrame,
                            train_dates: set) -> pd.DataFrame:
    """구간별 예상 좌석 소진량.

    핵심 아이디어: 타깃이 '현재 대비 변화량'이므로, 남은 구간의 정류장들에서
    평소 몇 석씩 소진되는지를 직접 더해 주면 모델이 물리적으로 옳은 사전값을
    갖게 된다. 정류장 단위 평균을 누적합으로 만들어
        expected_drop = cum[target_seq] - cum[snap_seq]
    로 계산한다. 통계는 학습기간 관측만으로 만들어 누수를 막는다.
    """
    v = visits.copy()
    v["date"] = v["first_seen"].dt.date.astype(str)
    v = v[v["date"].isin(train_dates)]
    # 한 정류장에서의 순 좌석 변화(음수 = 승객 순증)
    change = v["departure_seats"] - v["obs_arrival_seats"]
    fallback = v["last_seats"] - v["first_seats"]
    v["seat_change"] = change.fillna(fallback)
    v = v.dropna(subset=["seat_change"])
    v["hour"] = v["first_seen"].dt.hour
    v["is_weekend"] = v["first_seen"].dt.dayofweek.ge(5).astype(int)

    def cumulative(keys: list[str], min_size: int) -> pd.DataFrame:
        stat = (v.groupby(keys + ["station_seq"])["seat_change"]
                .agg(["mean", "size"]).reset_index())
        stat = stat[stat["size"] >= min_size].rename(columns={"mean": "stop_change"})
        stat = stat.sort_values(keys + ["station_seq"])
        stat["cum"] = stat.groupby(keys)["stop_change"].cumsum()
        return stat[keys + ["station_seq", "cum"]]

    def apply_cum(df: pd.DataFrame, cum: pd.DataFrame, keys: list[str], suffix: str):
        df = df.merge(cum.rename(columns={"station_seq": "target_seq", "cum": f"t_{suffix}"}),
                      on=keys + ["target_seq"], how="left")
        df = df.merge(cum.rename(columns={"station_seq": "snap_seq", "cum": f"s_{suffix}"}),
                      on=keys + ["snap_seq"], how="left")
        return df

    # 시간대별 통계가 희소하면 시간 무관 통계로 보완한다.
    df = apply_cum(df, cumulative(["route_id", "hour", "is_weekend"], 3),
                   ["route_id", "hour", "is_weekend"], "h")
    df = apply_cum(df, cumulative(["route_id", "is_weekend"], 5),
                   ["route_id", "is_weekend"], "d")

    drop_h = df["t_h"] - df["s_h"]
    drop_d = df["t_d"] - df["s_d"]
    df["expected_drop"] = drop_h.fillna(drop_d)
    df["expected_arrival_seats"] = (df["snap_seats"] + df["expected_drop"]).clip(0, None)
    return df.drop(columns=["t_h", "s_h", "t_d", "s_d"])


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--val-dates", default="2026-08-11,2026-08-12")
    ap.add_argument("--test-dates", default="2026-08-13,2026-08-14")
    # 광복절(8/15 토) + 주말 + 대체공휴일(8/17 월). 출퇴근 수요 구조가 평일과
    # 전혀 달라 평일 test에 섞으면 안 되고, 별도 홀드아웃으로 따로 평가한다.
    ap.add_argument("--holiday-dates", default="2026-08-15,2026-08-16,2026-08-17")
    # 관측이 하루의 일부만 있는 날(수집 시작일/오늘)은 러시아워가 빠져 편향된다.
    ap.add_argument("--exclude-dates", default="2026-08-18")
    args = ap.parse_args()

    hist, stations = load_raw()
    print(f"원천 관측 {len(hist):,}행 / {hist['route_id'].nunique()}노선 "
          f"({hist['observed_at'].min()} ~ {hist['observed_at'].max()})")

    turnaround = {rid: infer_turnaround(g) for rid, g in stations.groupby("route_id")}
    max_seq = stations.groupby("route_id")["station_seq"].max().to_dict()

    visits = build_visits(hist)
    print(f"정류장 방문 {len(visits):,}건, 운행 {visits['trip_id'].nunique():,}개")
    lq = visits["label_quality"].value_counts(dropna=False)
    print(f"라벨 등급 분포:\n{lq.to_string()}")

    # 차량 정원 = 관측된 최대 잔여좌석
    capacity = hist[hist["remaining_seats"].ge(0)].groupby("vehicle_id")["remaining_seats"].max()
    capacity = capacity[capacity.between(20, 80)]

    pairs = build_pairs(visits, capacity)
    print(f"학습 쌍 {len(pairs):,}행 / 도착사건 {pairs['event_id'].nunique():,}개")

    df = add_features(pairs, stations, turnaround, max_seq)

    # 날짜 기준 분할. 평일/연휴를 섞지 않고, 부분 수집일은 제외한다.
    val_d = set(args.val_dates.split(","))
    test_d = set(args.test_dates.split(","))
    holi_d = set(args.holiday_dates.split(","))
    excl_d = set(args.exclude_dates.split(",")) if args.exclude_dates else set()
    df = df[~df["date"].isin(excl_d)]
    df["split"] = np.select(
        [df["date"].isin(test_d), df["date"].isin(val_d), df["date"].isin(holi_d)],
        ["test", "val", "holiday"], default="train")
    print("분할:")
    print(df.drop_duplicates("event_id").groupby(["split", "date"]).size().to_string())

    df = add_historical_features(df, df["split"].eq("train"))
    train_dates = set(df.loc[df["split"].eq("train"), "date"].unique())
    df = add_segment_consumption(df, visits, train_dates)
    df = add_previous_bus(df, visits)
    print(f"expected_drop  결측률: {df['expected_drop'].isna().mean():.1%}")
    print(f"prev_bus_seats 결측률: {df['prev_bus_seats'].isna().mean():.1%}")

    out = DATA_DIR / "arrival_pairs.csv"
    df.to_csv(out, index=False)
    print(f"\nsaved -> {out}")
    print(df.groupby("split").agg(rows=("event_id", "size"), events=("event_id", "nunique")).to_string())


if __name__ == "__main__":
    main()
