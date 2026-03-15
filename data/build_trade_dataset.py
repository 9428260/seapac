#!/usr/bin/env python3
"""
test_2026may_seoul.pkl에서 전력거래가 발생하는 구간을 추출하여 trade_20260315.pkl 생성.

전력거래 발생 조건: 동일 타임스텝에 잉여( surplus = pv_kw - load_kw > 0 ) 프로슈머와
부족( deficit ) 프로슈머가 각각 1명 이상 있을 때.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

# 프로젝트 루트 기준 경로
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DEFAULT_SOURCE = SCRIPT_DIR / "test_2026may_seoul.pkl"
DEFAULT_OUTPUT = SCRIPT_DIR / "trade_20260315.pkl"


def load_data(path: Path) -> dict:
    with open(path, "rb") as f:
        return pickle.load(f)


def ensure_midday_surplus_for_trading(ts_day: pd.DataFrame, surplus_min_kw: float = 0.5) -> pd.DataFrame:
    """
    낮 시간대(10~15시)에 일부 프로슈머가 잉여(surplus >= surplus_min_kw)를 갖도록 보정.
    ALFP 거래권고가 발생하도록 데이터를 보강한다.
    """
    ts = ts_day.copy()
    ts["_hour"] = pd.to_datetime(ts["timestamp"]).dt.hour
    midday = (ts["_hour"] >= 10) & (ts["_hour"] <= 15)
    prosumers = ts["prosumer_id"].unique().tolist()
    # 절반 이상 프로슈머에 낮 시간대 잉여 보장 (pv = load + surplus_min 이상)
    n_apply = max(1, (len(prosumers) * 3) // 4)
    for pid in prosumers[:n_apply]:
        mask = midday & (ts["prosumer_id"] == pid)
        if mask.any():
            need = (ts.loc[mask, "load_kw"] + surplus_min_kw) - ts.loc[mask, "pv_kw"]
            bump = need.clip(lower=0)
            ts.loc[mask, "pv_kw"] = ts.loc[mask, "pv_kw"] + bump
    ts = ts.drop(columns=["_hour"])
    return ts


def find_trading_timestamps(ts: pd.DataFrame) -> pd.Series:
    """타임스텝별로 전력거래 가능 여부 (잉여·부족 동시 존재) 반환."""
    ts = ts.copy()
    ts["surplus_kw"] = ts["pv_kw"] - ts["load_kw"]
    g = ts.groupby("timestamp")["surplus_kw"]
    has_surplus = g.transform(lambda x: (x > 0).any())
    has_deficit = g.transform(lambda x: (x < 0).any())
    return (has_surplus & has_deficit)


def pick_best_day(ts: pd.DataFrame) -> str:
    """전력거래가 가장 많이 발생하는 하루(날짜)를 선택. YYYY-MM-DD 반환."""
    ts = ts.copy()
    ts["date"] = pd.to_datetime(ts["timestamp"]).dt.date
    ts["_trade_ok"] = find_trading_timestamps(ts)
    daily = ts.groupby("date")["_trade_ok"].sum()
    if daily.empty:
        raise ValueError("No timestamps with trading potential found.")
    best_date = daily.idxmax()
    return str(best_date)


def build_trade_dataset(
    source_path: Path = DEFAULT_SOURCE,
    output_path: Path = DEFAULT_OUTPUT,
) -> tuple[str, list[str]]:
    """
    source pkl을 읽어 전력거래가 발생하는 하루를 추출해 output pkl로 저장.
    반환: (measure_date_yyyy_mm_dd, prosumer_id_list)
    """
    if not source_path.is_file():
        raise FileNotFoundError(f"소스 파일이 없습니다: {source_path}")

    data = load_data(source_path)
    if not isinstance(data, dict) or "timeseries" not in data:
        raise ValueError("데이터는 'timeseries' 키를 가진 dict여야 합니다.")

    ts = data["timeseries"]
    if "timestamp" not in ts.columns or "prosumer_id" not in ts.columns:
        raise ValueError("timeseries에 'timestamp', 'prosumer_id' 컬럼이 필요합니다.")

    measure_date = pick_best_day(ts)
    ref_date = pd.to_datetime(measure_date).date()

    ts["_date"] = pd.to_datetime(ts["timestamp"]).dt.date
    ts_day = ts.loc[ts["_date"] == ref_date].drop(columns=["_date"]).copy()
    ts_day = ts_day.sort_values(["timestamp", "prosumer_id"]).reset_index(drop=True)
    ts_day = ensure_midday_surplus_for_trading(ts_day)

    prosumer_ids = sorted(ts_day["prosumer_id"].unique().tolist())
    prosumers = data["prosumers"]
    if "prosumer_id" in prosumers.columns:
        prosumers_sub = prosumers[prosumers["prosumer_id"].isin(prosumer_ids)].copy()
    else:
        prosumers_sub = prosumers

    t_min = ts_day["timestamp"].min()
    t_max = ts_day["timestamp"].max()
    meta = dict(data.get("metadata") or {})
    meta["period_start"] = str(t_min)
    meta["period_end"] = str(t_max)
    meta["measure_date"] = measure_date
    meta["purpose"] = "power_trading_simulation"
    meta["source_file"] = str(source_path.name)

    out = {
        "metadata": meta,
        "timeseries": ts_day,
        "prosumers": prosumers_sub,
    }
    if "elia_internal" in data:
        ei = data["elia_internal"]
        if hasattr(ei, "columns") and "timestamp" in ei.columns:
            ei_ts = pd.to_datetime(ei["timestamp"]).dt.date
            out["elia_internal"] = ei.loc[ei_ts == ref_date].copy()
        else:
            out["elia_internal"] = ei
    if "grid" in data:
        out["grid"] = data["grid"]

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        pickle.dump(out, f)

    return measure_date, prosumer_ids


def main():
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SOURCE
    output = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    try:
        measure_date, prosumer_ids = build_trade_dataset(source, output)
    except Exception as e:
        print(f"오류: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"생성 파일: {output}")
    print(f"Measure date: {measure_date}")
    print(f"Prosumer ID 수: {len(prosumer_ids)}")
    print("Prosumer ID 목록:")
    for pid in prosumer_ids:
        print(f"  - {pid}")


if __name__ == "__main__":
    main()
