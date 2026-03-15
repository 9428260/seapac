"""
LoadForecastAgent - 전력 부하(load_kw) 예측 에이전트
추천 모델: LightGBM, XGBoost
"""

import numpy as np
import pandas as pd
from alfp.agents.state import ALFPState
from alfp.models.lgbm_model import LGBMForecastModel
from alfp.models.xgboost_model import XGBForecastModel
from alfp.skills.energy_forecast import EnergyForecastSkill

TARGET = "load_kw"


def _build_model(model_name: str, model_config: dict, target: str):
    if model_name == "lgbm":
        return LGBMForecastModel(params=model_config, target=target)
    elif model_name == "xgboost":
        return XGBForecastModel(params=model_config, target=target)
    else:
        raise ValueError(f"지원하지 않는 모델: {model_name}")


def _split_train_val(df: pd.DataFrame, val_ratio: float = 0.15):
    n = len(df)
    split = int(n * (1 - val_ratio))
    return df.iloc[:split].copy(), df.iloc[split:].copy()


def load_forecast_agent(state: ALFPState) -> ALFPState:
    """
    LoadForecastAgent 노드 함수.
    - 학습/검증 분할
    - 모델 학습
    - 검증셋 예측
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[LoadForecastAgent] 부하 예측 모델 학습 시작")

    df = state["feature_df"]
    feature_names = state["feature_names"]
    selected_model = state.get("selected_model", "lgbm")
    model_config = state.get("model_config", {})

    try:
        # feature가 없으면 대상에서 제외
        available = [f for f in feature_names if f in df.columns]

        train_df, val_df = _split_train_val(df)

        X_train = train_df[available]
        y_train = train_df[TARGET]
        X_val = val_df[available]
        y_val = val_df[TARGET]

        model = _build_model(selected_model, model_config, TARGET)
        model.fit(X_train, y_train, X_val, y_val)

        # 검증셋 예측 (EnergyForecastSkill.build_forecast_result 사용)
        val_preds = model.predict(X_val)
        forecast_df = EnergyForecastSkill.build_forecast_result(
            val_df["timestamp"], val_df[TARGET], val_preds,
            target_col="load_kw", pred_col="predicted_load_kw",
        )

        log.append(f"  모델: {model.model_name}")
        log.append(f"  학습셋: {len(train_df):,}건 / 검증셋: {len(val_df):,}건")
        log.append("[LoadForecastAgent] 완료")

    except Exception as e:
        errors.append(f"[LoadForecastAgent] 오류: {e}")
        raise

    return {
        **state,
        "load_forecast": forecast_df,
        "load_model": model,
        "messages": log,
        "errors": errors,
    }
