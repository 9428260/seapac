#!/usr/bin/env python3
"""
test_2026may_seoul.pkl에서 지정한 기간(2026-05-01 ~ 2026-05-05)만 추려 테스트 데이터로 다시 저장.

사용법:
  python data/build_test_date_range.py
  python data/build_test_date_range.py 2026-05-01 2026-05-10
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "test_2026may_seoul.pkl"
DEFAULT_START = "2026-05-01"
DEFAULT_END = "2026-05-05"


def build_test_date_range(
    input_path: Path = DEFAULT_INPUT,
    output_path: Path | None = None,
    start_date: str = DEFAULT_START,
    end_date: str = DEFAULT_END,
) -> None:
    """테스트 pkl을 start_date ~ end_date 구간만 남겨 저장."""
    if output_path is None:
        output_path = input_path
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if start > end:
        start, end = end, start

    with open(input_path, "rb") as f:
        d = pickle.load(f)

    ts = d["timeseries"].copy()
    ts["_d"] = pd.to_datetime(ts["timestamp"]).dt.date
    mask = (ts["_d"] >= start) & (ts["_d"] <= end)
    ts = ts.loc[mask].drop(columns=["_d"]).reset_index(drop=True)
    d["timeseries"] = ts

    if "elia_internal" in d and d["elia_internal"] is not None:
        ei = d["elia_internal"].copy()
        if "timestamp" in ei.columns:
            ei["_d"] = pd.to_datetime(ei["timestamp"]).dt.date
            d["elia_internal"] = ei.loc[
                (ei["_d"] >= start) & (ei["_d"] <= end)
            ].drop(columns=["_d"]).reset_index(drop=True)

    meta = dict(d.get("metadata") or {})
    meta["period_start"] = str(ts["timestamp"].min())
    meta["period_end"] = str(ts["timestamp"].max())
    meta["period_description"] = f"{start} ~ {end}, Asia/Seoul, test"
    d["metadata"] = meta

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        pickle.dump(d, f)

    print(f"Saved {output_path}")
    print(f"  timeseries rows: {len(d['timeseries'])}")
    print(f"  period: {meta['period_start']} ~ {meta['period_end']}")


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_START
    end = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_END
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    build_test_date_range(
        input_path=DEFAULT_INPUT,
        output_path=out,
        start_date=start,
        end_date=end,
    )


if __name__ == "__main__":
    main()
