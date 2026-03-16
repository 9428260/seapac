"""
Self-Critic Agent — LLM Agent가 자기 전략을 반박하도록 설계 (AgentScope 레이어).

Multi-Agent Decision(Step 3)에서 산출된 decisions를 동일 LLM 관점에서 비판·반박하여
리스크와 약점을 노출하고, 필요 시 후속 정책 게이트·재계획에 활용합니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SelfCriticOutput:
    """Self-Critic Agent 출력."""
    refutations: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 = low, 1.0 = high
    recommendation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "refutations": self.refutations,
            "risk_score": self.risk_score,
            "recommendation": self.recommendation,
        }


def _self_critic_rule_based(decisions: dict) -> SelfCriticOutput:
    """규칙 기반 Self-Critic (LLM 미사용 시)."""
    out = SelfCriticOutput()
    ess = decisions.get("ess_schedule") or []
    n_charge = sum(1 for e in ess if (e.get("action") or "").lower() == "charge")
    n_discharge = sum(1 for e in ess if (e.get("action") or "").lower() == "discharge")
    if n_charge > 40 or n_discharge > 40:
        out.risk_score = 0.4
        out.refutations.append("ESS 충/방전 스텝 과다로 배터리 수명·사이클 리스크")
    trades = decisions.get("trading_recommendations") or []
    if len(trades) > 20:
        out.risk_score = min(1.0, out.risk_score + 0.2)
        out.refutations.append("거래 이벤트 다수로 시장 유동성·가격 변동 리스크")
    if not out.refutations:
        out.recommendation = "현재 전략 유지. Self-Critic 관점에서 특별한 반박 사유 없음."
    else:
        out.recommendation = "반박 사유를 Policy Gate 또는 재계획 단계에서 검토 권장."
    return out


def _self_critic_llm(decisions: dict, state_context: str | None) -> SelfCriticOutput:
    """LLM 기반 Self-Critic: 자기 전략을 반박하도록 프롬프트."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.output_parsers import JsonOutputParser
    from alfp.llm import get_llm

    system = """You are a Self-Critic for an energy community multi-agent system.
The system just produced an operational strategy (decisions). Your role is to **refute** it:
list weaknesses, risks, and how this strategy might fail. Be critical and concise.

Output JSON only, no markdown:
{
  "refutations": ["reason1", "reason2", ...],
  "risk_score": 0.0 to 1.0,
  "recommendation": "one short paragraph"
}"""
    summary = state_context or ""
    ess_len = len(decisions.get("ess_schedule") or [])
    trade_len = len(decisions.get("trading_recommendations") or [])
    dr_len = len(decisions.get("demand_response_events") or [])
    user = f"""Strategy to refute:
- ESS schedule steps: {ess_len}
- Trading recommendations: {trade_len}
- DR events: {dr_len}
{summary}

First 3 ESS entries: {json.dumps((decisions.get("ess_schedule") or [])[:3], ensure_ascii=False)}
First 2 trading: {json.dumps((decisions.get("trading_recommendations") or [])[:2], ensure_ascii=False)}

Output JSON only (refutations, risk_score, recommendation)."""
    llm = get_llm(temperature=0.2, stage="seapac_self_critic")
    parser = JsonOutputParser()
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    text = resp.content if hasattr(resp, "content") else str(resp)
    try:
        data = parser.parse(text)
    except Exception:
        data = {}
        try:
            data = json.loads(text)
        except Exception:
            pass
    return SelfCriticOutput(
        refutations=list(data.get("refutations") or []),
        risk_score=float(data.get("risk_score", 0.5)),
        recommendation=str(data.get("recommendation", "")),
    )


def run_self_critic(
    decisions: dict,
    state_context: str | None = None,
    use_llm: bool = True,
) -> SelfCriticOutput:
    """
    Self-Critic Agent 실행: 동일 전략(decisions)에 대해 LLM이 자기 전략을 반박하도록 설계.

    Args:
        decisions: Step 3 Multi-Agent Decision 출력 (ess_schedule, trading_recommendations, demand_response_events)
        state_context: 선택적 상태 요약 (LLM 컨텍스트)
        use_llm: True면 LLM 기반 반박, False면 규칙 기반

    Returns:
        SelfCriticOutput (refutations, risk_score, recommendation)
    """
    if use_llm:
        try:
            return _self_critic_llm(decisions, state_context)
        except Exception:
            pass
    return _self_critic_rule_based(decisions)
