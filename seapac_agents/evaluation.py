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

import json
from dataclasses import dataclass, field
from typing import Any

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
    default_dr_acceptance_rate: float = 0.75  # 실제 적용량 산출이 불가능할 때만 사용


# ─────────────────────────────────────────────────────────────────
# KPI 계산 함수
# ─────────────────────────────────────────────────────────────────

def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _compute_energy_cost(df: pd.DataFrame, cfg: EvaluationConfig) -> dict:
    """
    계통 구매 총 비용 (원).

    실제 실행 시계열에서 community_net_kw > 0 인 부족분을 계통 구매로 간주한다.
    스텝별 평균 구매단가가 있으면 동적 단가를 사용하고, 없으면 고정 단가를 사용한다.
    """
    if "community_net_kw" not in df.columns:
        return {
            "grid_import_kwh": 0.0,
            "avg_grid_price_krw_per_kwh": cfg.grid_price_krw_per_kwh,
            "total_grid_cost_krw": 0.0,
            "price_source": "missing_timeseries",
        }

    deficit_kw = df["community_net_kw"].clip(lower=0.0)
    import_kwh = float((deficit_kw * cfg.dt_h).sum())

    if "avg_price_buy_krw_per_kwh" in df.columns and not df["avg_price_buy_krw_per_kwh"].isna().all():
        price_series = df["avg_price_buy_krw_per_kwh"].fillna(cfg.grid_price_krw_per_kwh)
        total_cost = float((deficit_kw * cfg.dt_h * price_series).sum())
        avg_price = (total_cost / import_kwh) if import_kwh > 0 else float(price_series.mean())
        price_source = "timeseries_avg_price_buy"
    else:
        total_cost = import_kwh * cfg.grid_price_krw_per_kwh
        avg_price = cfg.grid_price_krw_per_kwh
        price_source = "config_default"

    return {
        "grid_import_kwh": round(import_kwh, 2),
        "avg_grid_price_krw_per_kwh": round(avg_price, 2),
        "total_grid_cost_krw": round(total_cost, 0),
        "price_source": price_source,
    }


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


def _compute_peak_reduction(summary: dict, df: pd.DataFrame, cfg: EvaluationConfig) -> dict:
    """
    피크 수요 감소율 (%).

    baseline_peak_kw가 제공된 경우에만 실제 시뮬레이션 피크와 비교한다.
    baseline이 없으면 감소율은 계산하지 않고 actual peak만 보고한다.
    """
    if "community_load_kw" not in df.columns:
        return {
            "peak_kw": 0.0,
            "baseline_peak_kw": 0.0,
            "peak_reduction_pct": 0.0,
            "baseline_source": "missing_timeseries",
            "peak_shaving_count": 0,
        }

    peak_kw = _safe_float(summary.get("peak_load_kw"), float(df["community_load_kw"].max()))

    if cfg.baseline_peak_kw > 0:
        baseline = cfg.baseline_peak_kw
        baseline_source = "config_baseline"
    else:
        baseline = peak_kw
        baseline_source = "actual_peak_only"

    reduction_pct = ((baseline - peak_kw) / baseline * 100) if baseline > 0 else 0.0

    return {
        "peak_kw": round(peak_kw, 2),
        "baseline_peak_kw": round(baseline, 2),
        "peak_reduction_pct": round(reduction_pct, 2),
        "baseline_source": baseline_source,
        "peak_shaving_count": int(summary.get("ess_peak_shaving_count", 0) or 0),
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
    df: pd.DataFrame,
    decisions: dict | None = None,
    cfg: EvaluationConfig | None = None,
) -> dict:
    """
    DR 권고 수락율.

    decisions의 권고 감축량과 실제 시뮬레이션 적용 감축량을 비교한다.
    실제 감축량 산출이 불가능할 때만 기본 수락율로 fallback 한다.
    """
    if cfg is None:
        cfg = EvaluationConfig()

    dr_events = 0
    requested_reduction_kwh = 0.0
    if decisions:
        dr_items = decisions.get("demand_response_events") or []
        dr_events = len(dr_items)
        requested_reduction_kwh = sum(
            _safe_float(item.get("recommended_reduction_kw", 0.0)) * cfg.dt_h
            for item in dr_items
        )

    if "dr_reduction_kw" in df.columns:
        actual_reduction_kwh = float((df["dr_reduction_kw"].clip(lower=0.0) * cfg.dt_h).sum())
    else:
        actual_reduction_kwh = _safe_float(summary.get("total_dr_reduction_kwh", 0.0))

    if requested_reduction_kwh > 0:
        acceptance_rate = min(actual_reduction_kwh / requested_reduction_kwh, 1.0)
        accepted_events = int(round(dr_events * acceptance_rate))
        note = "실제 감축량 / 권고 감축량 기준"
    elif dr_events > 0:
        acceptance_rate = cfg.default_dr_acceptance_rate
        accepted_events = round(dr_events * acceptance_rate)
        note = "권고 감축량 정보 부족 — 기본 수락율 적용"
    else:
        acceptance_rate = 0.0 if actual_reduction_kwh <= 0 else cfg.default_dr_acceptance_rate
        accepted_events = 0
        note = "DR 권고 없음"

    return {
        "dr_events_total": dr_events,
        "accepted_events": accepted_events,
        "requested_reduction_kwh": round(requested_reduction_kwh, 2),
        "actual_reduction_kwh": round(actual_reduction_kwh, 2),
        "acceptance_rate_pct": round(acceptance_rate * 100, 1),
        "note": note,
    }


