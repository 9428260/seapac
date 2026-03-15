"""
Data Loader - 학습/테스트 pkl 데이터 로드 및 전처리
"""

import pickle
from pathlib import Path
from typing import Optional
import pandas as pd
import numpy as np


def load_dataset(pkl_path: str) -> dict:
    """pkl 데이터셋을 로드합니다."""
    with open(pkl_path, "rb") as f:
        data = pickle.load(f)
    return data


def get_timeseries(data: dict, prosumer_id: Optional[str] = None) -> pd.DataFrame:
    """
    timeseries DataFrame을 반환합니다.
    prosumer_id를 지정하면 해당 프로슈머의 데이터만 반환합니다.
    """
    ts = data["timeseries"].copy()
    if prosumer_id is not None:
        ts = ts[ts["prosumer_id"] == prosumer_id].copy()
    ts = ts.sort_values("timestamp").reset_index(drop=True)
    return ts


def get_prosumer_list(data: dict) -> list:
    """모든 prosumer_id 목록을 반환합니다."""
    return sorted(data["timeseries"]["prosumer_id"].unique().tolist())


def get_price_data(data: dict) -> pd.DataFrame:
    """글로벌 가격 데이터(elia_internal)를 반환합니다."""
    return data["elia_internal"].copy().sort_values("timestamp").reset_index(drop=True)


def get_prosumer_metadata(data: dict, prosumer_id: str) -> dict:
    """특정 프로슈머의 설비 정보를 반환합니다."""
    p = data["prosumers"]
    row = p[p["prosumer_id"] == prosumer_id]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def train_test_split_by_time(
    df: pd.DataFrame,
    test_ratio: float = 0.2,
    timestamp_col: str = "timestamp",
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    시계열 데이터를 시간 순서 기준으로 train/validation 분할합니다.
    """
    df = df.sort_values(timestamp_col).reset_index(drop=True)
    split_idx = int(len(df) * (1 - test_ratio))
    train = df.iloc[:split_idx].copy()
    val = df.iloc[split_idx:].copy()
    return train, val


def describe_dataset(data: dict) -> str:
    """데이터셋 요약 정보를 반환합니다."""
    meta = data.get("metadata", {})
    ts = data["timeseries"]
    prosumers = data["prosumers"]

    lines = [
        f"데이터셋: {meta.get('name', 'N/A')}",
        f"기간: {meta.get('period_start', 'N/A')} ~ {meta.get('period_end', 'N/A')}",
        f"시간해상도: {meta.get('time_resolution_minutes', 'N/A')}분",
        f"타임존: {meta.get('timezone', 'N/A')}",
        f"프로슈머 수: {len(prosumers)}",
        f"타임스텝 수: {len(ts['timestamp'].unique())}",
        f"전체 레코드 수: {len(ts):,}",
        f"프로슈머 타입: {sorted(ts['prosumer_type'].unique().tolist())}",
        f"평균 부하(kW): {ts['load_kw'].mean():.2f}",
        f"평균 PV 발전(kW): {ts['pv_kw'].mean():.2f}",
    ]
    return "\n".join(lines)
