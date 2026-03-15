import solara

from mesa.visualization import Slider, SolaraViz, SpaceRenderer, make_plot_component
from mesa.visualization.components import AgentPortrayalStyle

from model import EnergyTradingModel


def agent_portrayal(agent) -> AgentPortrayalStyle:
    # net_energy > 0 (판매자)면 빨간색, < 0 (구매자)면 파란색, 0 근처면 회색
    if agent.net_energy > 0.5:
        color = "#d62728"  # red
    elif agent.net_energy < -0.5:
        color = "#1f77b4"  # blue
    else:
        color = "#7f7f7f"  # gray

    # 에너지 절대값에 따라 size 조정 (과도한 값 방지는 min/max)
    size = min(50, 10 + abs(agent.net_energy) * 5)

    x, y = agent.cell.coordinate
    return AgentPortrayalStyle(
        x=x,
        y=y,
        color=color,
        marker="s",
        size=size,
        alpha=0.9,
    )


model_params = {
    "width": 10,
    "height": 10,
    "initial_energy": Slider(
        "Initial energy", value=5.0, min=0.0, max=10.0, step=0.5
    ),
    "target_energy": Slider(
        "Target energy", value=5.0, min=0.0, max=10.0, step=0.5
    ),
    "energy_spread": Slider(
        "Energy spread", value=3.0, min=0.0, max=5.0, step=0.5
    ),
    "initial_cash": Slider(
        "Initial cash", value=100.0, min=0.0, max=200.0, step=10.0
    ),
    "base_price": Slider(
        "Base price", value=10.0, min=1.0, max=30.0, step=1.0
    ),
    "price_spread": Slider(
        "Price spread", value=3.0, min=0.0, max=10.0, step=0.5
    ),
}


# 초기 모델과 렌더러 설정 (Altair 백엔드 사용: Mesa 예제와 동일 패턴)
_initial_model = EnergyTradingModel()
_renderer = SpaceRenderer(_initial_model, backend="altair").setup_agents(
    agent_portrayal
)
_renderer.render()

# 시계열 플롯 (평균 가격, 거래량, 불균형)
MetricsPlot = make_plot_component(
    {
        "AvgPrice": "#ff7f0e",
        "TotalVolume": "#2ca02c",
        "TotalImbalance": "#9467bd",
    }
)

_page = SolaraViz(
    _initial_model,
    _renderer,
    components=[MetricsPlot],
    model_params=model_params,
    name="Energy Trading Simulator",
)


@solara.component
def Page():
    return _page

