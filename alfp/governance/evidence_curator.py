"""
Evidence Curator (PRD §4.1 — langchain_deepagent_architecture_prd.md).

의사결정 근거를 구조화하여 저장. Audit, 전략 재사용, Critic 분석, Strategy Memory에 활용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class EvidenceCuratorOutput:
    """Evidence Curator 저장 데이터 (PRD §4.1)."""
    task_id: str = ""
    context_summary: str = ""
    data_sources: list[str] = field(default_factory=list)
    reasoning_summary: str = ""
    alternatives: list[dict] = field(default_factory=list)
    chosen_strategy: dict = field(default_factory=dict)
    confidence_score: float = 0.0
    confidence_factors: list[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "context_summary": self.context_summary,
            "data_sources": self.data_sources,
            "reasoning_summary": self.reasoning_summary,
            "alternatives": self.alternatives,
            "chosen_strategy": self.chosen_strategy,
            "confidence_score": self.confidence_score,
            "confidence_factors": self.confidence_factors,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidenceCuratorOutput":
        return cls(
            task_id=d.get("task_id", ""),
            context_summary=d.get("context_summary", ""),
            data_sources=list(d.get("data_sources") or []),
            reasoning_summary=d.get("reasoning_summary", ""),
            alternatives=list(d.get("alternatives") or []),
            chosen_strategy=dict(d.get("chosen_strategy") or {}),
            confidence_score=float(d.get("confidence_score", 0.0)),
            confidence_factors=list(d.get("confidence_factors") or []),
            created_at=d.get("created_at", ""),
        )


def _evidence_confidence(metrics: dict, decisions: dict, plan: dict) -> tuple[float, list[str]]:
    kpi = metrics.get("kpi") or {}
    comparisons = decisions.get("candidate_comparisons") or []
    selected = decisions.get("selected_candidate") or {}
    scenario_mode = decisions.get("scenario_mode") or "day_ahead"
    score = 0.25
    factors: list[str] = []

    if kpi.get("MAPE_pass") is True:
        score += 0.18
        factors.append("MAPE KPI를 통과해 예측 기반 전략 신뢰도가 높습니다.")
    else:
        factors.append("MAPE KPI 미달로 예측 기반 의사결정의 불확실성이 남아 있습니다.")

    if kpi.get("peak_acc_pass") is True:
        score += 0.18
        factors.append("피크 정확도 KPI를 통과해 피크 대응 전략의 실행 가능성이 높습니다.")
    else:
        factors.append("피크 정확도 미달로 ESS/DR 피크 대응의 보수적 해석이 필요합니다.")

    if len(comparisons) >= 2:
        top = float(comparisons[0].get("overall_score", 0.0))
        second = float(comparisons[1].get("overall_score", 0.0))
        margin = top - second
        if margin >= 0.08:
            score += 0.14
            factors.append(f"상위 후보 간 점수 차이({margin:.3f})가 커 선택 우위가 분명합니다.")
        elif margin >= 0.03:
            score += 0.07
            factors.append(f"상위 후보 간 점수 차이({margin:.3f})가 제한적이라 대체안 검토가 필요합니다.")
        else:
            factors.append(f"상위 후보 간 점수 차이({margin:.3f})가 매우 작아 선택 불확실성이 큽니다.")

    risk = float(selected.get("risk_score", 0.5) or 0.5)
    policy = float(selected.get("policy_violation_probability", 0.2) or 0.2)
    if risk <= 0.3:
        score += 0.10
        factors.append("선택 전략의 운영 리스크가 낮은 편입니다.")
    else:
        factors.append("선택 전략의 운영 리스크가 충분히 낮지 않습니다.")

    if policy <= 0.15:
        score += 0.10
        factors.append("정책 위반 가능성이 낮아 승인 가능성이 높습니다.")
    else:
        factors.append("정책 위반 가능성이 높아 governance 재검토가 필요합니다.")

    if scenario_mode == "anomaly_response":
        score -= 0.08
        factors.append("이상상황 대응 모드라 정상 상황보다 confidence를 보수적으로 조정합니다.")

    if decisions.get("strategy_candidates"):
        score += 0.05
        factors.append(f"{len(decisions.get('strategy_candidates') or [])}개 후보를 비교한 뒤 선택했습니다.")

    return round(max(0.05, min(score, 0.95)), 2), factors


def curate_evidence(
    state: dict[str, Any],
    task_id: str | None = None,
) -> EvidenceCuratorOutput:
    """
    의사결정 근거를 구조화하여 Evidence Curator 출력 생성.
    """
    decisions = state.get("decisions") or {}
    plan = state.get("forecast_plan") or {}
    metrics = state.get("validation_metrics") or {}
    prosumer_id = state.get("prosumer_id", "unknown")

    if not task_id:
        task_id = f"{prosumer_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    kpi = metrics.get("kpi") or {}
    context_parts = [
        f"Prosumer: {prosumer_id}",
        f"Plan: model={plan.get('selected_model')}, horizon={plan.get('forecast_horizon_steps')} steps",
        f"Validation: MAPE_pass={kpi.get('MAPE_pass')}, peak_acc_pass={kpi.get('peak_acc_pass')}",
        f"Decision scenario={decisions.get('scenario_mode', 'day_ahead')}, selected={decisions.get('selected_candidate_id', 'N/A')}",
    ]
    context_summary = "; ".join(context_parts)

    data_sources = ["feature_df", "load_forecast", "pv_forecast", "net_load_forecast"]
    if plan.get("llm_reasoning"):
        data_sources.append("llm_forecast_planner")
    if decisions.get("llm_strategy"):
        data_sources.append("llm_decision_agent")
    if decisions.get("strategy_candidates"):
        data_sources.append("decision_strategy_candidates")

    reasoning_parts = [plan.get("llm_reasoning", "") or "Rule-based fallback"]
    llm_strat = decisions.get("llm_strategy") or {}
    if isinstance(llm_strat, dict) and llm_strat.get("overall_recommendation"):
        reasoning_parts.append(str(llm_strat.get("overall_recommendation", ""))[:500])
    if decisions.get("candidate_comparisons"):
        top_summaries = [
            c.get("summary", "")
            for c in (decisions.get("candidate_comparisons") or [])[:3]
            if c.get("summary")
        ]
        if top_summaries:
            reasoning_parts.append("Candidate comparison: " + " | ".join(top_summaries))
    reasoning_summary = "\n".join(part for part in reasoning_parts if part)

    alternatives = []
    for candidate in (decisions.get("strategy_candidates") or [])[:5]:
        alternatives.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "ess_mode": candidate.get("ess_mode"),
                "trading_variant": candidate.get("trading_variant"),
                "dr_variant": candidate.get("dr_variant"),
                "expected_profit_krw": candidate.get("expected_profit_krw"),
                "risk_score": candidate.get("risk_score"),
                "policy_violation_probability": candidate.get("policy_violation_probability"),
            }
        )
    if not alternatives and plan.get("selected_model"):
        alternatives.append({"type": "model_choice", "chosen": plan["selected_model"], "options": ["lgbm", "xgboost"]})

    selected_candidate = decisions.get("selected_candidate") or {}
    chosen_strategy = {
        "selected_candidate_id": decisions.get("selected_candidate_id"),
        "scenario_mode": decisions.get("scenario_mode"),
        "ess": decisions.get("ess_summary") or selected_candidate.get("ess_summary") or {},
        "trading_summary": decisions.get("trading_summary") or selected_candidate.get("trading_summary") or {},
        "dr_summary": decisions.get("dr_summary") or selected_candidate.get("dr_summary") or {},
        "tariff_saving": decisions.get("tariff_saving") or selected_candidate.get("tariff_saving") or {},
        "candidate_metrics": {
            "expected_profit_krw": selected_candidate.get("expected_profit_krw"),
            "risk_score": selected_candidate.get("risk_score"),
            "policy_violation_probability": selected_candidate.get("policy_violation_probability"),
            "battery_degradation_cost_krw": selected_candidate.get("battery_degradation_cost_krw"),
        },
    }

    confidence_score, confidence_factors = _evidence_confidence(metrics, decisions, plan)

    return EvidenceCuratorOutput(
        task_id=task_id,
        context_summary=context_summary,
        data_sources=data_sources,
        reasoning_summary=reasoning_summary[:2500],
        alternatives=alternatives,
        chosen_strategy=chosen_strategy,
        confidence_score=confidence_score,
        confidence_factors=confidence_factors,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
