"""
CDA Order Book — Bid / Ask 테이블 (PRD §7)

  Bid Table: | Agent | Price | Quantity |
  Ask Table: | Agent | Price | Quantity |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class Bid:
    """구매 입찰 (Buyer)."""
    agent: str
    price: float   # 원/kWh
    quantity: float  # kW


@dataclass
class Ask:
    """판매 호가 (Seller)."""
    agent: str
    price: float   # 원/kWh
    quantity: float  # kW


@dataclass
class OrderBook:
    """CDA 오더북: Bid 리스트 + Ask 리스트."""
    bids: List[Bid]
    asks: List[Ask]

    def add_bid(self, agent: str, price: float, quantity: float) -> None:
        if quantity > 0 and price > 0:
            self.bids.append(Bid(agent=agent, price=price, quantity=quantity))

    def add_ask(self, agent: str, price: float, quantity: float) -> None:
        if quantity > 0 and price > 0:
            self.asks.append(Ask(agent=agent, price=price, quantity=quantity))

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()
