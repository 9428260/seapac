"""
Critic / Red-Team Agent (PRD §4.2 — langchain_deepagent_architecture_prd.md).

Agent 전략을 공격적으로 검토: 리스크 분석, 반례 탐색, 실패 시나리오, 대안 전략 제시.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from alfp.deepagents import invoke_deepagents_governance_critic
from alfp.governance.evidence_curator import EvidenceCuratorOutput


@dataclass
class CriticAgentOutput:
    """Critic Agent 출력 (PRD §4.2)."""
    risk_score: float = 0.0  # 0.0 = low risk, 1.0 = high risk
    failure_scenarios: list[str] = field(default_factory=list)
    counterexamples: list[str] = field(default_factory=list)
    alternative_strategy: list[dict] = field(default_factory=list)
    revised_candidate_id: str = ""
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_score": self.risk_score,
            "failure_scenarios": self.failure_scenarios,
            "counterexamples": self.counterexamples,
            "alternative_strategy": self.alternative_strategy,
            "revised_candidate_id": self.revised_candidate_id,
            "recommendation": self.recommendation,
        }


def _critic_rule_based(evidence: EvidenceCuratorOutput, state: dict[str, Any]) -> CriticAgentOutput:
    """규칙 기반 Critic: confidence 외에 후보 비교, 정책 위험, 열화 비용까지 반영."""
    out = CriticAgentOutput()
    decisions = state.get("decisions") or {}
    comparisons = decisions.get("candidate_comparisons") or []
    selected = decisions.get("selected_candidate") or {}
    selected_id = decisions.get("selected_candidate_id") or selected.get("candidate_id", "")

    risk = (1.0 - evidence.confidence_score) * 0.45
    risk += float(selected.get("risk_score", 0.4) or 0.4) * 0.35
    risk += float(selected.get("policy_violation_probability", 0.1) or 0.1) * 0.2

    if selected.get("battery_degradation_cost_krw", 0) and float(selected.get("battery_degradation_cost_krw", 0)) > 150:
        risk += 0.08
        out.failure_scenarios.append("배터리 열화 비용이 높아 장기 운영 비용이 증가할 수 있습니다.")

    if evidence.confidence_score < 0.6:
        out.failure_scenarios.extend(
            [
                "예측 KPI 신뢰도가 충분하지 않아 ESS/거래 조합의 실행 성과 편차가 커질 수 있습니다.",
                "현재 선택안이 상위 대체안 대비 뚜렷하게 우월하지 않을 수 있습니다.",
            ]
        )

    if len(comparisons) >= 2:
        top = comparisons[0]
        second = comparisons[1]
        margin = float(top.get("overall_score", 0.0)) - float(second.get("overall_score", 0.0))
        if margin < 0.03:
            risk += 0.08
            out.failure_scenarios.append("상위 후보 간 점수 차이가 작아 반례 후보가 실제 운영에서 더 나을 수 있습니다.")
        for item in comparisons:
            if item.get("candidate_id") == selected_id:
                continue
            if float(item.get("overall_score", 0.0)) >= float(top.get("overall_score", 0.0)) - 0.02 and (
                float(item.get("risk_score", 1.0)) < float(selected.get("risk_score", 1.0))
                or float(item.get("policy_violation_probability", 1.0)) < float(selected.get("policy_violation_probability", 1.0))
            ):
                note = (
                    f"{item.get('candidate_id')} 후보는 유사한 점수이면서 risk={item.get('risk_score')}, "
                    f"policy={item.get('policy_violation_probability')}로 더 안전합니다."
                )
                out.counterexamples.append(note)
                out.alternative_strategy.append(
                    {
                        "candidate_id": item.get("candidate_id"),
                        "reason": note,
                        "action": "candidate_switch",
                    }
                )
        if out.alternative_strategy:
            out.revised_candidate_id = out.alternative_strategy[0]["candidate_id"]

    if decisions.get("scenario_mode") == "anomaly_response" and selected.get("trading_variant") == "aggressive":
        risk += 0.1
        out.failure_scenarios.append("이상상황 대응 모드에서 공격적 거래 전략은 안정성보다 수익을 과도하게 우선할 수 있습니다.")

    out.risk_score = round(min(risk, 0.98), 2)
    if out.revised_candidate_id:
        out.recommendation = (
            f"현재 선택안 대신 {out.revised_candidate_id} 후보를 우선 재검토하는 것이 더 안전합니다. "
            "Policy Gate에서 REPLAN_REQUIRED 또는 후보 교체를 검토해야 합니다."
        )
    elif out.risk_score >= 0.45:
        out.recommendation = "현재 전략은 승인 전 재계획 또는 보수적 수정안 검토가 필요합니다."
    else:
        out.recommendation = "현재 전략은 반례 검토 결과 상대적으로 안정적입니다."
    return out


def run_critic_agent(
    evidence: EvidenceCuratorOutput,
    state: dict[str, Any],
    use_llm: bool = False,
) -> CriticAgentOutput:
    """
    Critic Agent 실행: 전략 리스크·실패 시나리오·대안·권고 산출.
    """
    if use_llm:
        try:
            return _critic_deepagent(evidence, state)
        except Exception:
            pass
    return _critic_rule_based(evidence, state)


def _critic_deepagent(evidence: EvidenceCuratorOutput, state: dict[str, Any]) -> CriticAgentOutput:
    payload = {
        "evidence": evidence.to_dict(),
        "decisions": state.get("decisions") or {},
        "validation_metrics": state.get("validation_metrics") or {},
        "forecast_plan": state.get("forecast_plan") or {},
    }
    data = invoke_deepagents_governance_critic(context_json=json.dumps(payload, ensure_ascii=False))
    return CriticAgentOutput(
        risk_score=float(data.get("risk_score", 0.5)),
        failure_scenarios=list(data.get("failure_scenarios") or []),
        counterexamples=list(data.get("counterexamples") or []),
        alternative_strategy=list(data.get("alternative_strategy") or []),
        revised_candidate_id=str(data.get("revised_candidate_id", "")),
        recommendation=str(data.get("recommendation", "")),
    )
