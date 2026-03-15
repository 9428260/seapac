"""
Buyer Agent (PRD §6) — CDA 구매 입찰 생성

목표: 필요한 전력을 최소 비용으로 구매
입력: Energy Deficit, Market Price, Peak Risk
출력: Bid Price, Bid Quantity
"""

from __future__ import annotations


def generate_bids_from_state(
    state_json: dict,
    grid_price_default: float = 100.0,
    min_bid_price: float = 50.0,
    max_bid_price: float = 150.0,
) -> list[tuple[str, float, float]]:
    """
    State JSON에서 커뮤니티 부족분(deficit)을 바탕으로 구매 입찰(Bid) 생성.

    Returns:
        [(agent_id, price, quantity_kw), ...]
    """
    cs = state_json.get("community_state") or {}
    ms = state_json.get("market_state") or {}
    deficit = float(cs.get("deficit_energy", 0.0))
    peak_risk = cs.get("peak_risk", "LOW")
    grid_price = float(ms.get("grid_price") or grid_price_default)

    if deficit <= 0:
        return []

    # 피크 위험에 따라 구매 의향가 조정: HIGH면 그리드가보다 높게 입찰 가능
    if peak_risk == "HIGH":
        bid_price = min(grid_price * 1.1, max_bid_price)
    elif peak_risk == "MEDIUM":
        bid_price = min(grid_price * 0.95, max_bid_price)
    else:
        bid_price = max(grid_price * 0.9, min_bid_price)

    bid_price = round(bid_price, 1)
    return [("CommunityBuyer", bid_price, round(deficit, 2))]
