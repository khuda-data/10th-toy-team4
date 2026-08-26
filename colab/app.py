import os
import math
import time
import joblib
import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ---------------------------------------------------------
# 1. 설정
# ---------------------------------------------------------
SERVICE_KEY = "UVSE0iEdBQILtOEZOSxI+xtKV7zRfGWK3UVPv9ag/RVZmKvA/0sY5IotKndiQ33ldJoXs1TOrShq/Ls30BBQVA=="
MODEL_PATH = os.environ.get(
    "QUEUE_BOARDING_PKL",
    "/Users/jameskim/Desktop/bus-tracker-6/queue_boarding_v1_0_0.pkl",
)
MODEL_ID = "queue-boarding/v1.0.0"

STATIONS_CACHE = {}          # route_id -> [stations]
VEHICLE_HISTORY = {}         # (route_id, vehId) -> list of dict(ts, seq, seats)
MAX_HISTORY = 12


# ---------------------------------------------------------
# 2. queue-boarding/v1.0.0 포터블 추론 엔진 (노트북 계약 이식)
# ---------------------------------------------------------
def _lookup(profile, target):
    output = pd.DataFrame(index=target.index)
    blocks = {}
    for name in ("cell", "fallback"):
        block = profile[name]
        keys = list(block["keys"])
        index = pd.MultiIndex.from_frame(target[keys])
        blocks[name] = pd.DataFrame(
            {
                "mean": block["mean"].reindex(index).to_numpy(),
                "count": block["count"].reindex(index).to_numpy(),
                "low10": block["low10"].reindex(index).to_numpy(),
            },
            index=target.index,
        )
    mean = blocks["cell"]["mean"].fillna(blocks["fallback"]["mean"])
    mean = mean.fillna(float(profile["global_mean"]))
    count = blocks["cell"]["count"].fillna(blocks["fallback"]["count"]).fillna(0.0)
    low = blocks["cell"]["low10"].fillna(blocks["fallback"]["low10"])
    low = low.fillna(float(profile["global_low10"]))
    current = target["snapshot_remaining_seats"].to_numpy(float)
    output["snapshot_state_expected_seats"] = mean.to_numpy(float)
    output["snapshot_state_seat_deviation"] = current - mean.to_numpy(float)
    output["snapshot_state_low10_rate"] = low.to_numpy(float)
    output["snapshot_state_log_count"] = np.log1p(count.to_numpy(float))
    return output


def _decode_target(raw, data, kind):
    current = data["snapshot_remaining_seats"].to_numpy(float)
    gap = np.maximum(data["target_stop_gap"].to_numpy(float), 1.0)
    if kind == "delta_per_stop":
        prediction = current + np.asarray(raw, float) * gap
    elif kind == "delta_per_sqrt_stop":
        prediction = current + np.asarray(raw, float) * np.sqrt(gap)
    else:
        raise ValueError(f"unsupported point target kind: {kind}")
    return np.clip(prediction, 0, data["capacity"].to_numpy(float))


def _full_probability_matrix(probability, classes, class_count):
    output = np.zeros((len(probability), class_count), dtype=float)
    output[:, np.asarray(classes, dtype=int)] = probability
    return output


def _deterministic_pmf(value, capacity, max_seat):
    output = np.zeros(max_seat + 1, dtype=float)
    seat = int(np.clip(np.rint(value), 0, min(int(round(capacity)), max_seat)))
    output[seat] = 1.0
    return output


def _convolve(seat_pmfs, queue_ahead):
    cumulative = np.asarray([1.0], dtype=float)
    required = int(queue_ahead) + 1
    board = []
    for pmf in seat_pmfs:
        cumulative = np.convolve(cumulative, np.asarray(pmf, dtype=float))
        board.append(
            float(cumulative[required:].sum()) if required < len(cumulative) else 0.0
        )
    board_probability = np.maximum.accumulate(
        np.clip(np.asarray(board, dtype=float), 0.0, 1.0)
    )
    sent = np.concatenate(
        [board_probability[:1], np.diff(board_probability), 1.0 - board_probability[-1:]]
    )
    sent = np.clip(sent, 0.0, 1.0)
    sent /= max(float(sent.sum()), 1e-12)
    return board_probability, sent


