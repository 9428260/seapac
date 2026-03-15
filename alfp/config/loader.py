"""
skills_config.json 로더.
소스 수정 없이 파일만 수정해 스킬·에이전트 동작을 변경할 수 있도록 합니다.
"""

import json
from pathlib import Path
from typing import Any

# 기본값 (파일 없거나 키 누락 시 사용)
_DEFAULTS = {
    "energy_forecast": {
        "model_selection": {"lgbm_min_samples": 5000, "default_model": "xgboost"},
        "evaluate_forecast": {"mape_min_actual_kw": 1.0},
    },
    "tariff_analysis": {
        "interval_hours": 0.25,
        "tou_periods": {
            "off_peak": {"hours": [0, 1, 2, 3, 4, 5, 6, 7, 8, 23], "multiplier": 0.7},
            "mid_peak": {"hours": [9, 10, 11, 13, 14, 15, 16, 17, 21, 22], "multiplier": 1.0},
            "on_peak": {"hours": [12, 18, 19, 20], "multiplier": 1.5},
        },
    },
    "ess_optimization": {
        "defaults": {
            "bess_kwh_cap": 50.0,
            "bess_kw_cap": 25.0,
            "min_soc_pct": 0.20,
            "max_soc_pct": 0.90,
            "initial_soc_pct": 0.50,
            "dt_h": 0.25,
        },
        "peak_shaving": {"default_peak_quantile": 0.75, "charge_threshold_ratio": 0.5},
        "tou_schedule": {"price_high_quantile": 0.70, "price_low_quantile": 0.30},
    },
    "decision_agent": {
        "ess": {"bess_kwh_cap": 50.0, "bess_kw_cap": 25.0, "dt_h": 0.25},
        "peak_threshold_quantile": 0.85,
        "trading": {"surplus_kw_min": 0.5, "max_recommendations": 10},
        "demand_response": {"reduction_factor": 0.3},
        "tariff_fallback": {"default_price_buy_krw": 100.0},
        "llm_temperature": 0.2,
    },
    "forecast_planner": {
        "fallback": {
            "lgbm": {"num_leaves_energy_hub": 127, "num_leaves_default": 63, "n_estimators": 500, "learning_rate": 0.05},
            "xgboost": {"max_depth": 6, "n_estimators": 300, "learning_rate": 0.05},
        },
    },
    "validation": {
        "kpi": {"mape_target_pct": 10.0, "peak_acc_target_pct": 90.0},
    },
}

_CONFIG_PATH = Path(__file__).resolve().parent / "skills_config.json"
_cached: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """재귀적으로 override 로 base 를 덮어씁니다. base/override 는 변경하지 않습니다."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_skills_config() -> dict:
    """
    skills_config.json 을 읽어 기본값과 병합한 설정을 반환합니다.
    파일이 없거나 키가 없으면 기본값이 사용됩니다.
    """
    global _cached
    if _cached is not None:
        return _cached
    if _CONFIG_PATH.exists():
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            _cached = _deep_merge(_DEFAULTS, loaded)
        except (json.JSONDecodeError, OSError):
            _cached = dict(_DEFAULTS)
    else:
        _cached = dict(_DEFAULTS)
    return _cached


def reload_skills_config() -> dict:
    """캐시를 비우고 설정을 다시 읽습니다. (테스트/동적 반영용)"""
    global _cached
    _cached = None
    return get_skills_config()
