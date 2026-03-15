"""
FeatureEngineeringAgent - 시계열 feature 생성
hour, weekday, holiday, lag features, price features, OpenWeather 날씨 feature
"""

import os
import numpy as np
import pandas as pd
from alfp.agents.state import ALFPState
from alfp.tools.openweather import get_current_weather, get_weather_for_dataframe

# 한국 공휴일 (2026년 기준, 고정 공휴일)
KR_HOLIDAYS_2026 = {
    (1, 1),   # 신정
    (3, 1),   # 삼일절
    (5, 5),   # 어린이날
    (6, 6),   # 현충일
    (8, 15),  # 광복절
    (10, 3),  # 개천절
    (10, 9),  # 한글날
    (12, 25), # 크리스마스
}


def _is_holiday(dt_series: pd.Series) -> pd.Series:
    """날짜 시리즈에서 공휴일 여부를 반환합니다."""
    return dt_series.apply(
        lambda x: int((x.month, x.day) in KR_HOLIDAYS_2026)
    )


def build_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """
    DataFrame에 time-based, lag, rolling, price, 날씨 feature를 추가합니다.
    날씨 컬럼(weather_*)은 feature_engineering_agent에서 미리 추가된 df를 기대합니다.

    Returns
        feature_df: feature 컬럼이 추가된 DataFrame
        feature_names: 모델 입력으로 사용할 컬럼명 목록
    """
    df = df.copy()

    # timestamp를 timezone-naive로 변환 (tz-aware dt accessor 활용)
    ts = df["timestamp"]

    # ── 시간 기반 feature ─────────────────────────────────
    df["hour"] = ts.dt.hour
    df["minute"] = ts.dt.minute
    df["hour_frac"] = df["hour"] + df["minute"] / 60.0
    df["weekday"] = ts.dt.weekday          # 0=월, 6=일
    df["is_weekend"] = (df["weekday"] >= 5).astype(int)
    df["month"] = ts.dt.month
    df["day_of_year"] = ts.dt.day_of_year
    df["week_of_year"] = ts.dt.isocalendar().week.astype(int)
    df["is_holiday"] = _is_holiday(ts)
    df["is_peak_hour"] = df["hour"].apply(lambda h: int(9 <= h <= 22)).astype(int)

    # 계절성 사인/코사인 인코딩
    df["hour_sin"] = np.sin(2 * np.pi * df["hour_frac"] / 24)
    df["hour_cos"] = np.cos(2 * np.pi * df["hour_frac"] / 24)
    df["month_sin"] = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"] = np.cos(2 * np.pi * df["month"] / 12)

    # ── Lag features (15분 단위 기준) ─────────────────────
    # 1시간 전 = 4스텝, 24시간 전 = 96스텝, 1주 전 = 672스텝
    for col, lags in [
        ("load_kw",  [1, 4, 8, 96, 192, 672]),
        ("pv_kw",    [1, 4, 96]),
    ]:
        for lag in lags:
            df[f"{col}_lag{lag}"] = df[col].shift(lag)

    # ── Rolling 통계 ──────────────────────────────────────
    for col, windows in [
        ("load_kw", [4, 96]),   # 1h, 24h 이동평균
        ("pv_kw",   [4, 96]),
    ]:
        for w in windows:
            df[f"{col}_roll_mean_{w}"] = df[col].shift(1).rolling(w).mean()
            df[f"{col}_roll_std_{w}"]  = df[col].shift(1).rolling(w).std()

    # ── 가격 feature ──────────────────────────────────────
    for col in ["price_buy", "price_sell", "price_p2p"]:
        if col in df.columns:
            df[f"{col}_lag4"] = df[col].shift(4)
            df[f"{col}_roll_mean_96"] = df[col].shift(1).rolling(96).mean()

    # ── ESS / BESS feature ────────────────────────────────
    if "bess_soc_kwh" in df.columns:
        df["bess_soc_lag1"] = df["bess_soc_kwh"].shift(1)
        df["bess_soc_lag4"] = df["bess_soc_kwh"].shift(4)

    # ── 날씨 feature (OpenWeather) ──────────────────────
    # weather_* 컬럼은 feature_engineering_agent에서 get_weather_for_dataframe()으로 미리 추가됨

    # ── feature 컬럼 목록 (타겟 제외) ────────────────────
    exclude = {"timestamp", "bus", "prosumer_id", "prosumer_type", "split",
               "load_kw", "pv_kw", "wt_kw", "bess_ref_power_kw",
               "controllable_load_kw", "cdg_kw_cap"}
    feature_names = [c for c in df.columns if c not in exclude]

    # 결측행 제거 (lag으로 인한 초반 NaN)
    df = df.dropna(subset=feature_names).reset_index(drop=True)

    return df, feature_names


def feature_engineering_agent(state: ALFPState) -> ALFPState:
    """Feature Engineering Agent 노드 함수. OpenWeather 날씨 feature 포함."""
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[FeatureEngineeringAgent] feature 생성 시작")

    df = state["clean_data"]

    # OpenWeather API 키가 있으면 현재 날씨를 가져와 오늘 구간에 반영
    current_weather = None
    if os.environ.get("OPENWEATHER_API_KEY"):
        try:
            current_weather = get_current_weather()
            log.append("  OpenWeather 현재 날씨 반영 (오늘 구간)")
        except Exception as e:
            errors.append(f"[FeatureEngineeringAgent] OpenWeather 조회 실패: {e}")

    try:
        df = get_weather_for_dataframe(df, current_weather=current_weather)
        feature_df, feature_names = build_features(df)
        log.append(f"  생성된 feature 수: {len(feature_names)} (날씨 4개 포함)")
        log.append(f"  학습 가능 레코드 수: {len(feature_df):,} (lag 제거 후)")
        log.append("[FeatureEngineeringAgent] 완료")
    except Exception as e:
        errors.append(f"[FeatureEngineeringAgent] 오류: {e}")
        raise

    return {
        **state,
        "feature_df": feature_df,
        "feature_names": feature_names,
        "messages": log,
        "errors": errors,
    }
