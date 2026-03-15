"""
DataQualityAgent - 결측 데이터 탐지, 이상치 탐지, 데이터 정제
"""

import numpy as np
import pandas as pd
from alfp.agents.state import ALFPState
from alfp.data.loader import get_timeseries


def data_quality_agent(state: ALFPState) -> ALFPState:
    """
    원시 데이터에서 품질 검사 및 정제를 수행합니다.

    - 결측값 탐지 및 보간
    - 이상치 탐지 (IQR 기반)
    - 음수값 클리핑
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[DataQualityAgent] 데이터 품질 검사 시작")

    raw_data = state["raw_data"]
    prosumer_id = state.get("prosumer_id")

    try:
        df = get_timeseries(raw_data, prosumer_id)
        report = {}

        # 1. 결측값 탐지
        numeric_cols = ["load_kw", "pv_kw", "wt_kw", "bess_soc_kwh",
                        "price_buy", "price_sell", "price_p2p"]
        missing = df[numeric_cols].isnull().sum()
        missing_pct = (missing / len(df) * 100).round(2)
        report["missing_values"] = missing[missing > 0].to_dict()
        report["missing_pct"] = missing_pct[missing_pct > 0].to_dict()

        if missing.sum() > 0:
            log.append(f"  결측값 탐지: {missing[missing>0].to_dict()}")
            df[numeric_cols] = df[numeric_cols].interpolate(method="time")
            df[numeric_cols] = df[numeric_cols].bfill().ffill()
            log.append("  결측값 처리: 시간기반 보간 완료")

        # 2. 이상치 탐지 (IQR)
        outlier_report = {}
        for col in ["load_kw", "pv_kw"]:
            q1 = df[col].quantile(0.25)
            q3 = df[col].quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 3 * iqr
            upper = q3 + 3 * iqr
            mask = (df[col] < lower) | (df[col] > upper)
            outlier_count = mask.sum()
            if outlier_count > 0:
                outlier_report[col] = {
                    "count": int(outlier_count),
                    "lower_bound": round(lower, 3),
                    "upper_bound": round(upper, 3),
                }
                df.loc[mask, col] = df[col].clip(lower=lower, upper=upper)
                log.append(f"  {col} 이상치 {outlier_count}건 클리핑 처리")
        report["outliers"] = outlier_report

        # 3. 물리적 제약: 음수 클리핑
        for col in ["load_kw", "pv_kw", "wt_kw", "bess_soc_kwh"]:
            neg_count = (df[col] < 0).sum()
            if neg_count > 0:
                df[col] = df[col].clip(lower=0)
                log.append(f"  {col} 음수값 {neg_count}건 → 0으로 클리핑")

        report["total_records"] = len(df)
        report["timestamp_range"] = {
            "start": str(df["timestamp"].min()),
            "end": str(df["timestamp"].max()),
        }
        report["status"] = "clean"
        log.append(f"[DataQualityAgent] 완료: 총 {len(df):,}건, 품질 상태 '{report['status']}'")

    except Exception as e:
        errors.append(f"[DataQualityAgent] 오류: {e}")
        raise

    return {
        **state,
        "clean_data": df,
        "quality_report": report,
        "messages": log,
        "errors": errors,
    }
