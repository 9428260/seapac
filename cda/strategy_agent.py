"""
Strategy Agent (LLM) — CDA Strategy Negotiation PRD §3

에너지 시장 전략 수립: 예측·가격·ESS·피크위험을 분석하여
Bid/Ask·ESS 권고를 생성하고, 설명 가능한 reasoning log를 출력합니다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StrategyRecommendation:
    """Strategy Agent 출력 — 협상 레이어 초기 제안용."""
    # 판매 측 권고 (SmartSeller 호환)
    seller_action: str = "hold"  # sell_p2p | sell_grid | hold
    seller_bid_price: float = 0.0
    seller_bid_quantity_kw: float = 0.0
    seller_reason: str = ""
    # ESS 권고 (StorageMaster 호환)
    storage_action: str = "idle"  # charge | discharge | idle
    storage_power_kw: float = 0.0
    storage_reason: str = ""
    # 구매 측 권고 (CommunityBuyer 방향성)
    bid_price_suggestion: float = 0.0
    bid_quantity_suggestion: float = 0.0
    # DR 권고 (EcoSaver 호환) — 있으면 리스트
    dr_suggested: bool = False
    dr_reduction_kw: float = 0.0
    dr_reason: str = ""
    # 설명 가능 전략 (FR-2)
    reasoning_log: str = ""

    def to_seller_proposal(self, time_str: str = "") -> dict:
        """SmartSeller proposal 형식으로 변환."""
        if self.seller_action in ("hold", None):
            return {"action": "hold", "bid_price": 0.0, "bid_quantity_kw": 0.0, "reason": self.seller_reason}
        return {
            "action": self.seller_action,
            "bid_price": self.seller_bid_price,
            "bid_quantity_kw": self.seller_bid_quantity_kw,
            "surplus_kw": self.seller_bid_quantity_kw,
            "reason": self.seller_reason,
            "timestamp": time_str,
        }

    def to_storage_proposal(self) -> dict:
        """StorageMaster proposal 형식으로 변환."""
        return {
            "action": self.storage_action,
            "power_kw": self.storage_power_kw,
            "reason": self.storage_reason,
        }


def _strategy_rule_based(state_json: dict) -> StrategyRecommendation:
    """규칙 기반 전략 (LLM 미사용 또는 장애 시)."""
    cs = state_json.get("community_state") or {}
    ms = state_json.get("market_state") or {}
    es = state_json.get("ess_state") or {}
    time_str = state_json.get("time", "")

    surplus = float(cs.get("surplus_energy", 0.0))
    deficit = float(cs.get("deficit_energy", 0.0))
    peak_risk = cs.get("peak_risk", "LOW")
    total_load = float(cs.get("total_load", 0.0))
    grid_price = float(ms.get("grid_price") or 100.0)
    price_range = list(ms.get("community_trade_price_range") or [80.0, 110.0])
    p2p_min, p2p_max = float(price_range[0]), float(price_range[1])

    rec = StrategyRecommendation(reasoning_log="[Rule-based] 전략 Agent 폴백")

    # Seller
    if surplus <= 0:
        rec.seller_action = "hold"
        rec.seller_reason = "잉여 에너지 없음 — 판매 보류"
    else:
        if peak_risk == "HIGH":
            rec.seller_bid_price = round(p2p_max * 0.9, 1)
        elif peak_risk == "MEDIUM":
            rec.seller_bid_price = round((p2p_min + p2p_max) / 2, 1)
        else:
            rec.seller_bid_price = p2p_min
        rec.seller_bid_quantity_kw = round(surplus, 2)
        rec.seller_action = "sell_p2p" if rec.seller_bid_price < grid_price else "sell_grid"
        rec.seller_reason = f"잉여 {surplus:.1f}kW, 피크위험={peak_risk} → {rec.seller_action}"

    # Storage
    soc = es.get("soc")
    capacity = es.get("capacity")
    avail = float(es.get("available_discharge") or 0.0)
    if soc is None or capacity is None:
        rec.storage_action = "idle"
        rec.storage_reason = "ESS 미설치"
    else:
        soc_pct = float(soc)
        cap = float(capacity)
        max_kw = min(cap / 4, 50.0)
        if peak_risk == "HIGH" and soc_pct > 15:
            rec.storage_action = "discharge"
            rec.storage_power_kw = round(min(max_kw, avail / 0.25), 2)
            rec.storage_reason = f"피크 위험 HIGH → 방전 {rec.storage_power_kw}kW"
        elif surplus > 1.0 and soc_pct < 90:
            rec.storage_action = "charge"
            rec.storage_power_kw = round(min(max_kw, surplus), 2)
            rec.storage_reason = f"잉여 PV {surplus:.1f}kW 흡수 충전"
        elif grid_price <= 85 and soc_pct < 90:
            rec.storage_action = "charge"
            rec.storage_power_kw = round(min(max_kw, (0.95 - soc_pct / 100) * cap / 0.25), 2)
            rec.storage_reason = f"TOU 저가 {grid_price} 원/kWh → 충전"
        elif grid_price >= 115 and soc_pct > 15:
            rec.storage_action = "discharge"
            rec.storage_power_kw = round(min(max_kw, avail / 0.25), 2)
            rec.storage_reason = f"TOU 고가 {grid_price} 원/kWh → 방전"
        else:
            rec.storage_action = "idle"
            rec.storage_reason = "대기 (조건 미충족)"

    # Buyer suggestion
    if deficit > 0:
        if peak_risk == "HIGH":
            rec.bid_price_suggestion = min(grid_price * 1.1, 150.0)
        elif peak_risk == "MEDIUM":
            rec.bid_price_suggestion = min(grid_price * 0.95, 150.0)
        else:
            rec.bid_price_suggestion = max(grid_price * 0.9, 50.0)
        rec.bid_quantity_suggestion = round(deficit, 2)

    # DR
    if total_load > 500:
        rec.dr_suggested = True
        rec.dr_reduction_kw = round((total_load - 500) * 0.30, 2)
        rec.dr_reason = f"피크 초과 → 30% 절감 권고"
    elif peak_risk == "MEDIUM":
        rec.dr_suggested = True
        rec.dr_reduction_kw = round(total_load * 0.05, 2)
        rec.dr_reason = "피크 위험 MEDIUM → 예방적 5% 절감"

    rec.reasoning_log = (
        f"[Rule] 부하={total_load}kW, 잉여={surplus}kW, 부족={deficit}kW, 피크={peak_risk}, "
        f"그리드가={grid_price}. 판매={rec.seller_action}, ESS={rec.storage_action}."
    )
    return rec


def _strategy_llm(state_json: dict, state_summary: str) -> StrategyRecommendation:
    """LLM 기반 전략 생성 (FR-1, FR-2). Latency < 2s 목표."""
    from langchain_core.messages import HumanMessage, SystemMessage

    try:
        from alfp.llm import get_llm
    except ImportError:
        return _strategy_rule_based(state_json)

    cs = state_json.get("community_state") or {}
    ms = state_json.get("market_state") or {}
    es = state_json.get("ess_state") or {}
    time_str = state_json.get("time", "")

    system = """You are a Strategy Agent for a CDA (Continuous Double Auction) energy market.
