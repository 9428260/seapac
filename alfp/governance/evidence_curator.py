"""
Evidence Curator (PRD §4.1 — langchain_deepagent_architecture_prd.md).

의사결정 근거를 구조화하여 저장. Audit, 전략 재사용, Critic 분석, Strategy Memory에 활용.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from datetime import datetime


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
            created_at=d.get("created_at", ""),
        )


def curate_evidence(
    state: dict[str, Any],
    task_id: str | None = None,
) -> EvidenceCuratorOutput:
    """
    의사결정 근거를 구조화하여 Evidence Curator 출력 생성.

    Args:
        state: ALFPState 또는 decisions/forecast_plan/validation_metrics 포함 dict
        task_id: 작업 식별자 (미제공 시 prosumer_id + timestamp 기반 생성)

    Returns:
        EvidenceCuratorOutput (Audit, Critic, Strategy Memory 입력용)
    """
    decisions = state.get("decisions") or {}
    plan = state.get("forecast_plan") or {}
    metrics = state.get("validation_metrics") or {}
    prosumer_id = state.get("prosumer_id", "unknown")

    if not task_id:
        task_id = f"{prosumer_id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

    # Context summary: 프로슈머·계획·검증 요약
    kpi = metrics.get("kpi") or {}
    context_parts = [
        f"Prosumer: {prosumer_id}",
        f"Plan: model={plan.get('selected_model')}, horizon={plan.get('forecast_horizon_steps')} steps",
        f"Validation: MAPE_pass={kpi.get('MAPE_pass')}, peak_acc_pass={kpi.get('peak_acc_pass')}",
    ]
    context_summary = "; ".join(context_parts)

    # Data sources: 사용된 데이터/모델 출처
    data_sources = ["feature_df", "load_forecast", "pv_forecast", "net_load_forecast"]
    if plan.get("llm_reasoning"):
        data_sources.append("llm_forecast_planner")
    if decisions.get("llm_strategy"):
        data_sources.append("llm_decision_agent")

    # Reasoning summary: 계획·의사결정 LLM 근거
    reasoning_summary = plan.get("llm_reasoning", "") or "Rule-based fallback"
    llm_strat = decisions.get("llm_strategy") or {}
    if isinstance(llm_strat, dict) and llm_strat.get("overall_recommendation"):
        reasoning_summary += "\n" + str(llm_strat.get("overall_recommendation", ""))[:500]

    # Alternatives: 검증 실패 시 재계획 대안 등 (재계획 시 state에 있으면 활용)
    alternatives = []
    if plan.get("llm_risk_factors"):
        alternatives.append({"type": "risk_factors", "items": plan["llm_risk_factors"]})
    if plan.get("selected_model"):
        alternatives.append({"type": "model_choice", "chosen": plan["selected_model"], "options": ["lgbm", "xgboost"]})

    # Chosen strategy: ESS·거래·DR 요약
    ess_summary = decisions.get("ess_summary") or {}
    chosen_strategy = {
        "ess": ess_summary,
        "trading_summary": decisions.get("trading_summary") or {},
        "dr_summary": decisions.get("dr_summary") or {},
        "tariff_saving": decisions.get("tariff_saving") or {},
    }

    # Confidence: 검증 KPI 통과 시 높게, 미통과 시 낮게
    confidence_score = 0.8
    if kpi.get("MAPE_pass") is False or kpi.get("peak_acc_pass") is False:
        confidence_score = 0.4

    return EvidenceCuratorOutput(
        task_id=task_id,
        context_summary=context_summary,
        data_sources=data_sources,
        reasoning_summary=reasoning_summary[:2000],
        alternatives=alternatives,
        chosen_strategy=chosen_strategy,
        confidence_score=confidence_score,
        created_at=datetime.utcnow().isoformat() + "Z",
    )
