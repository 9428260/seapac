"""
EnergyForecastSkill - load forecast 실행 및 model selection 로직
설정: alfp/config/skills_config.json 의 energy_forecast 섹션
"""

from typing import Any
import pandas as pd
import numpy as np

from alfp.config import get_skills_config


class EnergyForecastSkill:
    """
    에너지 예측 스킬.
    LoadForecastAgent / PVForecastAgent에서 공통으로 사용하는
    모델 선택 및 예측 실행 유틸리티.
    """

    @staticmethod
    def select_model(n_samples: int, prosumer_type: str) -> str:
        """
        데이터 크기와 프로슈머 타입 기반 모델 자동 선택.

        Returns:
            "lgbm" | "xgboost"
        """
        cfg = get_skills_config().get("energy_forecast", {}).get("model_selection", {})
        min_samples = cfg.get("lgbm_min_samples", 5000)
        default_model = cfg.get("default_model", "xgboost")
        if n_samples >= min_samples:
            return "lgbm"
        return default_model

    @staticmethod
    def evaluate_forecast(actual: np.ndarray, predicted: np.ndarray) -> dict:
        """예측 결과 간단 평가 (MAPE, RMSE)."""
        cfg = get_skills_config().get("energy_forecast", {}).get("evaluate_forecast", {})
        min_actual = cfg.get("mape_min_actual_kw", 1.0)
        mask = actual > min_actual
        mape = float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100) if mask.sum() > 0 else float("nan")
        rmse = float(np.sqrt(np.mean((actual - predicted) ** 2)))
        return {"mape": round(mape, 2), "rmse": round(rmse, 4)}

    @staticmethod
    def build_forecast_result(
        timestamps: pd.Series,
        actual: pd.Series,
        predicted: np.ndarray,
        target_col: str,
        pred_col: str,
    ) -> pd.DataFrame:
        """예측 결과를 DataFrame으로 반환합니다."""
        return pd.DataFrame({
            "timestamp": timestamps.values,
            target_col: actual.values,
            pred_col: predicted,
        })
