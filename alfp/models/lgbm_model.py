"""
LightGBM 기반 전력 예측 모델
"""

import numpy as np
import pandas as pd
import lightgbm as lgb
from typing import Optional


class LGBMForecastModel:
    """LightGBM 회귀 모델 래퍼."""

    DEFAULT_PARAMS = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.05,
        "num_leaves": 63,
        "max_depth": -1,
        "min_child_samples": 20,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "n_estimators": 500,
        "early_stopping_rounds": 50,
        "verbose": -1,
        "n_jobs": -1,
    }

    def __init__(self, params: Optional[dict] = None, target: str = "load_kw"):
        self.params = {**self.DEFAULT_PARAMS, **(params or {})}
        self.target = target
        self.model: Optional[lgb.LGBMRegressor] = None
        self.feature_names: list = []

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None,
    ) -> "LGBMForecastModel":
        self.feature_names = X_train.columns.tolist()
        fit_params = {k: v for k, v in self.params.items() if k not in ("early_stopping_rounds",)}
        self.model = lgb.LGBMRegressor(**fit_params)

        eval_set = [(X_val, y_val)] if X_val is not None and y_val is not None else None
        callbacks = []
        if eval_set:
            callbacks.append(lgb.early_stopping(self.params.get("early_stopping_rounds", 50), verbose=False))
            callbacks.append(lgb.log_evaluation(-1))

        self.model.fit(
            X_train,
            y_train,
            eval_set=eval_set,
            callbacks=callbacks if callbacks else None,
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
        return "LightGBM"
