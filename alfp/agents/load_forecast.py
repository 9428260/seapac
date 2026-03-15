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


def _split_train_val(df: pd.DataFrame, val_ratio: float = 0.15, min_train: int = 24):
    """
    시계열을 train/val로 분할.
    1일(96스텝) 데이터일 때는 검증 구간을 낮 시간대(12시~24시)로 두어
    PV 잉여가 있는 구간이 val에 포함되도록 하고, 거래권고가 나올 수 있게 함.
    """
    n = len(df)
    if n < min_train:
        raise ValueError(
            f"데이터 행 수({n})가 최소 학습 구간({min_train})보다 적습니다. "
            "학습용 데이터(train_2026_seoul.pkl 등)로 ALFP를 실행하거나, 해당 프로슈머가 포함된 충분한 기간의 데이터를 사용하세요."
        )
    # 1일 수준(96스텝 이하) 데이터: val에 낮·오후가 포함되도록 절반 분할 (거래권고용)
    if n <= 96 and n >= min_train:
        split = n // 2
    else:
        split = int(n * (1 - val_ratio))
        split = max(min_train, min(split, n - 1))
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
        if df.empty:
            raise ValueError(
                "feature_df가 비어 있습니다. 사용 중인 데이터에 해당 프로슈머의 시계열이 있는지, "
                "또는 학습용 데이터(train_2026_seoul.pkl 등)로 ALFP를 실행해 보세요."
            )
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