def _compute_execution_quality(summary: dict) -> dict:
    """실행 승인 여부와 오류 현황."""
    validation_approved = bool(summary.get("validation_approved", True))
    simulation_approved = bool(summary.get("simulation_approved", True))
    execution_approved = bool(summary.get("execution_approved", validation_approved and simulation_approved))
    return {
        "execution_approved": execution_approved,
        "validation_approved": validation_approved,
        "simulation_approved": simulation_approved,
        "validation_errors_count": int(summary.get("validation_errors_count", 0) or 0),
        "simulation_approval_errors_count": int(summary.get("simulation_approval_errors_count", 0) or 0),
    }


def _compute_operational_value(
    summary: dict,
    energy_cost: dict,
    ess_degradation: dict,
) -> dict:
    """실행으로 발생한 운영 가치 요약."""
    trading_benefit = _safe_float(summary.get("community_saving_krw", 0.0))
    ess_saving = _safe_float(summary.get("ess_saving_krw", 0.0))
    degradation_cost = _safe_float(ess_degradation.get("ess_degradation_cost_krw", 0.0))
    value_added = trading_benefit + ess_saving - degradation_cost
    net_after_grid_cost = value_added - _safe_float(energy_cost.get("total_grid_cost_krw", 0.0))

    return {
        "trading_benefit_krw": round(trading_benefit, 0),
        "ess_saving_krw": round(ess_saving, 0),
        "value_added_krw": round(value_added, 0),
        "net_after_grid_cost_krw": round(net_after_grid_cost, 0),
    }


# ─────────────────────────────────────────────────────────────────
# 종합 등급 산정
# ─────────────────────────────────────────────────────────────────

