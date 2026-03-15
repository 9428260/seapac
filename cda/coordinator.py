"""
CDA Market Coordinator (PRD §6) — MarketCoordinator 대체

기능: Order Book 관리, 매칭 실행, 거래 체결, 시장 통계 생성.
SmartSeller/StorageMaster/EcoSaver 제안을 받아 CDA 매칭 후
seapac_agents와 동일한 decisions 형식으로 반환 (Execution/Mesa 호환).
"""

from __future__ import annotations

from typing import Any

from cda.orderbook import OrderBook
from cda.matching import match_cda
from cda.buyer import generate_bids_from_state
from cda.strategy_agent import generate_strategy
from cda.negotiation import run_negotiation


def _build_asks_from_seller_proposal(
    seller_proposal: dict,
    seller_agent: str = "SmartSeller",
) -> list[tuple[str, float, float]]:
    """SmartSeller proposal → (agent, price, quantity) Ask 리스트."""
    if not seller_proposal or seller_proposal.get("action") in ("hold", None):
        return []
    action = seller_proposal.get("action")
    if action not in ("sell_p2p", "sell_grid"):
        return []
    price = float(seller_proposal.get("bid_price", 0.0))
    qty = float(seller_proposal.get("bid_quantity_kw", 0.0))
    if qty <= 0 or price <= 0:
        return []
    return [(seller_agent, price, qty)]


def run_cda_step(
    state_json: dict,
    seller_msg: Any,
    storage_msg: Any,
    eco_msg: Any,
    policy_agent: Any,
    *,
    min_trade_kw: float = 0.2,
) -> dict:
    """
    단일 스텝 CDA 코디네이터: 제안 수집 → Order Book → 매칭 → decisions.

    Args:
        state_json: State Translator 출력
        seller_msg: SmartSeller AgentScope Msg (metadata.proposal)
        storage_msg: StorageMaster Msg
        eco_msg: EcoSaver Msg
        policy_agent: PolicyAgentAS 인스턴스 (validate_ess, validate_trade, validate_dr)
        min_trade_kw: 최소 거래량 (Policy와 동일)

    Returns:
        decisions dict (ess_schedule, trading_recommendations, demand_response_events 등)
        — seapac_agents.execution / simulation.model 호환.
    """
    cs = state_json.get("community_state") or {}
    time_str = state_json.get("time", "")

    # ── 제안 추출 ─────────────────────────────────────────
    seller_proposal = (getattr(seller_msg, "metadata", None) or {}).get("proposal", {}) if seller_msg else {}
    storage_proposal = (getattr(storage_msg, "metadata", None) or {}).get("proposal", {}) if storage_msg else {}
    dr_proposals = ((getattr(eco_msg, "metadata", None) or {}).get("proposal", {}) or {}).get("dr_events", []) if eco_msg else []

    # ── Policy 검증 (ESS, Trade, DR) ───────────────────────
    all_violations = []
    validated_storage, ess_errs = policy_agent.validate_ess(storage_proposal)
    all_violations.extend(ess_errs)
    validated_seller, trade_errs = policy_agent.validate_trade(seller_proposal)
    all_violations.extend(trade_errs)
    validated_dr = []
    for dr in dr_proposals:
        v, errs = policy_agent.validate_dr(dr)
        all_violations.extend(errs)
        if v:
            validated_dr.append(v)

    # ── 충돌 해결: 피크 HIGH 시 ESS 방전 우선, 잉여 일부만 판매 ─────
    ess_action = validated_storage.get("action", "idle")
    ess_power = float(validated_storage.get("power_kw", 0.0))
    peak_risk = cs.get("peak_risk", "LOW")

    if peak_risk == "HIGH" and validated_seller is not None:
        if ess_action == "discharge":
            validated_seller = None
            all_violations.append("HIGH 피크: ESS 방전 우선, P2P 판매 보류")
        elif ess_action == "charge":
            sell_qty = float(validated_seller.get("bid_quantity_kw", 0))
            if sell_qty > ess_power:
                validated_seller = {
                    **validated_seller,
                    "bid_quantity_kw": round(sell_qty - ess_power, 2),
                }

    # ── CDA Order Book 구성 및 매칭 ───────────────────────
    book = OrderBook(bids=[], asks=[])

    # Asks: SmartSeller (검증된 제안만)
    for agent, price, qty in _build_asks_from_seller_proposal(validated_seller or {}):
        if qty >= min_trade_kw:
            book.add_ask(agent, price, qty)

    # Bids: State 기반 Buyer
    for agent, price, qty in generate_bids_from_state(state_json):
        book.add_bid(agent, price, qty)

    trades = match_cda(book)

    # ── trading_recommendations: Mesa 호환 형식 ──────────
    trading_recommendations = []
    for t in trades:
        trading_recommendations.append({
            "timestamp": time_str,
            "surplus_kw": t.quantity_kw,
            "bid_price": t.trade_price,
            "action": "sell_p2p",
        })

    # ── ess_schedule (기존과 동일) ───────────────────────
    ess_schedule = [{
        "timestamp": time_str,
        "action": ess_action,
        "power_kw": ess_power,
        "soc_kwh": 0.0,
        "net_load_kw": float(cs.get("total_load", 0)) - float(cs.get("pv_generation", 0)),
        "reason": validated_storage.get("reason", ""),
    }]

    decisions = {
        "ess_schedule": ess_schedule,
        "ess_summary": {
            "action": ess_action,
            "power_kw": ess_power,
            "peak_risk": peak_risk,
            "storage_reason": validated_storage.get("reason", ""),
        },
        "trading_recommendations": trading_recommendations,
        "trading_summary": {
            "total_surplus_events": len(trading_recommendations),
            "total_surplus_kw": round(sum(r["surplus_kw"] for r in trading_recommendations), 2),
        },
        "demand_response_events": validated_dr,
        "dr_summary": {
            "dr_event_count": len(validated_dr),
            "total_reduction_kw": round(
                sum(float(d.get("recommended_reduction_kw", 0)) for d in validated_dr), 2
            ),
        },
        "policy_violations": all_violations,
        "cda_trades": [
            {
                "seller_agent": t.seller_agent,
                "buyer_agent": t.buyer_agent,
                "quantity_kw": t.quantity_kw,
                "trade_price": t.trade_price,
            }
            for t in trades
        ],
        "coordinator_notes": (
            f"CDA peak_risk={peak_risk}, ESS={ess_action} {ess_power}kW, "
            f"trades={len(trades)}, DR={len(validated_dr)}"
        ),
    }
    return decisions


