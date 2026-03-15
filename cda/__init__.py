"""
CDA (Continuous Double Auction) 기반 Multi-Agent Energy Market.

cda_energy_market_prd.md에 따른 Order Book, Matching Engine, Coordinator, Settlement 구현.
cda_strategy_negotiation_prd.md에 따른 Strategy Agent(LLM) 및 Negotiation Layer 포함.
seapac_agents의 MarketCoordinator 및 Execution을 대체하여 사용할 수 있음.
"""

from cda.orderbook import OrderBook, Bid, Ask
from cda.matching import match_cda
from cda.coordinator import (
    run_cda_step,
    run_cda_decision_series,
    run_cda_decision_series_with_agents,
    run_cda_step_with_strategy_and_negotiation,
    run_cda_decision_series_with_agents_and_negotiation,
)
from cda.settlement import run_execution, ExecutionResult
from cda.strategy_agent import generate_strategy, StrategyRecommendation
from cda.negotiation import run_negotiation, NegotiationResult, NegotiationStep

__all__ = [
    "OrderBook",
    "Bid",
    "Ask",
    "match_cda",
    "run_cda_step",
    "run_cda_decision_series",
    "run_cda_decision_series_with_agents",
    "run_cda_step_with_strategy_and_negotiation",
    "run_cda_decision_series_with_agents_and_negotiation",
    "run_execution",
    "ExecutionResult",
    "generate_strategy",
    "StrategyRecommendation",
    "run_negotiation",
    "NegotiationResult",
    "NegotiationStep",
]
