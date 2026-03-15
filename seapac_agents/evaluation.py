"""
Step 5 — Evaluation Engine (PRD: seapac_agentic_prd.md)

시뮬레이션 실행 결과를 KPI로 평가합니다.

KPIs (PRD):
  - Energy Cost        : 계통 구매 총 비용
  - Trading Profit     : 커뮤니티 에너지 거래 수익
  - Peak Reduction     : 피크 수요 감소율
  - ESS Degradation Cost: 배터리 마모 비용
  - User Acceptance    : DR 권고 수락율

입력: Step 4 ExecutionResult (summary dict + DataFrame)
출력: EvaluationReport (KPI dict + 등급 + 자연어 해석)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────
# KPI 설정
# ─────────────────────────────────────────────────────────────────

@dataclass
class EvaluationConfig:
    """KPI 계산 파라미터."""
    # Energy Cost
    grid_price_krw_per_kwh: float = 100.0     # 계통 구매 단가 (원/kWh)
    dt_h: float = 0.25                         # 스텝 시간 간격 (시간 단위, 15분)

    # Peak Reduction
    baseline_peak_kw: float = 0.0             # 기준 피크 (0이면 자체 계산)

    # ESS Degradation
    ess_degradation_cost_per_kwh: float = 5.0  # 사이클 비용 (원/kWh)
    # 참고: 리튬이온 배터리 평균 ~5~10 원/kWh 수준 (750사이클 기준)

    # User Acceptance
    # DR 권고 이벤트 수락율: 실제 수락 데이터가 없으면 기본값 사용
    default_dr_acceptance_rate: float = 0.75  # 75% 기본 가정


# ─────────────────────────────────────────────────────────────────
# KPI 계산 함수
# ─────────────────────────────────────────────────────────────────

def _compute_energy_cost(df: pd.DataFrame, cfg: EvaluationConfig) -> float:
    """
    계통 구매 총 비용 (원).

    community_net_kw > 0 인 스텝(부족분)을 계통에서 구매한 것으로 간주.
    """
    if "community_net_kw" not in df.columns:
        return 0.0
    deficit_kw = df["community_net_kw"].clip(lower=0.0)
    total_kwh = float((deficit_kw * cfg.dt_h).sum())
    return round(total_kwh * cfg.grid_price_krw_per_kwh, 0)


def _compute_trading_profit(summary: dict) -> dict:
    """
    P2P 거래 수익 및 절감액 (원).

    summary: ALFPSimulationModel.summary() 반환값
    """
    return {
        "seller_revenue_krw":   summary.get("seller_revenue_krw", 0),
        "buyer_saving_krw":     summary.get("buyer_saving_krw", 0),
        "community_saving_krw": summary.get("community_saving_krw", 0),
        "total_matched_kwh":    summary.get("total_matched_kwh", 0),
        "total_trades":         summary.get("total_trades", 0),
    }


def _compute_peak_reduction(df: pd.DataFrame, cfg: EvaluationConfig) -> dict:
    """
    피크 수요 감소율 (%).

    baseline이 지정되지 않으면 ESS 방전 전 부하(community_net_kw + ESS 방전)를 역산.
    """
    if "community_load_kw" not in df.columns:
        return {"peak_kw": 0.0, "baseline_peak_kw": 0.0, "peak_reduction_pct": 0.0}

    peak_kw = float(df["community_load_kw"].max())

    if cfg.baseline_peak_kw > 0:
        baseline = cfg.baseline_peak_kw
    else:
        # ESS 방전이 없었을 때의 추정 피크 (ess_power_kw < 0 → 방전)
        if "ess_power_kw" in df.columns:
            discharge = df["ess_power_kw"].clip(upper=0).abs()  # 방전량 (양수)
            baseline = float((df["community_load_kw"] + discharge).max())
        else:
            baseline = peak_kw  # 방전 정보 없으면 동일

    reduction_pct = ((baseline - peak_kw) / baseline * 100) if baseline > 0 else 0.0

    return {
        "peak_kw": round(peak_kw, 2),
        "baseline_peak_kw": round(baseline, 2),
        "peak_reduction_pct": round(reduction_pct, 2),
        "peak_shaving_count": int(
            (df["community_load_kw"] < baseline * 0.9).sum()
        ) if baseline > 0 else 0,
    }


def _compute_ess_degradation(summary: dict, cfg: EvaluationConfig) -> dict:
    """
    ESS 마모 비용 추정 (원).

    총 방전량(kWh) × 사이클 비용(원/kWh)
    """
    discharged_kwh = float(summary.get("ess_total_discharged_kwh", 0.0))
    degradation_cost = round(discharged_kwh * cfg.ess_degradation_cost_per_kwh, 0)

    return {
        "total_discharged_kwh":    round(discharged_kwh, 2),
        "degradation_cost_per_kwh": cfg.ess_degradation_cost_per_kwh,
        "ess_degradation_cost_krw": degradation_cost,
    }


def _compute_user_acceptance(
    summary: dict,
    decisions: dict | None = None,
    cfg: EvaluationConfig | None = None,
) -> dict:
    """
    DR 권고 수락율 추정.

    실제 수락 데이터 없을 경우 default_dr_acceptance_rate 사용.
    decisions가 있으면 DR 이벤트 수를 기록.
    """
    if cfg is None:
        cfg = EvaluationConfig()

    dr_events = 0
    if decisions:
        dr_events = len(decisions.get("demand_response_events") or [])

    acceptance_rate = cfg.default_dr_acceptance_rate
    accepted_events = round(dr_events * acceptance_rate)

    return {
        "dr_events_total": dr_events,
        "accepted_events": accepted_events,
        "acceptance_rate_pct": round(acceptance_rate * 100, 1),
        "note": "실측 수락 데이터 없음 — 기본값 적용" if dr_events == 0 else "기본 수락율 적용",
    }


# ─────────────────────────────────────────────────────────────────
# 종합 등급 산정
# ─────────────────────────────────────────────────────────────────

def _grade(kpis: dict) -> str:
    """
    KPI 기반 종합 등급 산정 (A/B/C/D).

    기준:
      A: peak_reduction >= 10% AND trading_profit > 0 AND energy_cost 합리적
      B: peak_reduction >= 5% OR trading_profit > 0
      C: ESS 동작 있음
      D: 기타
    """
    peak_red = kpis.get("peak_reduction", {}).get("peak_reduction_pct", 0.0)
    trading = kpis.get("trading_profit", {}).get("community_saving_krw", 0)
    ess_discharged = kpis.get("ess_degradation", {}).get("total_discharged_kwh", 0)

    if peak_red >= 10.0 and trading > 0:
        return "A"
    if peak_red >= 5.0 or trading > 0:
        return "B"
    if ess_discharged > 0:
        return "C"
    return "D"


# ─────────────────────────────────────────────────────────────────
# EvaluationReport
# ─────────────────────────────────────────────────────────────────

@dataclass
class EvaluationReport:
    """Step 5 평가 결과 보고서."""
    phase: int
    n_steps: int
    grade: str
    kpis: dict = field(default_factory=dict)
    summary_text: str = ""
    raw_summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "n_steps": self.n_steps,
            "grade": self.grade,
            "kpis": self.kpis,
            "summary_text": self.summary_text,
        }

    def print_report(self) -> None:
        """콘솔 출력."""
        print("=" * 60)
        print(f"Step 5 — Evaluation Report  (Phase {self.phase}, Grade: {self.grade})")
        print("=" * 60)
        print(self.summary_text)
        print("=" * 60)


# ─────────────────────────────────────────────────────────────────
# 메인 함수: run_evaluation()
# ─────────────────────────────────────────────────────────────────

def run_evaluation(
    execution_summary: dict,
    execution_df: pd.DataFrame | None = None,
    decisions: dict | None = None,
    config: EvaluationConfig | None = None,
) -> EvaluationReport:
    """
    Step 5 Evaluation Engine 메인 진입점.

    Args:
        execution_summary: Step 4 ExecutionResult.summary (ALFPSimulationModel.summary() 포함)
        execution_df: Step 4 ExecutionResult.dataframe (DataCollector 시계열)
        decisions: Step 3 decisions (DR 이벤트 수 파악용, optional)
        config: KPI 계산 파라미터

    Returns:
        EvaluationReport
    """
    cfg = config or EvaluationConfig()
    df = execution_df if execution_df is not None else pd.DataFrame()

    # ── KPI 계산 ─────────────────────────────────────────────────
    energy_cost = _compute_energy_cost(df, cfg)
    trading_profit = _compute_trading_profit(execution_summary)
    peak_reduction = _compute_peak_reduction(df, cfg)
    ess_degradation = _compute_ess_degradation(execution_summary, cfg)
    user_acceptance = _compute_user_acceptance(execution_summary, decisions, cfg)

    kpis = {
        "energy_cost": {
            "total_grid_cost_krw": energy_cost,
            "grid_price_krw_per_kwh": cfg.grid_price_krw_per_kwh,
        },
        "trading_profit": trading_profit,
        "peak_reduction": peak_reduction,
        "ess_degradation": ess_degradation,
        "user_acceptance": user_acceptance,
    }

    # ── 종합 등급 ─────────────────────────────────────────────────
    grade = _grade(kpis)

    # ── 자연어 요약 ───────────────────────────────────────────────
    n_steps = int(execution_summary.get("n_steps_run", len(df)))
    phase = int(execution_summary.get("phase", 4))
    avg_load = float(execution_summary.get("avg_community_load_kw", 0))
    peak_kw = peak_reduction["peak_kw"]
    peak_red_pct = peak_reduction["peak_reduction_pct"]
    comm_saving = trading_profit["community_saving_krw"]
    degrad_cost = ess_degradation["ess_degradation_cost_krw"]
    net_benefit = comm_saving - degrad_cost

    lines = [
        f"  [Energy Cost]    계통 구매 비용: {energy_cost:,.0f} 원  (단가 {cfg.grid_price_krw_per_kwh} 원/kWh)",
        f"  [Trading Profit] 커뮤니티 절감: {comm_saving:,.0f} 원  (거래 {trading_profit['total_trades']}건, {trading_profit['total_matched_kwh']:.1f} kWh)",
        f"  [Peak Reduction] 피크 {peak_kw:.1f} kW  →  {peak_red_pct:.1f}% 감소 (기준 {peak_reduction['baseline_peak_kw']:.1f} kW)",
        f"  [ESS Degradation] 방전 {ess_degradation['total_discharged_kwh']:.1f} kWh  →  마모 비용 {degrad_cost:,.0f} 원",
        f"  [User Acceptance] DR 이벤트 {user_acceptance['dr_events_total']}건, 수락율 {user_acceptance['acceptance_rate_pct']}%",
        f"  ─────────────────────────────────────────",
        f"  순 효과 (절감 - 마모): {net_benefit:,.0f} 원   →   평가 등급: {grade}",
    ]
    summary_text = "\n".join(lines)

    return EvaluationReport(
        phase=phase,
        n_steps=n_steps,
        grade=grade,
        kpis=kpis,
        summary_text=summary_text,
        raw_summary=execution_summary,
    )


def evaluate_from_execution_result(
    execution_result: Any,  # simulation.execution.ExecutionResult
    decisions: dict | None = None,
    config: EvaluationConfig | None = None,
) -> EvaluationReport:
    """
    Step 4 ExecutionResult 객체를 직접 받아 평가 수행.

    Args:
        execution_result: simulation.execution.run_execution() 반환값
        decisions: Step 3 decisions (optional)
        config: 평가 설정

    Returns:
        EvaluationReport
    """
    return run_evaluation(
        execution_summary=execution_result.summary,
        execution_df=execution_result.dataframe,
        decisions=decisions,
        config=config,
    )