def _msg_with_proposal(proposal: dict) -> Any:
    """협상 합의안을 run_cda_step에 넘기기 위한 Msg 호환 객체."""
    class _FakeMsg:
        def __init__(self, p: dict) -> None:
            self.metadata = {"proposal": p}
    return _FakeMsg(proposal)


def run_cda_step_with_strategy_and_negotiation(
    state_json: dict,
    seller_msg: Any,
    storage_msg: Any,
    eco_msg: Any,
    policy_agent: Any,
    *,
    use_llm_strategy: bool = True,
    state_summary: str | None = None,
    min_trade_kw: float = 0.2,
) -> dict:
    """
    단일 스텝: Strategy Agent → Negotiation → CDA 매칭 → decisions.

    PRD (cda_strategy_negotiation_prd.md):
      Forecast Layer → Strategy Agent (LLM) → Negotiation Layer → Policy/Trust → CDA Market.

    Returns:
        decisions dict (기존 형식) + strategy_reasoning_log, negotiation_log 추가.
    """
    strategy_rec = generate_strategy(
        state_json,
        state_summary=state_summary,
        use_llm=use_llm_strategy,
    )
    negotiation_result = run_negotiation(
        state_json,
        strategy_rec,
        seller_msg,
        storage_msg,
        eco_msg,
        policy_agent,
    )
    # 합의안을 run_cda_step에 넘김 (검증·충돌해결은 협상 단계에서 이미 수행)
    fake_seller = _msg_with_proposal(negotiation_result.consensus_seller_proposal or {})
    fake_storage = _msg_with_proposal(negotiation_result.consensus_storage_proposal or {})
    fake_eco = _msg_with_proposal({"dr_events": negotiation_result.consensus_dr_events})
    decisions = run_cda_step(
        state_json,
        fake_seller,
        fake_storage,
        fake_eco,
        policy_agent,
        min_trade_kw=min_trade_kw,
    )
    decisions["strategy_reasoning_log"] = strategy_rec.reasoning_log
    decisions["negotiation_log"] = [
        {"role": s.role, "content": s.content[:500], "proposal_keys": list((s.proposal or {}).keys())}
        for s in negotiation_result.negotiation_log
    ]
    decisions["conflicts_resolved"] = negotiation_result.conflicts_resolved
    return decisions


