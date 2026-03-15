#!/usr/bin/env python3
"""
MATPOWER case141.m 파일을 파싱하여 IEEE141_grid.pkl을 생성합니다.
실제 IEEE 141버스 계통 데이터(Khodr et al., Caracas 배전계통)를 사용합니다.

출처: MATPOWER case141
  https://github.com/MATPOWER/matpower/blob/master/data/case141.m
  H.M. Khodr et al., Electric Power Systems Research, 2008.
"""
import pickle
import re
from pathlib import Path

import numpy as np
import pandas as pd

# MATPOWER column indices (0-based for Python)
BUS_I, BUS_TYPE, PD, QD, GS, BS, AREA, VM, VA, BASE_KV, ZONE, VMAX, VMIN = range(13)
F_BUS, T_BUS, BR_R, BR_X, BR_B, RATE_A, RATE_B, RATE_C, TAP, SHIFT, BR_STATUS = range(11)
GEN_BUS, PG, QG, QMAX, QMIN, VG, MBASE, GEN_STATUS, PMAX, PMIN = range(10)

CASE141_PATH = Path(__file__).parent / "case141.m"
OUTPUT_PKL = Path(__file__).parent / "IEEE141_grid.pkl"
BASE_MVA = 10.0
VBASE_KV = 12.47
PF = 0.85


def parse_mpc_array(text: str, start_marker: str, end_marker: str = "];"):
    """Extract a single mpc.xxx = [ ... ]; block and return list of rows (list of floats)."""
    start = text.find(start_marker)
    if start == -1:
        raise ValueError(f"Block not found: {start_marker}")
    start = text.index("[", start) + 1
    end = text.index(end_marker, start)
    block = text[start:end]
    rows = []
    for line in block.splitlines():
        line = line.strip().rstrip(";").strip()
        if not line or line.startswith("%"):
            continue
        # remove trailing comment
        if "%%" in line:
            line = line.split("%%")[0].strip().rstrip(";")
        tokens = line.split()
        row = [float(t) for t in tokens]
        rows.append(row)
    return rows


def load_case141(mpath: Path):
    """Load and convert case141.m to DataFrames."""
    text = mpath.read_text(encoding="utf-8", errors="replace")

    bus_rows = parse_mpc_array(text, "mpc.bus = [")
    gen_rows = parse_mpc_array(text, "mpc.gen = [")
    branch_rows = parse_mpc_array(text, "mpc.branch = [")

    # --- Branch: convert r, x from Ohms to p.u. ---
    Vbase = VBASE_KV * 1e3   # V
    Sbase = BASE_MVA * 1e6   # VA
    Zbase = Vbase ** 2 / Sbase
    for row in branch_rows:
        row[BR_R] = row[BR_R] / Zbase
        row[BR_X] = row[BR_X] / Zbase

    # --- Bus: convert Pd (kVA) to MW, Qd (MVAr) ---
    for row in bus_rows:
        s_mva = row[PD] / 1e3   # kVA -> MVA
        row[PD] = s_mva * PF
        row[QD] = s_mva * np.sin(np.arccos(PF))

    # --- Build DataFrames (column names compatible with converter & notebook) ---
    buses = pd.DataFrame({
        "bus_id": [int(r[BUS_I]) for r in bus_rows],
        "type": [int(r[BUS_TYPE]) for r in bus_rows],
        "pd_mw": [r[PD] for r in bus_rows],
        "qd_mvar": [r[QD] for r in bus_rows],
        "base_kv": [r[BASE_KV] for r in bus_rows],
        "vm": [r[VM] for r in bus_rows],
        "va": [r[VA] for r in bus_rows],
        "vmin": [r[VMIN] for r in bus_rows],
        "vmax": [r[VMAX] for r in bus_rows],
        "name": [f"bus_{int(r[BUS_I])}" for r in bus_rows],
    })

    branches = pd.DataFrame({
        "from_bus": [int(r[F_BUS]) for r in branch_rows],
        "to_bus": [int(r[T_BUS]) for r in branch_rows],
        "r": [r[BR_R] for r in branch_rows],
        "x": [r[BR_X] for r in branch_rows],
        "b": [r[BR_B] for r in branch_rows],
        "status": [int(r[BR_STATUS]) for r in branch_rows],
    })

    generators = pd.DataFrame({
        "bus": [int(r[GEN_BUS]) for r in gen_rows],
        "pg": [r[PG] for r in gen_rows],
        "qg": [r[QG] for r in gen_rows],
        "p_max": [r[PMAX] for r in gen_rows],
        "p_min": [r[PMIN] for r in gen_rows],
        "q_max": [r[QMAX] for r in gen_rows],
        "q_min": [r[QMIN] for r in gen_rows],
        "status": [int(r[GEN_STATUS]) for r in gen_rows],
    })

    return {
        "buses": buses,
        "branches": branches,
        "generators": generators,
    }


def main():
    if not CASE141_PATH.exists():
        raise FileNotFoundError(
            f"case141.m not found at {CASE141_PATH}. "
            "Download from: https://raw.githubusercontent.com/MATPOWER/matpower/master/data/case141.m"
        )

    grid = load_case141(CASE141_PATH)
    obj = {"grid": grid}

    with open(OUTPUT_PKL, "wb") as f:
        pickle.dump(obj, f)

    print(f"Saved {OUTPUT_PKL} (MATPOWER case141 – actual IEEE 141-bus system)")
    print(f"  buses:      {len(grid['buses'])} rows")
    print(f"  branches:   {len(grid['branches'])} rows")
    print(f"  generators: {len(grid['generators'])} rows")
    print(f"  base_kv:    {VBASE_KV} kV, baseMVA: {BASE_MVA}")


if __name__ == "__main__":
    main()