class QueueBoardingV1:
    def __init__(self, bundle):
        self.bundle = bundle

    @classmethod
    def load(cls, path):
        bundle = joblib.load(path)
        if bundle.get("model_id") != MODEL_ID or bundle.get("format_version") != 1:
            raise ValueError("unsupported queue-boarding artifact")
        return cls(bundle)

    def prepare_features(self, rows):
        output = rows.copy()
        missing = sorted(
            set(self.bundle["required_prepared_input_features"]) - set(output.columns)
        )
        if missing:
            raise ValueError(f"missing prepared snapshot features: {missing}")
        supported = set(self.bundle["routes"])
        unknown = sorted(set(output["route_id"].astype(str)) - supported)
        if unknown:
            raise ValueError(f"unsupported routes: {unknown}")
        output["route_id"] = output["route_id"].astype(str)
        output["route_code"] = output["route_code"].astype("string")
        for column in self.bundle["categorical_features"]:
            output[column] = output[column].astype("string")
        state = _lookup(self.bundle["state_profile"], output)
        output[list(state.columns)] = state
        return output

    def predict_seat_distributions(self, rows):
        prepared = self.prepare_features(rows)
        columns = list(self.bundle["feature_columns"])
        point = np.zeros(len(prepared), dtype=float)
        for name, model in self.bundle["point"]["models"].items():
            raw = model.predict(prepared[columns])
            decoded = _decode_target(raw, prepared, self.bundle["point"]["target_kinds"][name])
            point += float(self.bundle["point"]["weights"][name]) * decoded
        point = np.clip(point, 0, prepared["capacity"].to_numpy(float))

        distribution = self.bundle["distribution"]
        raw_probability = np.clip(
            distribution["classifier"].predict_proba(prepared[columns]), 1e-9, 1.0
        )
        calibrator = distribution["calibrator"]

        # queue-boarding/v1.0.0이 더 새로운 scikit-learn에서 저장된 경우의 호환 처리
        if not hasattr(calibrator, "multi_class"):
            calibrator.multi_class = "auto"

        bucket_probability = _full_probability_matrix(
            calibrator.predict_proba(np.log(raw_probability)),
            calibrator.classes_,
            int(distribution["hybrid_classes"]),
        )
        max_seat = int(distribution["max_seat"])
        hybrid = np.zeros((len(prepared), max_seat + 1), dtype=float)
        routes = prepared["route_id"].astype(str).to_numpy()
        global_matrix = distribution["global_expansion_matrix"]
        for route_id in np.unique(routes):
            mask = routes == route_id
            matrix = distribution["route_expansion_matrices"].get(route_id, global_matrix)
            hybrid[mask] = bucket_probability[mask] @ matrix
        capacities = prepared["capacity"].round().clip(0, max_seat).astype(int).to_numpy()
        for index, capacity in enumerate(capacities):
            hybrid[index, capacity + 1:] = 0.0
        hybrid /= np.maximum(hybrid.sum(axis=1, keepdims=True), 1e-12)
        weight = float(self.bundle["boarding_policy"]["hybrid_weight"])
        mixed = np.empty_like(hybrid)
        for index, (value, capacity) in enumerate(zip(point, capacities)):
            deterministic = _deterministic_pmf(value, capacity, max_seat)
            mixed[index] = weight * hybrid[index] + (1.0 - weight) * deterministic
        mixed /= np.maximum(mixed.sum(axis=1, keepdims=True), 1e-12)
        return point, mixed, prepared

    def predict_queries(self, rows):
        required = {"query_id", "bus_order", "queue_ahead"}
        missing = sorted(required - set(rows.columns))
        if missing:
            raise ValueError(f"missing query columns: {missing}")
        point, pmfs, prepared = self.predict_seat_distributions(rows)
        prepared = prepared.copy()
        prepared["_position"] = np.arange(len(prepared))
        output = []
        expected_buses = int(self.bundle["boarding_policy"]["max_visible_buses"])
        for query_id, group in prepared.groupby("query_id", sort=False):
            ordered = group.sort_values("bus_order")
            if ordered["bus_order"].astype(int).tolist() != list(range(1, expected_buses + 1)):
                raise ValueError(f"query {query_id!r} must contain bus_order 1..{expected_buses}")
            queues = ordered["queue_ahead"].astype(int).unique()
            if len(queues) != 1 or queues[0] < 0:
                raise ValueError(f"query {query_id!r} has invalid queue_ahead")
            positions = ordered["_position"].to_numpy(int)
            board, sent = _convolve([pmfs[p] for p in positions], int(queues[0]))
            row = {
                "query_id": query_id,
                "queue_ahead": int(queues[0]),
                "predicted_sent_class": int(np.argmax(sent)),
                "expected_sent_buses_truncated": float(np.sum(1.0 - board)),
                "tail_probability_after_3_buses": float(sent[-1]),
                "model_id": self.bundle["model_id"],
            }
            for index in range(expected_buses):
                row[f"board_by_{index + 1}_probability"] = float(board[index])
                row[f"sent_{index}_probability"] = float(sent[index])
                row[f"bus_{index + 1}_point_arrival_seats"] = float(point[positions[index]])
            row["sent_3_plus_probability"] = float(sent[-1])
            output.append(row)
        return pd.DataFrame(output)