async def _run_cda_single_step_with_negotiation_async(
    state_json: dict,
    policy_agent: Any,
    seller_agent: Any,
    storage_agent: Any,
    eco_saver_agent: Any,
    *,
    state_message_template: str = "커뮤니티 에너지 상태 [{time}]: 부하={total_load}kW, 피크위험={peak_risk}",
    use_llm_strategy: bool = True,
) -> dict:
    """한 스텝: State → 에이전트 호출 → Strategy → Negotiation → CDA → decisions."""
    from agentscope.message import Msg

    _cs = state_json.get("community_state") or {}
    state_msg = Msg(
        name="StateTranslator",
        content=state_message_template.format(
            time=state_json.get("time", "?"),
            total_load=_cs.get("total_load", 0),
            peak_risk=_cs.get("peak_risk", "N/A"),
        ),
        role="user",
        metadata={"state": state_json},
    )
    policy_msg = await policy_agent(state_msg)
    seller_msg = await seller_agent(state_msg)
    storage_msg = await storage_agent(state_msg)
    eco_msg = await eco_saver_agent(state_msg)
    return run_cda_step_with_strategy_and_negotiation(
        state_json,
        seller_msg,
        storage_msg,
        eco_msg,
        policy_agent,
        use_llm_strategy=use_llm_strategy,
    )


def run_cda_decision_series_with_agents_and_negotiation(
    state_json_list: list[dict],
    policy_agent: Any,
    seller_agent: Any,
    storage_agent: Any,
    eco_saver_agent: Any,
    *,
    state_message_template: str = "커뮤니티 에너지 상태 [{time}]: 부하={total_load}kW, 피크위험={peak_risk}",
    use_llm_strategy: bool = True,
) -> dict:
    """
    다중 스텝: Strategy Agent + Negotiation Layer 포함 CDA 의사결정.

    PRD §4.3: Strategy 제안 → 에이전트 제안 공유 → 협상 → 합의 → CDA 제출.

    Returns:
        decisions (ess_schedule, trading_recommendations, demand_response_events)
        + strategy_reasoning_logs, negotiation_logs (스텝별).
    """
    import asyncio

    async def _run_all() -> dict:
        ess_schedule = []
        trading_recommendations = []
        demand_response_events = []
        strategy_logs = []
        negotiation_logs = []
        for state in state_json_list:
            d = await _run_cda_single_step_with_negotiation_async(
                state,
                policy_agent,
                seller_agent,
                storage_agent,
                eco_saver_agent,
                state_message_template=state_message_template,
                use_llm_strategy=use_llm_strategy,
            )
            ess_schedule.extend(d.get("ess_schedule", []))
            trading_recommendations.extend(d.get("trading_recommendations", []))
            demand_response_events.extend(d.get("demand_response_events", []))
            if d.get("strategy_reasoning_log"):
                strategy_logs.append({"time": state.get("time"), "log": d["strategy_reasoning_log"]})
            if d.get("negotiation_log"):
                negotiation_logs.append({"time": state.get("time"), "steps": d["negotiation_log"]})
        return {
            "ess_schedule": ess_schedule,
            "trading_recommendations": trading_recommendations,
            "demand_response_events": demand_response_events,
            "ess_summary": {"total_steps": len(ess_schedule)},
            "trading_summary": {
                "total_surplus_events": len(trading_recommendations),
                "total_surplus_kw": round(
                    sum(float(r.get("surplus_kw", 0)) for r in trading_recommendations), 2
                ),
            },
            "dr_summary": {"dr_event_count": len(demand_response_events)},
            "strategy_reasoning_logs": strategy_logs,
            "negotiation_logs": negotiation_logs,
        }

    return asyncio.run(_run_all())


