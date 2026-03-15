from mesa.discrete_space import CellAgent


class EnergyTrader(CellAgent):
    """단순 에너지 트레이더 에이전트.

    - energy: 현재 보유 에너지 (kWh 같은 단위로 가정)
    - target_energy: 목표 에너지 수준 (이보다 많으면 판매자, 적으면 구매자)
    - cash: 보유 현금
    - bid_price / ask_price: 개별 호가
    """

    def __init__(
        self,
        model: "EnergyTradingModel",
        cell,
        energy: float,
        target_energy: float,
        cash: float,
        bid_price: float,
        ask_price: float,
    ):
        super().__init__(model)
        self.cell = cell
        self.energy = energy
        self.target_energy = target_energy
        self.cash = cash
        self.bid_price = bid_price
        self.ask_price = ask_price

        self.traded_volume = 0.0
        self.avg_trade_price = 0.0

    @property
    def net_energy(self) -> float:
        """양수면 잉여(판매자), 음수면 부족(구매자)."""
        return self.energy - self.target_energy

    def step(self) -> None:
        """단일 타임스텝에서 이웃과 거래 시도."""
        # 이미 목표 근처면 아무 것도 하지 않음
        if abs(self.net_energy) < 0.01:
            return

        # 이웃 후보
        neighbors = list(self.cell.neighborhood.agents)
        self.model.rng.shuffle(neighbors)

        for neighbor in neighbors:
            if neighbor is self:
                continue

            # 현재 에이전트가 구매자 역할
            if self.net_energy < 0 and neighbor.net_energy > 0:
                # 가격 조건: self.bid >= neighbor.ask
                if self.bid_price >= neighbor.ask_price and self.cash >= neighbor.ask_price:
                    self._trade_with(neighbor, buyer=self, seller=neighbor)
                    break

            # 현재 에이전트가 판매자 역할
            elif self.net_energy > 0 and neighbor.net_energy < 0:
                if neighbor.bid_price >= self.ask_price and neighbor.cash >= self.ask_price:
                    self._trade_with(neighbor, buyer=neighbor, seller=self)
                    break

    def _trade_with(
        self,
        counterparty: "EnergyTrader",
        buyer: "EnergyTrader",
        seller: "EnergyTrader",
        quantity: float = 1.0,
    ) -> None:
        """1단위 에너지 거래."""
        # 실제로 거래 가능한 최대 수량 제한 (단순히 1단위와 잔고 체크)
        if seller.energy <= 0 or buyer.cash <= 0:
            return

        price = (buyer.bid_price + seller.ask_price) / 2.0
        total_cost = price * quantity

        if buyer.cash < total_cost or seller.energy < quantity:
            return

        # 잔고 업데이트
        buyer.cash -= total_cost
        buyer.energy += quantity

        seller.cash += total_cost
        seller.energy -= quantity

        # 통계용
        for agent in (buyer, seller):
            agent.traded_volume += quantity
            # 간단히 마지막 가격을 평균으로 사용
            agent.avg_trade_price = price

        # 모델 수준의 집계
        self.model.total_traded_volume += quantity
        self.model.last_trade_price = price

