"""
IEEE141 그리드 최소 구조 pkl 생성.
elia_ieee141_reproduction_converter.py의 load_ieee141_grid()가 기대하는 형식:
  {"grid": {"buses": df, "branches": df, "generators": df}}
  또는 {"buses": df, "branches": df, "generators": df}
PROSUMER_TABLE의 버스 ID를 포함한 최소 데이터프레임을 생성.
"""
import pickle
from pathlib import Path

import pandas as pd

# elia_ieee141_reproduction_converter.PROSUMER_TABLE 에서 사용하는 버스 ID
BUS_IDS = [
    48, 78, 102, 127, 59, 109, 130, 140, 67, 95, 133, 136,
    62, 86, 106, 138, 74, 100, 116, 134,
]

OUTPUT_PATH = Path("IEEE141_grid.pkl")


def main():
    # 최소 buses: bus_id, type (1=PQ, 2=PV, 3=slack), base_kv
    buses = pd.DataFrame({
        "bus_id": BUS_IDS,
        "type": [1] * (len(BUS_IDS) - 1) + [3],  # 마지막을 slack으로
        "base_kv": [12.5] * len(BUS_IDS),
        "name": [f"bus_{b}" for b in BUS_IDS],
    })

    # 최소 branches: from_bus, to_bus, r, x (연결만 있으면 됨)
    branches = pd.DataFrame({
        "from_bus": BUS_IDS[:-1],
        "to_bus": BUS_IDS[1:],
        "r": [0.01] * (len(BUS_IDS) - 1),
        "x": [0.05] * (len(BUS_IDS) - 1),
    })

    # 최소 generators: bus, p_max, q_max
    generators = pd.DataFrame({
        "bus": [BUS_IDS[-1]],
        "p_max": [100.0],
        "q_max": [50.0],
    })

    grid = {
        "buses": buses,
        "branches": branches,
        "generators": generators,
    }

    # converter는 "grid" 키 또는 직접 buses/branches/generators 둘 다 허용
    obj = {"grid": grid}

    with open(OUTPUT_PATH, "wb") as f:
        pickle.dump(obj, f)

    print(f"Saved {OUTPUT_PATH}")
    print("  buses:", len(buses))
    print("  branches:", len(branches))
    print("  generators:", len(generators))


if __name__ == "__main__":
    main()
