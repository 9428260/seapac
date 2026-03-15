"""
PVForecastAgent - 태양광(PV) 발전량 예측 에이전트
입력: irradiance proxy, hour, month (날씨 proxy 포함)
"""

import numpy as np
import pandas as pd
from alfp.agents.state import ALFPState
from alfp.models.lgbm_model import LGBMForecastModel
from alfp.models.xgboost_model import XGBForecastModel
from alfp.skills.energy_forecast import EnergyForecastSkill

TARGET = "pv_kw"

# PV 예측에 적합한 feature만 선택 (태양광 관련)
PV_FEATURE_KEYWORDS = [
    "hour", "minute", "hour_frac", "month", "day_of_year",
    "weekday", "is_weekend", "is_holiday",
    "hour_sin", "hour_cos", "month_sin", "month_cos",
    "pv_kw_lag", "pv_kw_roll",
    "price_buy", "price_sell",
    "weather_temp_c", "weather_clouds_pct", "weather_humidity_pct", "weather_wind_speed_ms",
]


def _pv_features(feature_names: list) -> list:
    """PV 예측에 유효한 feature만 필터링합니다."""
    selected = []
    for f in feature_names:
        if any(kw in f for kw in PV_FEATURE_KEYWORDS):
            selected.append(f)
    return selected


def _split_train_val(df: pd.DataFrame, val_ratio: float = 0.15):
    n = len(df)
    split = int(n * (1 - val_ratio))
    return df.iloc[:split].copy(), df.iloc[split:].copy()


def pv_forecast_agent(state: ALFPState) -> ALFPState:
    """
    PVForecastAgent 노드 함수.
    - 태양광 전용 feature 선택
    - 모델 학습 (낮 시간대에만 의미 있음)
    - 검증셋 예측 (음수 → 0 클리핑)
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[PVForecastAgent] PV 발전량 예측 모델 학습 시작")

    df = state["feature_df"]
    feature_names = state["feature_names"]
    selected_model = state.get("selected_model", "lgbm")
    model_config = state.get("model_config", {})

    try:
        pv_feats = _pv_features(feature_names)
        pv_feats = [f for f in pv_feats if f in df.columns]

        train_df, val_df = _split_train_val(df)

        X_train = train_df[pv_feats]
        y_train = train_df[TARGET]
        X_val = val_df[pv_feats]
        y_val = val_df[TARGET]

        if selected_model == "lgbm":
            model = LGBMForecastModel(params=model_config, target=TARGET)
        else:
            model = XGBForecastModel(params=model_config, target=TARGET)

        model.feature_names = pv_feats
        model.fit(X_train, y_train, X_val, y_val)

        val_preds = np.clip(model.predict(X_val), 0, None)
        forecast_df = EnergyForecastSkill.build_forecast_result(
            val_df["timestamp"], val_df[TARGET], val_preds,
            target_col="pv_kw", pred_col="predicted_pv_kw",
        )

        log.append(f"  PV feature 수: {len(pv_feats)}")
        log.append(f"  학습셋: {len(train_df):,}건 / 검증셋: {len(val_df):,}건")
        log.append("[PVForecastAgent] 완료")

    except Exception as e:
        errors.append(f"[PVForecastAgent] 오류: {e}")
        raise

    return {
        **state,
        "pv_forecast": forecast_df,
        "pv_model": model,
        "messages": log,
        "errors": errors,
    }
