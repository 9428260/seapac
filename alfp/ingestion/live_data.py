"""
실시간/외부 측정값 ingest.

지원 포맷:
- CSV
- JSON (list[dict] 또는 {"records": [...]})

필수 컬럼:
- prosumer_id
- timestamp

선택 컬럼:
- load_kw, pv_kw, wt_kw, bess_soc_kwh
- price_buy, price_sell, price_p2p
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


_MERGE_COLUMNS = [
    "load_kw",
    "pv_kw",
    "wt_kw",
    "bess_soc_kwh",
    "price_buy",
    "price_sell",
    "price_p2p",
]


def load_external_measurements(path: str | Path) -> pd.DataFrame:
    """외부 측정 파일을 DataFrame으로 로드."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"외부 ingest 파일이 없습니다: {p}")

    if p.suffix.lower() == ".csv":
        df = pd.read_csv(p)
    elif p.suffix.lower() in {".json", ".jsonl"}:
        if p.suffix.lower() == ".jsonl":
            records = []
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
            df = pd.DataFrame(records)
        else:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data = data.get("records", [])
            df = pd.DataFrame(data)
    else:
        raise ValueError(f"지원하지 않는 ingest 파일 형식: {p.suffix}")

    if df.empty:
        return df
    if "prosumer_id" not in df.columns or "timestamp" not in df.columns:
        raise ValueError("외부 ingest 데이터에는 prosumer_id, timestamp 컬럼이 필요합니다.")
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def apply_external_measurements(dataset: dict[str, Any], external_df: pd.DataFrame) -> dict[str, Any]:
    """
    외부 측정값을 dataset['timeseries']에 merge한다.

    동일 (prosumer_id, timestamp) 키가 있으면 overwrite하고,
    없으면 새 row를 append한다.
    """
    if external_df is None or external_df.empty:
        return dataset

    out = dict(dataset)
    ts = out["timeseries"].copy()
    ts["timestamp"] = pd.to_datetime(ts["timestamp"])

    merge_cols = [c for c in _MERGE_COLUMNS if c in external_df.columns]
    ext = external_df[["prosumer_id", "timestamp"] + merge_cols].copy()
    ext["timestamp"] = pd.to_datetime(ext["timestamp"])

    merged = ts.merge(
        ext,
        on=["prosumer_id", "timestamp"],
        how="left",
        suffixes=("", "__ext"),
    )
    for col in merge_cols:
        ext_col = f"{col}__ext"
        if ext_col in merged.columns:
            merged[col] = merged[ext_col].combine_first(merged[col])
            merged = merged.drop(columns=[ext_col])

    existing_keys = set(zip(ts["prosumer_id"], ts["timestamp"]))
    append_rows = ext[
        ~ext.apply(lambda r: (r["prosumer_id"], r["timestamp"]) in existing_keys, axis=1)
    ].copy()
    if not append_rows.empty:
        append_base = ts.iloc[:0].copy()
        for col in append_base.columns:
            if col not in append_rows.columns:
                append_rows[col] = pd.NA
        append_rows = append_rows[append_base.columns]
        merged = pd.concat([merged, append_rows], ignore_index=True)

    merged = merged.sort_values(["prosumer_id", "timestamp"]).reset_index(drop=True)
    out["timeseries"] = merged

    meta = dict(out.get("metadata") or {})
    meta["external_ingest_applied"] = True
    meta["external_ingest_records"] = int(len(external_df))
    out["metadata"] = meta
    return out
