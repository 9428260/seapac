"""
XGBoost 기반 전력 예측 모델
"""

import numpy as np
import pandas as pd
import xgboost as xgb
from typing import Optional


class XGBForecastModel:
    """XGBoost 회귀 모델 래퍼."""

    DEFAULT_PARAMS = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "learning_rate": 0.05,
        "max_depth": 6,
        "min_child_weight": 5,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "n_estimators": 500,
        "early_stopping_rounds": 50,
        "verbosity": 0,
        "n_jobs": -1,
        "random_state": 42,
    }

    def __init__(self, params: Optional[dict] = None, target: str = "load_kw"):
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
        self.target = target
        self.model: Optional[xgb.XGBRegressor] = None
        self.feature_names: list = []

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "XGBForecastModel":
        self.feature_names = X_train.columns.tolist()
        self.model = xgb.XGBRegressor(**self.params)

        eval_set = [(X_val, y_val)] if X_val is not None and y_val is not None else None
        self.model.fit(
            X_train,
            y_train,
            eval_set=eval_set,
            verbose=False,
        )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        assert self.model is not None, "모델이 학습되지 않았습니다."
        preds = self.model.predict(X[self.feature_names])
        return np.clip(preds, 0, None)

    def feature_importance(self) -> pd.Series:
        assert self.model is not None
        return pd.Series(
            self.model.feature_importances_,
            index=self.feature_names,
        ).sort_values(ascending=False)

    @property
    def model_name(self) -> str:
        return "XGBoost"