def run_cda_decision_series(
    state_json_list: list[dict],
    run_single_step_fn: callable,
) -> dict:
    """
    다중 스텝 CDA 의사결정 일괄 실행.

    run_single_step_fn(state_json) → decisions (한 스텝).
    각 스텝 decisions를 합쳐 Step 4 run_execution이 기대하는 형식으로 반환.

    Args:
        state_json_list: State Translator 출력 리스트
        run_single_step_fn: (state_json) -> decisions

    Returns:
        decisions: ess_schedule, trading_recommendations, demand_response_events 리스트
    """
    ess_schedule = []
    trading_recommendations = []
    demand_response_events = []

    for state in state_json_list:
        d = run_single_step_fn(state)
        ess_schedule.extend(d.get("ess_schedule", []))
        trading_recommendations.extend(d.get("trading_recommendations", []))
        demand_response_events.extend(d.get("demand_response_events", []))

    return {
        "ess_schedule": ess_schedule,
        "trading_recommendations": trading_recommendations,
        "demand_response_events": demand_response_events,
        "ess_summary": {"total_steps": len(ess_schedule)},
        "trading_summary": {
            "total_surplus_events": len(trading_recommendations),
            "total_surplus_kw": round(
                sum(float(r.get("surplus_kw", 0)) for r in trading_recommendations), 2
            ),
        },
        "dr_summary": {"dr_event_count": len(demand_response_events)},
    }


async def _run_cda_single_step_async(
    state_json: dict,
    policy_agent: Any,
    seller_agent: Any,
    storage_agent: Any,
    eco_saver_agent: Any,
    state_message_template: str = "커뮤니티 에너지 상태 [{time}]: 부하={total_load}kW, 피크위험={peak_risk}",
) -> dict:
    """한 스텝: State → 에이전트 호출 → CDA 코디네이터 → decisions."""
    from agentscope.message import Msg

    _cs = state_json.get("community_state") or {}
    state_msg = Msg(
        name="StateTranslator",
        content=state_message_template.format(
            time=state_json.get("time", "?"),
            total_load=_cs.get("total_load", 0),
            peak_risk=_cs.get("peak_risk", "N/A"),
        ),
        role="user",
        metadata={"state": state_json},
    )
    policy_msg = await policy_agent(state_msg)
    seller_msg = await seller_agent(state_msg)
    storage_msg = await storage_agent(state_msg)
    eco_msg = await eco_saver_agent(state_msg)
    return run_cda_step(
        state_json,
        seller_msg,
        storage_msg,
        eco_msg,
        policy_agent,
    )


def run_cda_decision_series_with_agents(
    state_json_list: list[dict],
    policy_agent: Any,
    seller_agent: Any,
    storage_agent: Any,
    eco_saver_agent: Any,
    *,
    state_message_template: str = "커뮤니티 에너지 상태 [{time}]: 부하={total_load}kW, 피크위험={peak_risk}",
) -> dict:
    """
    AgentScope 에이전트(SmartSeller, StorageMaster, EcoSaver, Policy)와 함께
    다중 스텝 CDA 의사결정 실행. MarketCoordinator 대신 CDA 매칭 사용.

    Returns:
        decisions (ess_schedule, trading_recommendations, demand_response_events)
    """
    import asyncio

    async def _run_all() -> dict:
        ess_schedule = []
        trading_recommendations = []
        demand_response_events = []
        for state in state_json_list:
            d = await _run_cda_single_step_async(
                state,
                policy_agent,
                seller_agent,
                storage_agent,
                eco_saver_agent,
                state_message_template=state_message_template,
            )
            ess_schedule.extend(d.get("ess_schedule", []))
            trading_recommendations.extend(d.get("trading_recommendations", []))
            demand_response_events.extend(d.get("demand_response_events", []))
        return {
            "ess_schedule": ess_schedule,
            "trading_recommendations": trading_recommendations,
            "demand_response_events": demand_response_events,
            "ess_summary": {"total_steps": len(ess_schedule)},
            "trading_summary": {
                "total_surplus_events": len(trading_recommendations),
                "total_surplus_kw": round(
                    sum(float(r.get("surplus_kw", 0)) for r in trading_recommendations), 2
                ),
            },
            "dr_summary": {"dr_event_count": len(demand_response_events)},
        }

    return asyncio.run(_run_all())
