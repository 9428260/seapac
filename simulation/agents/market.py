"""
EnergyMarketAgent - Phase 4: 에너지 거래 연동

단지 내 P2P(Peer-to-Peer) 에너지 거래를 중개하는 마켓 에이전트.
잉여 전력이 있는 판매자와 부족한 구매자를 매칭하고 거래를 체결합니다.

거래 가격: p2p_price (buy_price > p2p_price > sell_price)
  - 판매자: sell_price < p2p_price 이므로 수익 증가
  - 구매자: p2p_price < buy_price 이므로 비용 절감
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import mesa

if TYPE_CHECKING:
    from simulation.agents.prosumer import ProsumerAgent


@dataclass
class TradeRecord:
    """체결된 거래 기록."""
    step: int
    hour: int
    seller_id: str
    buyer_id: str
    amount_kw: float
    price_p2p: float
    revenue_seller: float   # 판매자 수익 (원)
    saving_buyer: float     # 구매자 절감 (원)


class EnergyMarketAgent(mesa.Agent):
    """
    에너지 거래 마켓 에이전트 (단지 내 중앙 중개자).

    매 스텝마다:
      1) 잉여 PV 보유 ProsumerAgent → 판매 후보 목록 작성
      2) 전력 부족 ProsumerAgent → 구매 후보 목록 작성
      3) Greedy 매칭: 잉여량 큰 판매자 → 부족량 큰 구매자 순으로 매칭
      4) 체결량·가격 기록, 에이전트에 반영
    """

    def __init__(
        self,
        model: mesa.Model,
        min_trade_kw: float = 0.2,          # 최소 거래 단위 (kW)
        max_trade_kw: float = 100.0,         # 최대 단일 거래 (kW)
        commission_rate: float = 0.02,       # 중개 수수료 (2%)
    ):
        super().__init__(model)
        self.min_trade_kw     = min_trade_kw
        self.max_trade_kw     = max_trade_kw
        self.commission_rate  = commission_rate

        # ── 스텝 상태 ─────────────────────────────────────────
        self.trades_this_step: list[TradeRecord] = []
        self.matched_kw_this_step: float = 0.0
        self.unmatched_surplus_kw: float = 0.0
        self.unmatched_deficit_kw: float = 0.0

        # ── 누적 통계 ─────────────────────────────────────────
        self.total_trades: int             = 0
        self.total_matched_kwh: float      = 0.0
        self.total_revenue_krw: float      = 0.0   # 마켓 수수료 수입
        self.total_seller_revenue_krw: float = 0.0
        self.total_buyer_saving_krw: float   = 0.0

        self.trade_log: list[TradeRecord] = []

    # ─────────────────────────────────────────────────────────────
    # Mesa step()
    # ─────────────────────────────────────────────────────────────
    def step(self) -> None:
        from simulation.agents.prosumer import ProsumerAgent

        self.trades_this_step = []
        self.matched_kw_this_step = 0.0

        prosumers: list[ProsumerAgent] = list(
            self.model.agents_by_type.get(ProsumerAgent) or []
        )

        # ── 1) 판매자 / 구매자 분류 ──────────────────────────
        sellers = [
            (a, a.surplus_kw)
            for a in prosumers
            if a.surplus_kw >= self.min_trade_kw
        ]
        buyers = [
            (a, a.deficit_kw)
            for a in prosumers
            if a.deficit_kw >= self.min_trade_kw
        ]

        # 잉여량 내림차순, 부족량 내림차순 정렬
        sellers.sort(key=lambda x: x[1], reverse=True)
        buyers.sort(key=lambda x: x[1], reverse=True)

        # 잔여 공급·수요 (변경 가능 리스트로 변환)
        seller_remaining = [s for _, s in sellers]
        buyer_remaining  = [b for _, b in buyers]

        # ── 2) Greedy 매칭 ────────────────────────────────────
        for si, (seller_agent, _) in enumerate(sellers):
            if seller_remaining[si] < self.min_trade_kw:
                continue
            for bi, (buyer_agent, _) in enumerate(buyers):
                if buyer_remaining[bi] < self.min_trade_kw:
                    continue
                if seller_agent is buyer_agent:
                    continue

                trade_kw = min(
                    seller_remaining[si],
                    buyer_remaining[bi],
                    self.max_trade_kw,
                )
                if trade_kw < self.min_trade_kw:
                    continue

                price_p2p = seller_agent.current_price_p2p
                dt = 0.25  # 15분 = 0.25 h

                # 수익 / 절감 계산
                revenue_seller = trade_kw * dt * price_p2p * (1 - self.commission_rate)
                saving_buyer   = trade_kw * dt * (buyer_agent.current_price_buy - price_p2p)
                commission     = trade_kw * dt * price_p2p * self.commission_rate

                # 에이전트 상태 업데이트
                seller_agent.energy_sold_kw    += trade_kw
                seller_agent.trading_revenue   += revenue_seller
                seller_agent.cumulative_saving += revenue_seller
                buyer_agent.energy_bought_kw   += trade_kw
                buyer_agent.cumulative_saving  += max(saving_buyer, 0.0)

                # 잔여 공급·수요 감소
                seller_remaining[si] -= trade_kw
                buyer_remaining[bi]  -= trade_kw
                self.matched_kw_this_step += trade_kw

                record = TradeRecord(
                    step=self.model.current_step,
                    hour=self.model.current_hour,
                    seller_id=seller_agent.prosumer_id,
                    buyer_id=buyer_agent.prosumer_id,
                    amount_kw=round(trade_kw, 3),
                    price_p2p=round(price_p2p, 2),
                    revenue_seller=round(revenue_seller, 2),
                    saving_buyer=round(max(saving_buyer, 0.0), 2),
                )
                self.trades_this_step.append(record)
                self.trade_log.append(record)

                # 누적
                self.total_trades           += 1
                self.total_matched_kwh      += trade_kw * dt
                self.total_revenue_krw      += commission
                self.total_seller_revenue_krw += revenue_seller
                self.total_buyer_saving_krw   += max(saving_buyer, 0.0)

        # ── 3) 미매칭 잔량 기록 ───────────────────────────────
        self.unmatched_surplus_kw = sum(seller_remaining)
        self.unmatched_deficit_kw = sum(buyer_remaining)

    # ─────────────────────────────────────────────────────────────
    # 공개 속성
    # ─────────────────────────────────────────────────────────────
    @property
    def market_efficiency(self) -> float:
        """매칭 효율 = 매칭량 / (매칭량 + 미매칭 공급량)."""
        total = self.matched_kw_this_step + self.unmatched_surplus_kw
        return self.matched_kw_this_step / total if total > 0 else 0.0

    @property
    def total_community_saving_krw(self) -> float:
        return self.total_seller_revenue_krw + self.total_buyer_saving_krw
