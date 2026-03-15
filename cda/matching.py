"""
CDA Matching Engine (PRD §8)

  Step 1: Bid 정렬 (가격 기준 내림차순)
  Step 2: Ask 정렬 (가격 기준 오름차순)
  Step 3: 매칭 조건 — Highest Bid ≥ Lowest Ask
  거래 가격: Trade Price = (Bid Price + Ask Price) / 2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from cda.orderbook import OrderBook, Bid, Ask


@dataclass
class Trade:
    """체결된 거래 1건."""
    seller_agent: str
    buyer_agent: str
    quantity_kw: float
    trade_price: float  # (bid_price + ask_price) / 2
    bid_price: float
    ask_price: float


def match_cda(book: OrderBook) -> List[Trade]:
    """
    CDA 매칭 실행.

    Returns:
        체결된 거래 리스트 (시간순).
    """
    # Step 1: Bid 가격 내림차순
    bids = sorted(book.bids, key=lambda b: (-b.price, -b.quantity))
    # Step 2: Ask 가격 오름차순
    asks = sorted(book.asks, key=lambda a: (a.price, -a.quantity))

    trades: List[Trade] = []
    # 남은 수량 (복사하여 소진 반영): (원본 인덱스, 가격, 수량) / (인덱스, agent, 가격, 수량)
    bid_remaining = [(i, b.price, b.quantity) for i, b in enumerate(bids)]
    ask_remaining = [(i, a.agent, a.price, a.quantity) for i, a in enumerate(asks)]

    # Step 3: Highest Bid ≥ Lowest Ask 인 동안 매칭
    while bid_remaining and ask_remaining:
        (bid_idx, bid_p, bid_q) = bid_remaining[0]
        (ask_idx, ask_agent, ask_p, ask_q) = ask_remaining[0]
        if bid_p < ask_p:
            break
        buyer_agent = bids[bid_idx].agent
        qty = min(bid_q, ask_q)
        if qty <= 0:
            break
        trade_price = round((bid_p + ask_p) / 2, 2)
        trades.append(Trade(
            seller_agent=ask_agent,
            buyer_agent=buyer_agent,
            quantity_kw=round(qty, 2),
            trade_price=trade_price,
            bid_price=bid_p,
            ask_price=ask_p,
        ))
        # 수량 감소
        new_bid_q = round(bid_q - qty, 2)
        new_ask_q = round(ask_q - qty, 2)
        if new_bid_q <= 0:
            bid_remaining.pop(0)
        else:
            bid_remaining[0] = (bid_remaining[0][0], bid_p, new_bid_q)
        if new_ask_q <= 0:
            ask_remaining.pop(0)
        else:
            ask_remaining[0] = (ask_idx, ask_agent, ask_p, new_ask_q)

    return trades
