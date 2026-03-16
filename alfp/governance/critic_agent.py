"""
Critic / Red-Team Agent (PRD §4.2 — langchain_deepagent_architecture_prd.md).

Agent 전략을 공격적으로 검토: 리스크 분석, 반례 탐색, 실패 시나리오, 대안 전략 제시.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from alfp.governance.evidence_curator import EvidenceCuratorOutput


@dataclass
class CriticAgentOutput:
    """Critic Agent 출력 (PRD §4.2)."""
    risk_score: float = 0.0  # 0.0 = low risk, 1.0 = high risk
    failure_scenarios: list[str] = field(default_factory=list)
    alternative_strategy: list[dict] = field(default_factory=list)
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_score": self.risk_score,
            "failure_scenarios": self.failure_scenarios,
            "alternative_strategy": self.alternative_strategy,
            "recommendation": self.recommendation,
        }


def _critic_rule_based(evidence: EvidenceCuratorOutput, decisions: dict) -> CriticAgentOutput:
    """규칙 기반 Critic: confidence·전략 요약으로 리스크·실패 시나리오·대안 생성."""
    out = CriticAgentOutput()
    out.risk_score = 1.0 - evidence.confidence_score

    # Low confidence → failure scenarios
    if evidence.confidence_score < 0.6:
        out.failure_scenarios = [
            "예측 MAPE 초과 시 ESS 스케줄 부정확",
            "피크 부정합 시 피크쉐이빙 효과 미달",
            "전력 수요 급변 시 DR 대응 지연",
        ]
        out.alternative_strategy = [
            {"action": "replan", "reason": "KPI 미달 시 다른 모델/파라미터로 재계획"},
            {"action": "conservative_ess", "reason": "SoC 보수 운영으로 리스크 감소"},
        ]
        out.recommendation = "재계획(replan) 또는 보수적 ESS 운영 권장. 정책 게이트에서 REPLAN_REQUIRED 검토."
    else:
        out.failure_scenarios = []
        out.alternative_strategy = []
        out.recommendation = "현재 전략 유지 권장. Policy Gate 통과 후 실행 가능."

    # ESS 과다 방전/충전 리스크
    chosen = evidence.chosen_strategy or {}
    ess = chosen.get("ess") or {}
    n_discharge = ess.get("discharge_steps", 0) or 0
    n_charge = ess.get("charge_steps", 0) or 0
    if n_discharge > 30 or n_charge > 30:
        out.risk_score = min(1.0, out.risk_score + 0.2)
        out.failure_scenarios.append("ESS 사이클 과다 시 배터리 수명 저하 가능")

    return out


def run_critic_agent(
    evidence: EvidenceCuratorOutput,
    state: dict[str, Any],
    use_llm: bool = False,
) -> CriticAgentOutput:
    """
    Critic Agent 실행: 전략 리스크·실패 시나리오·대안·권고 산출.

    Args:
        evidence: Evidence Curator 출력
        state: ALFPState (decisions 등)
        use_llm: True면 LLM 기반 비판 (미구현 시 규칙 기반 사용)

    Returns:
        CriticAgentOutput
    """
    decisions = state.get("decisions") or {}
    if use_llm:
        try:
            return _critic_llm(evidence, state)
        except Exception:
            pass
    return _critic_rule_based(evidence, decisions)


def _critic_llm(evidence: EvidenceCuratorOutput, state: dict[str, Any]) -> CriticAgentOutput:
    """LLM 기반 Critic (선택적)."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.output_parsers import JsonOutputParser
    from alfp.llm import get_llm

    system = """You are a Red-Team Critic for an energy management agent.
Analyze the given decision evidence and output JSON only:
{
  "risk_score": 0.0 to 1.0,
  "failure_scenarios": ["scenario1", "scenario2"],
  "alternative_strategy": [{"action": "...", "reason": "..."}],
  "recommendation": "one paragraph recommendation"
}"""
    user = f"""Evidence:
- context: {evidence.context_summary}
- reasoning: {evidence.reasoning_summary[:800]}
- chosen_strategy: {evidence.chosen_strategy}
- confidence: {evidence.confidence_score}

Output JSON only."""
    llm = get_llm(temperature=0.2, stage="governance_critic")
    parser = JsonOutputParser()
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    data = parser.invoke(resp.content)
    return CriticAgentOutput(
        risk_score=float(data.get("risk_score", 0.5)),
        failure_scenarios=list(data.get("failure_scenarios") or []),
        alternative_strategy=list(data.get("alternative_strategy") or []),
        recommendation=str(data.get("recommendation", "")),
    )