MODEL = None
if os.path.exists(MODEL_PATH):
    try:
        MODEL = QueueBoardingV1.load(MODEL_PATH)
        print(f"✅ 모델 로드 성공: {MODEL.bundle['model_id']} "
              f"(지원 노선 {len(MODEL.bundle['routes'])}개)")
    except Exception as e:
        print(f"❌ 모델 로드 오류: {e}")
else:
    print(f"⚠️ 경고: 모델 파일을 찾을 수 없습니다. ({MODEL_PATH})")


# ---------------------------------------------------------
# 3. 실시간 GBIS 응답 → prepared feature 변환
# ---------------------------------------------------------
def _update_history(route_id, bus):
    key = (route_id, bus["vehId"])
    history = VEHICLE_HISTORY.setdefault(key, [])
    now = time.time()
    if not history or history[-1]["seq"] != bus["stationSeq"]:
        history.append({"ts": now, "seq": bus["stationSeq"], "seats": bus["remainSeatCnt"]})
        if len(history) > MAX_HISTORY:
            del history[:len(history) - MAX_HISTORY]
    else:
        history[-1]["seats"] = bus["remainSeatCnt"]


def _history_features(route_id, veh_id):
    """정류장 이동 이력에서 좌석 변화·소요시간 피처를 계산 (없으면 NaN)."""
    history = VEHICLE_HISTORY.get((route_id, veh_id), [])
    out = dict(
        seat_delta_previous_stop=np.nan,
        seat_change_per_stop=np.nan,
        rolling_seat_change_per_stop_3=np.nan,
        minutes_since_previous_stop=np.nan,
        rolling_minutes_per_stop_3=np.nan,
    )
    if len(history) >= 2:
        prev, cur = history[-2], history[-1]
        stops = max(cur["seq"] - prev["seq"], 1)
        out["seat_delta_previous_stop"] = float(cur["seats"] - prev["seats"])
        out["seat_change_per_stop"] = out["seat_delta_previous_stop"] / stops
        out["minutes_since_previous_stop"] = (cur["ts"] - prev["ts"]) / 60.0
        window = history[-4:]
        total_stops = max(window[-1]["seq"] - window[0]["seq"], 1)
        out["rolling_seat_change_per_stop_3"] = (window[-1]["seats"] - window[0]["seats"]) / total_stops
        out["rolling_minutes_per_stop_3"] = ((window[-1]["ts"] - window[0]["ts"]) / 60.0) / total_stops
    return out