Analyze the community state and output a short strategic recommendation and concrete numbers.

Output JSON only, no markdown. Use this exact structure:
{
  "seller_action": "sell_p2p" | "sell_grid" | "hold",
  "seller_bid_price": number (원/kWh, 0 if hold),
  "seller_bid_quantity_kw": number,
  "seller_reason": "one short sentence",
  "storage_action": "charge" | "discharge" | "idle",
  "storage_power_kw": number,
  "storage_reason": "one short sentence",
  "bid_price_suggestion": number (for buyer side, 0 if no deficit),
  "bid_quantity_suggestion": number,
  "dr_suggested": true | false,
  "dr_reduction_kw": number,
  "dr_reason": "one short sentence or empty",
  "reasoning_log": "2-3 sentences explaining your strategy (e.g. price trend, peak risk, storage)"
}

Rules: seller_bid_quantity_kw should not exceed surplus_energy. If surplus_energy is 0, use hold.
Storage power should be reasonable (e.g. < 50 kW typical). reasoning_log must be in Korean or English."""

    user = f"""Current state (time={time_str}):
{state_summary}

Raw state (community_state, market_state, ess_state):
{json.dumps({"community_state": cs, "market_state": ms, "ess_state": es}, ensure_ascii=False, indent=0)}

Output JSON only (no markdown)."""

    try:
        llm = get_llm(temperature=0.2)
        resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        text = resp.content if hasattr(resp, "content") else str(resp)
        # strip markdown code block if present
        if "```" in text:
            for part in text.split("```"):
                if part.strip().startswith("json"):
                    text = part.strip()[4:].strip()
                    break
                if part.strip().startswith("{"):
                    text = part.strip()
                    break
        data = json.loads(text)
    except Exception:
        return _strategy_rule_based(state_json)

    return StrategyRecommendation(
        seller_action=str(data.get("seller_action", "hold")),
        seller_bid_price=float(data.get("seller_bid_price", 0.0)),
        seller_bid_quantity_kw=float(data.get("seller_bid_quantity_kw", 0.0)),
        seller_reason=str(data.get("seller_reason", "")),
        storage_action=str(data.get("storage_action", "idle")),
        storage_power_kw=float(data.get("storage_power_kw", 0.0)),
        storage_reason=str(data.get("storage_reason", "")),
        bid_price_suggestion=float(data.get("bid_price_suggestion", 0.0)),
        bid_quantity_suggestion=float(data.get("bid_quantity_suggestion", 0.0)),
        dr_suggested=bool(data.get("dr_suggested", False)),
        dr_reduction_kw=float(data.get("dr_reduction_kw", 0.0)),
        dr_reason=str(data.get("dr_reason", "")),
        reasoning_log=str(data.get("reasoning_log", "")),
    )


def generate_strategy(
    state_json: dict,
    state_summary: str | None = None,
    use_llm: bool = True,
) -> StrategyRecommendation:
    """
    전략 Agent 실행: state 기반 Bid/Ask·ESS·DR 권고 및 reasoning log 생성.

    Args:
        state_json: State Translator 출력 (community_state, market_state, ess_state)
        state_summary: 선택적 자연어 요약 (LLM용). None이면 generate_summary 호출 시도
        use_llm: True면 LLM 사용, False 또는 LLM 실패 시 규칙 기반

    Returns:
        StrategyRecommendation (협상 레이어 초기 제안용)
    """
    if state_summary is None:
        try:
            from seapac_agents.state_translator import generate_summary
            state_summary = generate_summary(state_json)
        except Exception:
            state_summary = json.dumps(state_json.get("community_state") or {}, ensure_ascii=False)

    if use_llm:
        try:
            return _strategy_llm(state_json, state_summary)
        except Exception:
            pass
    return _strategy_rule_based(state_json)
