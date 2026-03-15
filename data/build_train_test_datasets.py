#!/usr/bin/env python3
"""
학습용(2026년, 서울 시간) / 테스트용(2026년 5월 1개월, 서울 시간) 데이터셋 생성.

- 학습용: 2026년 ELIA 데이터, Asia/Seoul 시간대, 프로슈머 ID = bus_{id}_{prosumer_type}
- 테스트용: 2026년 5월 ELIA 데이터 참조 1개월, 동일 시간대·동일 프로슈머 ID

출력:
  - train_2026_seoul.pkl  (학습용)
  - test_2026may_seoul.pkl (테스트용, 학습 이후 평가용)
"""
import pickle
from pathlib import Path

import pandas as pd

from elia_ieee141_reproduction_converter import (
    load_elia_raw_from_screenshot_schema,
    build_dataset_from_elia_df,
    load_ieee141_grid,
    build_prosumer_table,
)

DATA_DIR = Path(__file__).parent
ELIA_RAW_PATH = DATA_DIR / "elia_raw.csv"
IEEE141_GRID_PKL = DATA_DIR / "IEEE141_grid.pkl"
TRAIN_OUTPUT = DATA_DIR / "train_2026_seoul.pkl"
TEST_OUTPUT = DATA_DIR / "test_2026may_seoul.pkl"
TIMEZONE = "Asia/Seoul"


def main():
    # 1) ELIA 원시 데이터 로드 (서울 시간대로 변환)
    print("Loading ELIA raw and converting to Asia/Seoul...")
    elia_full = load_elia_raw_from_screenshot_schema(
        ELIA_RAW_PATH,
        target_tz=TIMEZONE,
    )
    elia_full["timestamp"] = pd.to_datetime(elia_full["timestamp"])

    # 2) 학습용: 2026년 데이터만
    elia_2026 = elia_full[
        (elia_full["timestamp"].dt.year == 2026)
    ].copy()
    if elia_2026.empty:
        raise ValueError("No 2026 data in elia_raw.csv. Ensure the file contains 2026 dates.")
    t_min_train = elia_2026["timestamp"].min()
    t_max_train = elia_2026["timestamp"].max()
    print(f"Train period (2026): {t_min_train} ~ {t_max_train} ({len(elia_2026)} rows)")

    train_dataset = build_dataset_from_elia_df(
        elia_2026,
        ieee141_grid_pkl=IEEE141_GRID_PKL,
        split_label="train",
        metadata_extra={
            "purpose": "training",
            "period_start": str(t_min_train),
            "period_end": str(t_max_train),
            "period_description": "2026 data, Asia/Seoul",
        },
    )
    with open(TRAIN_OUTPUT, "wb") as f:
        pickle.dump(train_dataset, f)
    print(f"Saved {TRAIN_OUTPUT}")
    print(f"  timeseries rows: {len(train_dataset['timeseries'])}, prosumers: {len(train_dataset['prosumers'])}")
    print(f"  prosumer_id sample: {train_dataset['prosumers']['prosumer_id'].tolist()[:5]}...")

    # 3) 테스트용: 2026년 5월 1개월 (2026-05-01 00:00 ~ 2026-05-31 23:45, 서울 시간)
    # 원본에 2026년 5월이 없으면 2025년 5월 데이터를 2026년 5월로 날짜만 이동하여 사용
    elia_2026may = elia_full[
        (elia_full["timestamp"].dt.year == 2026)
        & (elia_full["timestamp"].dt.month == 5)
    ].copy()
    if elia_2026may.empty:
        elia_2025may = elia_full[
            (elia_full["timestamp"].dt.year == 2025)
            & (elia_full["timestamp"].dt.month == 5)
        ].copy()
        if elia_2025may.empty:
            raise ValueError("No May 2025 or May 2026 data in elia_raw.csv.")
        # 2025-05 → 2026-05 로 날짜만 변경 (요일·시간 패턴 유지)
        elia_2026may = elia_2025may.copy()
        elia_2026may["timestamp"] = elia_2025may["timestamp"] + pd.DateOffset(years=1)
        print("(Using 2025-05 data re-dated to 2026-05 for test period)")
    t_min_test = elia_2026may["timestamp"].min()
    t_max_test = elia_2026may["timestamp"].max()
    print(f"Test period (2026-05): {t_min_test} ~ {t_max_test} ({len(elia_2026may)} rows)")

    test_dataset = build_dataset_from_elia_df(
        elia_2026may,
        ieee141_grid_pkl=IEEE141_GRID_PKL,
        split_label="test",
        metadata_extra={
            "purpose": "test_after_training",
            "period_start": str(t_min_test),
            "period_end": str(t_max_test),
            "period_description": "2026 May (1 month), Asia/Seoul, for evaluation after training",
        },
    )
    with open(TEST_OUTPUT, "wb") as f:
        pickle.dump(test_dataset, f)
    print(f"Saved {TEST_OUTPUT}")
    print(f"  timeseries rows: {len(test_dataset['timeseries'])}, prosumers: {len(test_dataset['prosumers'])}")

    # 4) 프로슈머 20개 ID 확인
    prosumers = build_prosumer_table()
    ids = prosumers["prosumer_id"].tolist()
    print("\n20 prosumer_id (bus_{id}_{prosumer_type}):")
    for i, pid in enumerate(ids):
        print(f"  {i+1:2d}. {pid}")


if __name__ == "__main__":
    main()
