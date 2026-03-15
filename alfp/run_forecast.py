"""
ALFP 전력 사용량 예측 — 빠른 실행 스크립트.

프로젝트 루트에서 실행:
    python alfp/run_forecast.py
    python alfp/run_forecast.py --prosumer bus_62_Residential
    python alfp/run_forecast.py --list-prosumers
    python -m alfp.run_forecast --prosumer bus_48_Commercial
"""

import sys
import os

# 프로젝트 루트를 sys.path에 추가 (alfp/ 내에서 실행해도 alfp 패키지 import 가능)
_script_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_script_dir)
if _root not in sys.path:
    sys.path.insert(0, _root)

from alfp.main import run, print_section
from alfp.data.loader import load_dataset, get_prosumer_list, describe_dataset


def demo_all_types():
    """각 프로슈머 타입별 대표 1개씩 예측 실행 데모."""
    representatives = [
        "bus_48_Commercial",
        "bus_62_Residential",
        "bus_67_Industrial",
        "bus_59_Rural",
        "bus_74_EnergyHub",
    ]
    results = {}
    for pid in representatives:
        print(f"\n{'#'*60}")
        print(f"# {pid}")
        print(f"{'#'*60}")
        result = run(prosumer_id=pid, verbose=False)
        results[pid] = result
    return results


if __name__ == "__main__":
    if "--all" in sys.argv:
        demo_all_types()
    else:
        # 기본: alfp.main과 동일한 CLI
        from alfp.main import run
        import argparse

        parser = argparse.ArgumentParser(description="ALFP 전력 사용량 예측 시스템")
        parser.add_argument("--prosumer", default="bus_48_Commercial")
        parser.add_argument("--data", default="data/train_2026_seoul.pkl")
        parser.add_argument("--horizon", type=int, default=96)
        parser.add_argument("--list-prosumers", action="store_true")
        parser.add_argument("--quiet", action="store_true")
        parser.add_argument("--all", action="store_true", help="모든 타입 데모 실행")

        args = parser.parse_args()

        if args.list_prosumers:
            data = load_dataset(args.data)
            print(describe_dataset(data))
            print()
            for pid in get_prosumer_list(data):
                print(f"  {pid}")
        else:
            run(
                prosumer_id=args.prosumer,
                data_path=args.data,
                forecast_horizon=args.horizon,
                verbose=not args.quiet,
            )
