"""팀의 GBIS 수집 서버에서 데이터를 내려받아 CSV로 저장합니다."""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_OUTPUT_DIR = Path("data/csv")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="팀의 GBIS 서버 데이터를 로컬 캐시와 CSV로 내려받습니다."
    )
    parser.add_argument(
        "route_id",
        nargs="?",
        help="전체 이력을 받을 GBIS 노선 ID (생략하면 노선 목록과 최신 데이터만 받음)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"CSV 저장 폴더 (기본값: {DEFAULT_OUTPUT_DIR})",
    )
    return parser


def save_csv(dataframe, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    dataframe.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"저장: {path} ({len(dataframe):,}행)")


def main() -> int:
    args = build_parser().parse_args()
    output_dir = args.output_dir

    try:
        from gbis_client import GBISApiCache, GBISClientError
    except ModuleNotFoundError as exc:
        print(
            f"필요한 패키지({exc.name})가 설치되어 있지 않습니다.\n"
            "먼저 다음 명령을 실행하세요:\n"
            "  python3 -m pip install -r requirements-client.txt"
        )
        return 1

    try:
        with GBISApiCache.from_env() as cache:
            print("노선·정류장·최신 위치 데이터를 갱신합니다...")
            counts = cache.refresh_all()
            print(
                "갱신 완료: "
                f"노선 {counts['routes']:,}건, "
                f"정류장 {counts['stations']:,}건, "
                f"최신 위치 {counts['latest_locations']:,}건"
            )

            routes = cache.routes_df()
            stations = cache.stations_df()
            latest = cache.latest_locations_df()

            save_csv(routes, output_dir / "routes.csv")
            save_csv(stations, output_dir / "stations.csv")
            save_csv(latest, output_dir / "latest_locations.csv")

            if args.route_id is None:
                print("\n수집 가능한 노선 ID:")
                if routes.empty:
                    print("  서버에 수집된 노선이 없습니다.")
                else:
                    for row in routes.itertuples():
                        print(
                            f"  {row.route_id} "
                            f"(관측 {row.observation_count:,}건, "
                            f"마지막 수집 {row.last_collected_at})"
                        )
                    print("\n과거 이력까지 받으려면 다음처럼 다시 실행하세요:")
                    print(f"  python get_data.py {routes.iloc[0]['route_id']}")
                return 0

            print(f"\n노선 {args.route_id}의 과거 이력을 갱신합니다...")
            downloaded = cache.refresh_full_history(args.route_id)
            history = cache.history_df(args.route_id)
            save_csv(history, output_dir / f"history_{args.route_id}.csv")
            print(f"새로 내려받은 이력: {downloaded:,}건")
            return 0
    except (GBISClientError, ValueError) as exc:
        print(f"오류: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
