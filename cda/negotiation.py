"""
Negotiation Layer — CDA Strategy Negotiation PRD §4

Strategy Agent 초안 제안 후, SmartSeller/StorageMaster/EcoSaver/Policy와
협상하여 합의안을 도출하고, 최종 Bid를 CDA 시장에 제출하기 전 단계입니다.
FR-5: multi-agent strategy discussion, FR-6: resolve conflicts, FR-7: consensus, FR-8: audit log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NegotiationStep:
    """협상 한 턴 (감사 로그용)."""
    role: str
    content: str
    proposal: dict | None = None


@dataclass
class NegotiationResult:
    """협상 결과 — 코디네이터가 사용할 합의 제안."""
    consensus_seller_proposal: dict | None = None
    consensus_storage_proposal: dict | None = None
    consensus_dr_events: list[dict] = field(default_factory=list)
    negotiation_log: list[NegotiationStep] = field(default_factory=list)
    conflicts_resolved: list[str] = field(default_factory=list)


def _extract_proposal(msg: Any) -> dict:
    """AgentScope Msg에서 metadata.proposal 추출."""
    if msg is None:
        return {}
    meta = getattr(msg, "metadata", None) or {}
    return meta.get("proposal") or {}


def _extract_dr_events(eco_proposal: dict) -> list[dict]:
    """EcoSaver proposal에서 dr_events 추출."""
    return list(eco_proposal.get("dr_events") or [])


def run_negotiation(
    state_json: dict,
    strategy_recommendation: Any,
    seller_msg: Any,
    storage_msg: Any,
    eco_msg: Any,
    policy_agent: Any,
    *,
    max_rounds: int = 2,
    use_llm_discussion: bool = False,
) -> NegotiationResult:
    """
    협상 레이어 실행: 전략 제안 + 에이전트 제안 → 충돌 해결 → 합의안.

    Flow (PRD §4.3):
      Step 1: Strategy Agent 제안 (strategy_recommendation)
      Step 2: 에이전트 제안 공유 (seller_msg, storage_msg, eco_msg)
      Step 3: 협상 (규칙 기반 충돌 해결 또는 1라운드 LLM)
      Step 4: 합의안 생성
      Step 5: 최종 Bid는 호출자가 CDA에 제출

    Args:
        state_json: State Translator 출력
        strategy_recommendation: StrategyRecommendation (strategy_agent.generate_strategy 출력)
        seller_msg, storage_msg, eco_msg: AgentScope 에이전트 응답 Msg
        policy_agent: PolicyAgentAS (validate_ess, validate_trade, validate_dr)
        max_rounds: 협상 라운드 수 (현재 규칙 기반에서는 1로 충분)
        use_llm_discussion: True면 협상 요약용 LLM 1회 호출 (선택)

    Returns:
        NegotiationResult (consensus 제안 + negotiation_log)
    """
    from cda.strategy_agent import StrategyRecommendation

    log: list[NegotiationStep] = []
    conflicts_resolved: list[str] = []

    # Step 1: Strategy 제안
    if isinstance(strategy_recommendation, StrategyRecommendation):
        strat = strategy_recommendation
        log.append(NegotiationStep(
            role="StrategyAgent",
            content=strat.reasoning_log or "Initial strategy proposal",
            proposal={
                "seller": strat.to_seller_proposal(state_json.get("time", "")),
                "storage": strat.to_storage_proposal(),
                "dr_suggested": strat.dr_suggested,
                "dr_reduction_kw": strat.dr_reduction_kw,
            },
        ))
    else:
        strat = None

    # Step 2: 에이전트 제안 수집
    seller_proposal = _extract_proposal(seller_msg)
    storage_proposal = _extract_proposal(storage_msg)
    eco_proposal = _extract_proposal(eco_msg)
    dr_proposals = _extract_dr_events(eco_proposal)

    log.append(NegotiationStep(role="SmartSeller", content=str(seller_proposal.get("reason", ""))[:200], proposal=seller_proposal))
    log.append(NegotiationStep(role="StorageMaster", content=str(storage_proposal.get("reason", ""))[:200], proposal=storage_proposal))
    log.append(NegotiationStep(role="EcoSaver", content=f"DR events: {len(dr_proposals)}", proposal=eco_proposal))

    cs = state_json.get("community_state") or {}
    peak_risk = cs.get("peak_risk", "LOW")

    # Step 3 & 4: 충돌 해결 + 합의 (Utility optimization / Rule-based)
    # PRD: 피크 HIGH 시 ESS 방전 우선, 판매 지연 권고 등
    consensus_seller = seller_proposal if seller_proposal else None
    consensus_storage = storage_proposal if storage_proposal else {}
    consensus_dr = list(dr_proposals)

    # 전략 제안이 있으면 에이전트 제안과 병합 (전략은 "권고", 에이전트 제안은 구체치 — 우선 에이전트 유지, 충돌 시 규칙 적용)
    if strat:
        # Storage: 전략과 StorageMaster 중 피크 시 방전 우선
        strat_storage = strat.to_storage_proposal()
        if peak_risk == "HIGH":
            if strat_storage.get("action") == "discharge" and consensus_storage.get("action") != "discharge":
                consensus_storage = {**strat_storage, "reason": strat_storage.get("reason", "") + " (전략 반영)"}
                conflicts_resolved.append("HIGH 피크: 전략 권고에 따라 ESS 방전 우선 반영")
            elif consensus_storage.get("action") == "discharge":
                pass  # 이미 방전
            elif strat_storage.get("action") == "discharge":
                consensus_storage = strat_storage
                conflicts_resolved.append("HIGH 피크: StorageMaster 대신 전략 Agent 방전 채택")
        # Seller: 전략이 "지연 판매"를 권고할 수 있음 — 현재는 에이전트 제안 유지, 피크 HIGH 시 아래에서 제거됨

    # Policy 검증 적용
    validated_storage, ess_errs = policy_agent.validate_ess(consensus_storage)
    validated_seller, trade_errs = policy_agent.validate_trade(consensus_seller or {})
    for e in ess_errs:
        conflicts_resolved.append(f"Policy: {e}")
    for e in trade_errs:
        conflicts_resolved.append(f"Policy: {e}")
    validated_dr = []
    for dr in consensus_dr:
        v, errs = policy_agent.validate_dr(dr)
        conflicts_resolved.extend(errs)
        if v:
            validated_dr.append(v)

    # 피크 HIGH 시 ESS 방전 우선 → P2P 판매 보류/감소 (기존 coordinator 로직과 동일)
    ess_action = validated_storage.get("action", "idle")
    ess_power = float(validated_storage.get("power_kw", 0.0))
    if peak_risk == "HIGH" and validated_seller:
        if ess_action == "discharge":
            validated_seller = None
            conflicts_resolved.append("HIGH 피크: ESS 방전 우선, P2P 판매 보류")
        elif ess_action == "charge":
            sell_qty = float(validated_seller.get("bid_quantity_kw", 0))
            if sell_qty > ess_power:
                validated_seller = {**validated_seller, "bid_quantity_kw": round(sell_qty - ess_power, 2)}
                conflicts_resolved.append("HIGH 피크: ESS 충전 반영하여 판매 수량 조정")

    log.append(NegotiationStep(
        role="Policy",
        content="; ".join(conflicts_resolved[-5:]) if conflicts_resolved else "No violations",
        proposal=None,
    ))

    return NegotiationResult(
        consensus_seller_proposal=validated_seller,
        consensus_storage_proposal=validated_storage,
        consensus_dr_events=validated_dr,
        negotiation_log=log,
        conflicts_resolved=conflicts_resolved,
    )
