"""
TariffAnalysisSkill - TOU 요금 분석 및 최적 충방전 시간대 계산
설정: alfp/config/skills_config.json 의 tariff_analysis 섹션
"""

import numpy as np
import pandas as pd

from alfp.config import get_skills_config


def _get_tou_periods() -> dict:
    """설정에서 TOU 구간을 로드. hours 는 리스트로 저장되어 있음."""
    cfg = get_skills_config().get("tariff_analysis", {})
    return cfg.get("tou_periods", {
        "off_peak": {"hours": [0, 1, 2, 3, 4, 5, 6, 7, 8, 23], "multiplier": 0.7},
        "mid_peak": {"hours": [9, 10, 11, 13, 14, 15, 16, 17, 21, 22], "multiplier": 1.0},
        "on_peak": {"hours": [12, 18, 19, 20], "multiplier": 1.5},
    })


def _get_interval_hours() -> float:
    """설정에서 구간 길이(시간). 15분 = 0.25."""
    cfg = get_skills_config().get("tariff_analysis", {})
    return float(cfg.get("interval_hours", 0.25))


class TariffAnalysisSkill:
    """
    전기요금(Tariff) 분석 스킬.

    - TOU(Time-of-Use) 요금 구간 분류
    - 요금 기반 최적 에너지 사용 패턴 분석
    - 요금 절감 시뮬레이션
    """

    @property
    def TOU_PERIODS(self) -> dict:
        """한국 산업용 TOU 기준 (설정 파일에서 변경 가능)."""
        return _get_tou_periods()

    def classify_period(self, hour: int) -> str:
        """시간에 따라 TOU 구간을 반환합니다."""
        for period, info in self.TOU_PERIODS.items():
            if hour in info["hours"]:
                return period
        return "mid_peak"

    def analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        타임스탬프 기반 TOU 구간 및 요금 배율을 분석합니다.

        Args:
            df: 'timestamp', 'price_buy', 'load_kw' 컬럼 필요

        Returns:
            TOU 분석이 추가된 DataFrame
        """
        result = df.copy()
        result["hour"] = result["timestamp"].dt.hour
        result["tou_period"] = result["hour"].apply(self.classify_period)
        result["tou_multiplier"] = result["tou_period"].map(
            {k: v["multiplier"] for k, v in self.TOU_PERIODS.items()}
        )
        interval_h = _get_interval_hours()
        result["estimated_cost"] = result["load_kw"] * result["price_buy"] * interval_h
        return result

    def cost_saving_simulation(
        self,
        df: pd.DataFrame,
        ess_schedule: pd.DataFrame,
    ) -> dict:
        """
        ESS 운영에 따른 요금 절감 시뮬레이션.

        Args:
            df: 원본 부하 + 요금 DataFrame
            ess_schedule: ESS 스케줄 DataFrame (action, power_kw)

        Returns:
            절감 시뮬레이션 결과 dict
        """
        interval_h = _get_interval_hours()
        base_cost = float((df["load_kw"] * df["price_buy"] * interval_h).sum())

        # ESS 방전으로 grid 구매 감소, 충전 시 grid 구매 증가
        merged = pd.merge(
            df[["timestamp", "load_kw", "price_buy"]],
            ess_schedule[["timestamp", "action", "power_kw"]],
            on="timestamp",
            how="left",
        ).fillna({"power_kw": 0, "action": "idle"})

        merged["adjusted_load"] = merged["load_kw"].copy()
        merged.loc[merged["action"] == "discharge", "adjusted_load"] -= merged["power_kw"]
        merged.loc[merged["action"] == "charge", "adjusted_load"] += merged["power_kw"]
        merged["adjusted_load"] = merged["adjusted_load"].clip(lower=0)
        merged["adjusted_cost"] = merged["adjusted_load"] * merged["price_buy"] * interval_h

        adjusted_cost = float(merged["adjusted_cost"].sum())
        saving = base_cost - adjusted_cost
        saving_pct = saving / base_cost * 100 if base_cost > 0 else 0

        return {
            "base_cost_krw": round(base_cost, 0),
            "adjusted_cost_krw": round(adjusted_cost, 0),
            "saving_krw": round(saving, 0),
            "saving_pct": round(saving_pct, 2),
        }

    def summarize_by_period(self, df: pd.DataFrame) -> pd.DataFrame:
        """TOU 구간별 평균 부하 및 요금 통계."""
        analyzed = self.analyze(df)
        return (
            analyzed.groupby("tou_period")
            .agg(
                avg_load_kw=("load_kw", "mean"),
                avg_price=("price_buy", "mean"),
                total_cost=("estimated_cost", "sum"),
                n_steps=("load_kw", "count"),
            )
            .round(3)
            .reset_index()
        )
