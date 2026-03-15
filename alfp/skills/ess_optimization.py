"""
ESSOptimizationSkill - ESS 충방전 스케줄 및 Peak Shaving 최적화
설정: alfp/config/skills_config.json 의 ess_optimization 섹션
"""

import numpy as np
import pandas as pd

from alfp.config import get_skills_config


def _ess_defaults() -> dict:
    """설정에서 ESS 기본값 로드."""
    return get_skills_config().get("ess_optimization", {}).get("defaults", {
        "bess_kwh_cap": 50.0, "bess_kw_cap": 25.0, "min_soc_pct": 0.20,
        "max_soc_pct": 0.90, "initial_soc_pct": 0.50, "dt_h": 0.25,
    })


class ESSOptimizationSkill:
    """
    ESS 운영 최적화 스킬.

    - Peak Shaving: 피크 시간대 방전으로 최대 부하 저감
    - Valley Filling: 저부하 시간대 충전으로 배터리 효율 향상
    - TOU 최적화: 요금제 연동 충방전 전략
    """

    def __init__(
        self,
        bess_kwh_cap: float = None,
        bess_kw_cap: float = None,
        min_soc_pct: float = None,
        max_soc_pct: float = None,
        initial_soc_pct: float = None,
        dt_h: float = None,
    ):
        d = _ess_defaults()
        self.bess_kwh_cap = bess_kwh_cap if bess_kwh_cap is not None else d["bess_kwh_cap"]
        self.bess_kw_cap = bess_kw_cap if bess_kw_cap is not None else d["bess_kw_cap"]
        min_pct = min_soc_pct if min_soc_pct is not None else d["min_soc_pct"]
        max_pct = max_soc_pct if max_soc_pct is not None else d["max_soc_pct"]
        init_pct = initial_soc_pct if initial_soc_pct is not None else d["initial_soc_pct"]
        self.dt_h = dt_h if dt_h is not None else d["dt_h"]
        self.min_soc = self.bess_kwh_cap * min_pct
        self.max_soc = self.bess_kwh_cap * max_pct
        self.soc = self.bess_kwh_cap * init_pct

    def peak_shaving_schedule(
        self, net_load: pd.Series, timestamps: pd.Series, peak_limit_kw: float = None
    ) -> pd.DataFrame:
        """
        Peak Shaving 스케줄 생성.

        Args:
            net_load: 예측 Net Load 시리즈
            timestamps: 타임스탬프 시리즈
            peak_limit_kw: 피크 제한값 (None이면 75th percentile)

        Returns:
            스케줄 DataFrame
        """
        ps_cfg = get_skills_config().get("ess_optimization", {}).get("peak_shaving", {})
        default_quantile = ps_cfg.get("default_peak_quantile", 0.75)
        charge_ratio = ps_cfg.get("charge_threshold_ratio", 0.5)
        if peak_limit_kw is None:
            peak_limit_kw = float(net_load.quantile(default_quantile))

        schedule = []
        soc = self.soc

        for ts, nl in zip(timestamps, net_load):
            if nl > peak_limit_kw and soc > self.min_soc:
                # 방전 (피크 초과분 공급)
                needed = nl - peak_limit_kw
                available = (soc - self.min_soc) / self.dt_h
                discharge = min(needed, available, self.bess_kw_cap)
                soc -= discharge * self.dt_h
                action, power = "discharge", round(discharge, 2)
            elif nl < peak_limit_kw * charge_ratio and soc < self.max_soc:
                # 충전 (여유 시간대)
                headroom = (self.max_soc - soc) / self.dt_h
                charge = min(headroom, self.bess_kw_cap)
                soc += charge * self.dt_h
                action, power = "charge", round(charge, 2)
            else:
                action, power = "idle", 0.0

            schedule.append({
                "timestamp": ts,
                "net_load_kw": round(float(nl), 2),
                "action": action,
                "power_kw": power,
                "soc_kwh": round(soc, 2),
                "soc_pct": round(soc / self.bess_kwh_cap * 100, 1),
            })

        return pd.DataFrame(schedule)

    def tou_schedule(
        self,
        net_load: pd.Series,
        timestamps: pd.Series,
        price_series: pd.Series,
    ) -> pd.DataFrame:
        """
        TOU(Time-of-Use) 요금제 기반 충방전 전략.
        높은 요금 → 방전, 낮은 요금 → 충전.
        """
        tou_cfg = get_skills_config().get("ess_optimization", {}).get("tou_schedule", {})
        q_high = tou_cfg.get("price_high_quantile", 0.70)
        q_low = tou_cfg.get("price_low_quantile", 0.30)
        price_high = float(price_series.quantile(q_high))
        price_low = float(price_series.quantile(q_low))
        soc = self.soc
        schedule = []

        for ts, nl, price in zip(timestamps, net_load, price_series):
            if float(price) >= price_high and soc > self.min_soc:
                available = (soc - self.min_soc) / self.dt_h
                discharge = min(available, self.bess_kw_cap, float(nl))
                soc -= discharge * self.dt_h
                action, power = "discharge", round(discharge, 2)
            elif float(price) <= price_low and soc < self.max_soc:
                headroom = (self.max_soc - soc) / self.dt_h
                charge = min(headroom, self.bess_kw_cap)
                soc += charge * self.dt_h
                action, power = "charge", round(charge, 2)
            else:
                action, power = "idle", 0.0

            schedule.append({
                "timestamp": ts,
                "price_buy": round(float(price), 2),
                "net_load_kw": round(float(nl), 2),
                "action": action,
                "power_kw": power,
                "soc_kwh": round(soc, 2),
            })

        return pd.DataFrame(schedule)

    def summarize(self, schedule_df: pd.DataFrame) -> dict:
        """스케줄 요약 통계."""
        return {
            "total_steps": len(schedule_df),
            "charge_steps": int((schedule_df["action"] == "charge").sum()),
            "discharge_steps": int((schedule_df["action"] == "discharge").sum()),
            "idle_steps": int((schedule_df["action"] == "idle").sum()),
            "total_charged_kwh": round(
                schedule_df[schedule_df["action"] == "charge"]["power_kw"].sum() * self.dt_h, 2
            ),
            "total_discharged_kwh": round(
                schedule_df[schedule_df["action"] == "discharge"]["power_kw"].sum() * self.dt_h, 2
            ),
            "min_soc_kwh": round(schedule_df["soc_kwh"].min(), 2),
            "max_soc_kwh": round(schedule_df["soc_kwh"].max(), 2),
        }
