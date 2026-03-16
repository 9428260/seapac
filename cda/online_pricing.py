"""
체결 결과 기반 온라인 가격 업데이트.

에이전트별 시장 피드백을 저장하고 다음 라운드 bid/ask price를 조정한다.
"""

from __future__ import annotations

import json
from pathlib import Path


_STORE_PATH = Path(__file__).resolve().parent.parent / "memory_store" / "market_feedback.json"


def _load_store() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        with open(_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_store(store: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def adjust_price(agent_id: str, base_price: float, side: str) -> float:
    """
    과거 미체결/체결 성과를 바탕으로 가격을 조정한다.

    side:
    - buy: 반복 미체결이면 상향, 자주 체결되면 과도한 가격을 완화
    - sell: 반복 미체결이면 하향, 자주 체결되면 소폭 상향
    """
    store = _load_store()
    stats = store.get(agent_id, {})
    matched = float(stats.get("matched_trades", 0))
    unmatched_buy = float(stats.get("unmatched_bids", 0))
    unmatched_sell = float(stats.get("unmatched_asks", 0))

    price = float(base_price)
    if side == "buy":
        if unmatched_buy > matched:
            price *= 1.03
        elif matched > unmatched_buy + 3:
            price *= 0.99
    elif side == "sell":
        if unmatched_sell > matched:
            price *= 0.97
        elif matched > unmatched_sell + 3:
            price *= 1.01
    return round(price, 2)


def record_market_feedback(
    trades: list[dict],
    bids: list[dict],
    asks: list[dict],
) -> None:
    """체결/미체결 결과를 agent별로 누적 저장한다."""
    store = _load_store()

    def _entry(agent_id: str) -> dict:
        return store.setdefault(agent_id, {
            "matched_trades": 0,
            "unmatched_bids": 0,
            "unmatched_asks": 0,
            "avg_trade_price": 0.0,
            "last_bid_price": 0.0,
            "last_ask_price": 0.0,
        })

    for bid in bids:
        agent_id = str(bid.get("agent"))
        ent = _entry(agent_id)
        ent["last_bid_price"] = float(bid.get("price", 0))
        ent["unmatched_bids"] += 1

    for ask in asks:
        agent_id = str(ask.get("agent"))
        ent = _entry(agent_id)
        ent["last_ask_price"] = float(ask.get("price", 0))
        ent["unmatched_asks"] += 1

    for tr in trades:
        seller = str(tr.get("seller_agent"))
        buyer = str(tr.get("buyer_agent"))
        trade_price = float(tr.get("trade_price", 0))
        for agent_id, side in ((seller, "sell"), (buyer, "buy")):
            ent = _entry(agent_id)
            prev_n = float(ent.get("matched_trades", 0))
            prev_avg = float(ent.get("avg_trade_price", 0))
            ent["matched_trades"] = prev_n + 1
            ent["avg_trade_price"] = round(((prev_avg * prev_n) + trade_price) / (prev_n + 1), 2)
            if side == "sell" and ent["unmatched_asks"] > 0:
                ent["unmatched_asks"] -= 1
            if side == "buy" and ent["unmatched_bids"] > 0:
                ent["unmatched_bids"] -= 1

    _save_store(store)