def _grade(kpis: dict) -> str:
    """
    KPI 기반 종합 등급 산정 (A/B/C/D).
    """
    execution = kpis.get("execution_quality", {})
    if not execution.get("execution_approved", True):
        return "D"

    peak_red = kpis.get("peak_reduction", {}).get("peak_reduction_pct", 0.0)
    value_added = kpis.get("operational_value", {}).get("value_added_krw", 0.0)
    dr_acceptance = kpis.get("user_acceptance", {}).get("acceptance_rate_pct", 0.0)
    trading = kpis.get("trading_profit", {}).get("community_saving_krw", 0.0)
    ess_discharged = kpis.get("ess_degradation", {}).get("total_discharged_kwh", 0)

    if peak_red >= 10.0 and value_added > 0 and (dr_acceptance >= 70.0 or kpis.get("user_acceptance", {}).get("dr_events_total", 0) == 0):
        return "A"
    if peak_red >= 5.0 or value_added > 0 or trading > 0:
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
    llm_analysis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "n_steps": self.n_steps,
            "grade": self.grade,
            "kpis": self.kpis,
            "summary_text": self.summary_text,
            "llm_analysis": self.llm_analysis,
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
    peak_reduction = _compute_peak_reduction(execution_summary, df, cfg)
    ess_degradation = _compute_ess_degradation(execution_summary, cfg)
    user_acceptance = _compute_user_acceptance(execution_summary, df, decisions, cfg)
    execution_quality = _compute_execution_quality(execution_summary)
    operational_value = _compute_operational_value(execution_summary, energy_cost, ess_degradation)

    kpis = {
        "execution_quality": execution_quality,
        "energy_cost": energy_cost,
        "trading_profit": trading_profit,
        "peak_reduction": peak_reduction,
        "ess_degradation": ess_degradation,
        "user_acceptance": user_acceptance,
        "operational_value": operational_value,
    }

    # ── 종합 등급 ─────────────────────────────────────────────────
    grade = _grade(kpis)

    # ── 자연어 요약 ───────────────────────────────────────────────
    n_steps = int(execution_summary.get("n_steps_run", len(df)))
    phase = int(execution_summary.get("phase", 4))
    peak_kw = peak_reduction["peak_kw"]
    peak_red_pct = peak_reduction["peak_reduction_pct"]
    comm_saving = trading_profit["community_saving_krw"]
    ess_saving = operational_value["ess_saving_krw"]
    degrad_cost = ess_degradation["ess_degradation_cost_krw"]
    value_added = operational_value["value_added_krw"]
    approved_str = "승인" if execution_quality["execution_approved"] else "미승인"

    lines = [
        f"  [Execution]      {approved_str}  (정책={execution_quality['validation_approved']}, 시뮬레이션={execution_quality['simulation_approved']})",
        f"  [Energy Cost]    계통 구매 {energy_cost['grid_import_kwh']:.1f} kWh  →  {energy_cost['total_grid_cost_krw']:,.0f} 원  (단가 {energy_cost['avg_grid_price_krw_per_kwh']:.1f}, {energy_cost['price_source']})",
        f"  [Trading Profit] 커뮤니티 절감: {comm_saving:,.0f} 원  (거래 {trading_profit['total_trades']}건, {trading_profit['total_matched_kwh']:.1f} kWh)",
        f"  [Peak Reduction] 실제 피크 {peak_kw:.1f} kW  →  {peak_red_pct:.1f}% 감소 (기준 {peak_reduction['baseline_peak_kw']:.1f} kW, {peak_reduction['baseline_source']})",
        f"  [ESS]            절감 {ess_saving:,.0f} 원 / 방전 {ess_degradation['total_discharged_kwh']:.1f} kWh / 마모 비용 {degrad_cost:,.0f} 원",
        f"  [User Acceptance] DR {user_acceptance['dr_events_total']}건, 요청 {user_acceptance['requested_reduction_kwh']:.1f} kWh, 실제 {user_acceptance['actual_reduction_kwh']:.1f} kWh, 수락율 {user_acceptance['acceptance_rate_pct']}%",
        f"  ─────────────────────────────────────────",
        f"  운영 가치 (거래+ESS-마모): {value_added:,.0f} 원   →   평가 등급: {grade}",
    ]

    llm_analysis: dict[str, Any] = {}
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from alfp.llm import is_llm_enabled, get_llm

        if is_llm_enabled("evaluation_summary"):
            system = """당신은 에너지 운영 평가 보조 분석기입니다.
KPI와 등급을 해석해 운영자가 읽기 쉬운 한국어 요약과 개선 제안을 작성하세요.
JSON only:
{"executive_summary": string, "strength": string, "risk": string, "next_action": string}"""
            user = (
                f"grade={grade}\n"
                f"kpis={json.dumps(kpis, ensure_ascii=False)}\n"
                f"execution_summary={json.dumps(execution_summary, ensure_ascii=False)}\n"
                "Output JSON only."
            )
            llm = get_llm(temperature=0.1, stage="evaluation_summary")
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            raw = resp.content if hasattr(resp, "content") else str(resp)
            llm_analysis = json.loads(raw.strip().removeprefix('```json').removesuffix('```').strip())
            if llm_analysis.get("executive_summary"):
                lines.extend([
                    "  ─────────────────────────────────────────",
                    f"  [LLM Summary] {llm_analysis.get('executive_summary', '')}",
                    f"  [LLM Strength] {llm_analysis.get('strength', '')}",
                    f"  [LLM Risk] {llm_analysis.get('risk', '')}",
                    f"  [LLM Next] {llm_analysis.get('next_action', '')}",
                ])
    except Exception:
        llm_analysis = {}
    summary_text = "\n".join(lines)

    return EvaluationReport(
        phase=phase,
        n_steps=n_steps,
        grade=grade,
        kpis=kpis,
        summary_text=summary_text,
        raw_summary=execution_summary,
        llm_analysis=llm_analysis,
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
    execution_summary = dict(execution_result.summary)
    execution_summary["execution_approved"] = bool(getattr(execution_result, "approved", True))
    execution_summary["validation_errors"] = list(getattr(execution_result, "validation_errors", []) or [])
    execution_summary["simulation_approval_errors"] = list(getattr(execution_result, "simulation_approval_errors", []) or [])

    return run_evaluation(
        execution_summary=execution_summary,
        execution_df=execution_result.dataframe,
        decisions=decisions,
        config=config,
    )