def build_feature_row(route_id, bus, target, stations, bus_order, queue_ahead, query_id):
    now = pd.Timestamp.now()
    minutes = now.hour * 60 + now.minute + now.second / 60.0
    angle = 2.0 * math.pi * minutes / 1440.0
    max_seq = max(s["stationSeq"] for s in stations) if stations else 100
    turn_seq = next((s["stationSeq"] for s in stations if s.get("turnYn") == "Y"), max_seq)

    seats = float(max(bus["remainSeatCnt"], 0))
    capacity = 45.0
    ceiling = 70.0 if seats > 44 else 44.0
    gap = float(max(target["stationSeq"] - bus["stationSeq"], 1))
    hist = _history_features(route_id, bus["vehId"])

    projected = np.nan
    est_minutes = np.nan
    if not np.isnan(hist["rolling_seat_change_per_stop_3"]):
        projected = seats + hist["rolling_seat_change_per_stop_3"] * gap
    if not np.isnan(hist["rolling_minutes_per_stop_3"]):
        est_minutes = gap * hist["rolling_minutes_per_stop_3"]

    target_load_ratio = (capacity - seats) / capacity
    row = {
        "route_id": str(route_id),
        "capacity": capacity,
        "snapshot_time_sin": math.sin(angle),
        "snapshot_time_cos": math.cos(angle),
        "route_progress": target["stationSeq"] / max_seq,
        "snapshot_route_progress": bus["stationSeq"] / max_seq,
        "x": target["lng"],
        "y": target["lat"],
        "snapshot_capacity": capacity,
        "target_load_ratio": target_load_ratio,
        "target_stop_gap": gap,
        "snapshot_remaining_seats": seats,
        "seat_delta_previous_stop": hist["seat_delta_previous_stop"],
        "seat_change_per_stop": hist["seat_change_per_stop"],
        "rolling_seat_change_per_stop_3": hist["rolling_seat_change_per_stop_3"],
        "minutes_since_previous_stop": hist["minutes_since_previous_stop"],
        "rolling_minutes_per_stop_3": hist["rolling_minutes_per_stop_3"],
        "estimated_minutes_to_arrival": est_minutes,
        "projected_arrival_seats": projected,
        "seats_per_remaining_stop": seats / gap,
        "load_gap_interaction": target_load_ratio * gap,
        "currently_low_5": float(seats <= 5),
        "currently_low_10": float(seats <= 10),
        "observed_ceiling_capacity": ceiling,
        "observed_ceiling_load_ratio": (ceiling - seats) / ceiling,
        "observed_ceiling_load_gap": (ceiling - seats) / ceiling * gap,
        "target_low_10_rate": np.nan,
        "target_low_rate_log_count": np.nan,
        "path_low_10_mean": np.nan,
        "path_low_10_sum": np.nan,
        "path_flow_mean": np.nan,
        "path_flow_sum": np.nan,
        "path_flow_std": np.nan,
        "path_flow_fallback_share": np.nan,
        "previous_bus_departure_age_minutes": np.nan,
        "station_seq_cat": str(int(target["stationSeq"])),
        "direction": "to_city" if target["stationSeq"] <= turn_seq else "from_city",
        "snapshot_day_of_week": int(now.dayofweek),
        "snapshot_low_plate_cat": str(int(bus.get("lowPlate", 0))),
        "target_state_cat": "0",
        "snapshot_station_seq_cat": str(int(bus["stationSeq"])),
        "snapshot_time_bin_30": str(int(minutes // 30)),
        "route_code": str(route_id),
        "query_id": query_id,
        "bus_order": bus_order,
        "queue_ahead": int(queue_ahead),
    }
    return row


def virtual_bus(stations):
    """관측 버스가 3대 미만일 때 기점에서 새로 출발하는 가상 만석-여유 버스로 패딩."""
    first_seq = min(s["stationSeq"] for s in stations) if stations else 1
    return {
        "vehId": "__virtual__",
        "plateNo": "가상(미출발)",
        "stationSeq": first_seq,
        "remainSeatCnt": 44,
        "lowPlate": 0,
        "virtual": True,
    }


# ---------------------------------------------------------
# 4. API 엔드포인트
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/model_info")
def model_info():
    if MODEL is None:
        return jsonify({"status": "error", "message": "모델이 로드되지 않았습니다."}), 503
    b = MODEL.bundle
    return jsonify({
        "status": "success",
        "data": {
            "model_id": b["model_id"],
            "routes": sorted(b["routes"]),
            "boarding_policy": {
                "hybrid_weight": b["boarding_policy"]["hybrid_weight"],
                "max_visible_buses": b["boarding_policy"]["max_visible_buses"],
            },
        },
    })


@app.route("/api/search_routes")
def search_routes():
    keyword = request.args.get("keyword", "").strip()
    if not keyword:
        return jsonify({"status": "error", "message": "keyword 파라미터가 필요합니다."}), 400
    url = "https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteListv2"
    params = {"serviceKey": SERVICE_KEY, "keyword": keyword}
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        route_list = data.get("response", {}).get("msgBody", {}).get("busRouteList", [])
        if not isinstance(route_list, list):
            route_list = [route_list]
        supported = set(MODEL.bundle["routes"]) if MODEL else set()
        routes = [{
            "routeId": str(r.get("routeId", "")),
            "routeName": r.get("routeName", ""),
            "routeTypeName": r.get("routeTypeName", ""),
            "startStationName": r.get("startStationName", ""),
            "endStationName": r.get("endStationName", ""),
            "modelSupported": str(r.get("routeId", "")) in supported,
        } for r in route_list]
        return jsonify({"status": "success", "data": routes})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/stations/<route_id>")
def get_stations(route_id):
    if route_id in STATIONS_CACHE:
        return jsonify({"status": "success", "data": STATIONS_CACHE[route_id]})
    url = "https://apis.data.go.kr/6410000/busrouteservice/v2/getBusRouteStationListv2"
    params = {"serviceKey": SERVICE_KEY, "routeId": route_id}
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        station_list = data.get("response", {}).get("msgBody", {}).get("busRouteStationList", [])
        if not isinstance(station_list, list):
            station_list = [station_list]
        stations = [{
            "stationSeq": int(s.get("stationSeq", 0)),
            "stationName": s.get("stationName", ""),
            "stationId": str(s.get("stationId", "")),
            "lat": float(s.get("y", 0)),
            "lng": float(s.get("x", 0)),
            "turnYn": s.get("turnYn", "N"),
        } for s in station_list]
        stations.sort(key=lambda x: x["stationSeq"])
        STATIONS_CACHE[route_id] = stations
        return jsonify({"status": "success", "data": stations})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/buses/<route_id>")
def get_buses(route_id):
    url = "https://apis.data.go.kr/6410000/buslocationservice/v2/getBusLocationListv2"
    params = {"serviceKey": SERVICE_KEY, "routeId": route_id}
    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        bus_list = data.get("response", {}).get("msgBody", {}).get("busLocationList", [])
        if not isinstance(bus_list, list):
            bus_list = [bus_list]
        buses = []
        for b in bus_list:
            remain = int(b.get("remainSeatCnt", -1))
            bus = {
                "vehId": str(b.get("vehId", "")),
                "plateNo": b.get("plateNo", ""),
                "stationSeq": int(b.get("stationSeq", 0)),
                "stationId": str(b.get("stationId", "")),
                "remainSeatCnt": remain,
                "passengerCnt": (44 - remain) if remain >= 0 else 0,
                "lowPlate": int(b.get("lowPlate", 0)),
            }
            _update_history(route_id, bus)
            buses.append(bus)
        return jsonify({"status": "success", "data": buses})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/predict", methods=["POST"])
def predict():
    if MODEL is None:
        return jsonify({"status": "error", "message": "모델이 로드되지 않았습니다."}), 503
    req = request.json
    route_id = str(req.get("route_id"))
    target_seq = int(req.get("target_seq"))
    queue_ahead = max(int(req.get("queue_ahead", 0)), 0)
    buses = req.get("buses", [])

    stations = STATIONS_CACHE.get(route_id, [])
    target = next((s for s in stations if s["stationSeq"] == target_seq), None)
    if target is None:
        return jsonify({"status": "success", "prediction": None})

    upcoming = sorted(
        [b for b in buses if b["stationSeq"] <= target_seq and b["remainSeatCnt"] >= 0],
        key=lambda x: -x["stationSeq"],
    )[:3]
    if not upcoming:
        return jsonify({"status": "success", "prediction": None})
    padded = list(upcoming)
    while len(padded) < 3:
        padded.append(virtual_bus(stations))

    route_fallback = False
    model_route = route_id
    supported = set(MODEL.bundle["routes"])
    if route_id not in supported:
        model_route = sorted(supported)[0]   # 미지원 노선은 전역 프로파일로 근사
        route_fallback = True

    rows = [
        build_feature_row(model_route, bus, target, stations, order, queue_ahead, "live-query")
        for order, bus in enumerate(padded, start=1)
    ]
    try:
        result = MODEL.predict_queries(pd.DataFrame(rows)).iloc[0].to_dict()
    except Exception as e:
        print(f"❌ 예측 에러: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    sent_class = int(result["predicted_sent_class"])
    dispatch_msgs = {
        0: "지금 오는 버스(1번째)에 탑승 가능",
        1: "1대 보내고 2번째 버스에 탑승",
        2: "2대 보내고 3번째 버스에 탑승",
        3: "다음 3대 안에 탑승 어려움 — 증차/대체 노선 필요",
    }
    bus_details = []
    for i, bus in enumerate(padded, start=1):
        bus_details.append({
            "order": i,
            "plate_no": bus["plateNo"],
            "virtual": bool(bus.get("virtual")),
            "current_seats": bus["remainSeatCnt"],
            "stops_left": max(target_seq - bus["stationSeq"], 0),
            "predicted_seats": round(result[f"bus_{i}_point_arrival_seats"], 1),
            "board_by_probability": round(result[f"board_by_{i}_probability"], 4),
        })
    return jsonify({
        "status": "success",
        "prediction": {
            "model_id": result["model_id"],
            "queue_ahead": queue_ahead,
            "predicted_sent_class": sent_class,
            "sent_class_label": "3+" if sent_class == 3 else str(sent_class),
            "dispatch_message": dispatch_msgs[sent_class],
            "sent_probabilities": [round(result[f"sent_{k}_probability"], 4) for k in range(3)]
                                   + [round(result["sent_3_plus_probability"], 4)],
            "expected_sent_buses": round(result["expected_sent_buses_truncated"], 2),
            "tail_probability_after_3_buses": round(result["tail_probability_after_3_buses"], 4),
            "predicted_seats": bus_details[0]["predicted_seats"],  # 기존 UI 호환(1번째 버스)
            "plate_no": bus_details[0]["plate_no"],
            "stops_left": bus_details[0]["stops_left"],
            "buses": bus_details,
            "route_fallback": route_fallback,
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
