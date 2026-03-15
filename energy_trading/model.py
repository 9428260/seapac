import mesa
from mesa.datacollection import DataCollector

from agents import EnergyTrader


def _total_imbalance(model: "EnergyTradingModel") -> float:
    """전체 에너지가 목표에서 얼마나 벗어났는지 (절대값 합)."""
    return sum(abs(a.net_energy) for a in model.agents)


def _avg_price(model: "EnergyTradingModel") -> float:
    return float(model.last_trade_price) if model.last_trade_price is not None else 0.0


def _total_volume(model: "EnergyTradingModel") -> float:
    return float(model.total_traded_volume)


class EnergyTradingModel(mesa.Model):
    """단순 에너지 거래 모델."""

    def __init__(
        self,
        width: int = 10,
        height: int = 10,
        initial_energy: float = 5.0,
        target_energy: float = 5.0,
        energy_spread: float = 3.0,
        initial_cash: float = 100.0,
        base_price: float = 10.0,
        price_spread: float = 3.0,
    ):
        super().__init__()

        self.grid = mesa.discrete_space.OrthogonalMooreGrid(
            (width, height),
            torus=True,
            random=self.random,
        )

        self.total_traded_volume: float = 0.0
        self.last_trade_price: float | None = None

        # 에이전트 생성 및 배치 (각 셀에 하나씩)
        for cell in self.grid.all_cells:
            # 초기 에너지를 약간 랜덤하게
            energy = initial_energy + self.rng.normal(0, energy_spread)
            cash = max(0.0, initial_cash + self.rng.normal(0, 10.0))

            # 개별 호가
            bid = max(0.1, base_price + self.rng.normal(0, price_spread))
            ask = max(0.1, base_price + self.rng.normal(0, price_spread))

            EnergyTrader(
                self,
                cell,
                energy=energy,
                target_energy=target_energy,
                cash=cash,
                bid_price=bid,
                ask_price=ask,
            )

        # DataCollector
        self.datacollector = DataCollector(
            {
                "AvgPrice": _avg_price,
                "TotalVolume": _total_volume,
                "TotalImbalance": _total_imbalance,
            }
        )

        # 초기 상태 수집
        self.datacollector.collect(self)

    def step(self) -> None:
        # 스텝마다 거래량 리셋 (누적 지표 말고 per-step 을 보고 싶다면 여기서 사용)
        self.total_traded_volume = 0.0
        self.last_trade_price = None

        # 무작위 순서로 에이전트 활성화
        self.agents.shuffle_do("step")
        self.datacollector.collect(self)


if __name__ == "__main__":
    import pandas as pd

    model = EnergyTradingModel()
    model.run_for(20)
    # Mesa 3.5 DataCollector는 pandas helper 메서드가 없으므로 직접 생성
    df = pd.DataFrame(model.datacollector.model_vars)
    print(df.head())

