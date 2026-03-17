"""
DeepAgents-backed decision agent for ALFP using MCP skills.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, ConfigDict
from langchain_core.tools import tool

from alfp.deepagents.common import extract_structured_response
from alfp.llm import get_llm
from alfp.mcp.decision_skills_client import call_decision_skill


class CandidateComparison(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str
    expected_profit_krw: float
    risk_score: float
    policy_violation_probability: float
    battery_degradation_cost_krw: float
    explainability_score: float
    overall_score: float
    summary: str


class DecisionPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    scenario_mode: str
    selected_candidate_id: str
    candidate_portfolios: list[dict[str, Any]] = Field(default_factory=list)
    candidate_comparisons: list[CandidateComparison] = Field(default_factory=list)
    selected_candidate: dict[str, Any] = Field(default_factory=dict)
    mode_guidance: list[str] = Field(default_factory=list)
    ess_strategy: str
    trading_strategy: str
    dr_strategy: str
    overall_recommendation: str
    priority_actions: list[str] = Field(default_factory=list)
    expected_savings: str = ""
    alert_level: str = "정상"


class StrategyCandidatePlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    scenario_mode: str
    candidate_portfolios: list[dict[str, Any]] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selected_candidate: dict[str, Any] = Field(default_factory=dict)
    mode_guidance: list[str] = Field(default_factory=list)
    ess_strategy: str = ""
    trading_strategy: str = ""
    dr_strategy: str = ""
    overall_recommendation: str = ""
    priority_actions: list[str] = Field(default_factory=list)
    expected_savings: str = ""
    alert_level: str = "정상"


class StrategyRiskReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_comparisons: list[CandidateComparison] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selected_candidate: dict[str, Any] = Field(default_factory=dict)
    mode_guidance: list[str] = Field(default_factory=list)
    ess_strategy: str = ""
    trading_strategy: str = ""
    dr_strategy: str = ""
    overall_recommendation: str = ""
    priority_actions: list[str] = Field(default_factory=list)
    expected_savings: str = ""
    alert_level: str = "정상"


@tool
def generate_strategy_candidates(context_json: str) -> str:
    """Generate multiple ESS, trading, and DR portfolio candidates for the given context."""
    return json.dumps(call_decision_skill("generate_strategy_candidates", context_json=context_json), ensure_ascii=False)


@tool
def compare_strategy_candidates(context_json: str, candidates_json: str) -> str:
    """Compare candidate portfolios on profit, risk, policy, battery degradation, and explainability."""
    return json.dumps(
        call_decision_skill(
            "compare_strategy_candidates",
            context_json=context_json,
            candidates_json=candidates_json,
        ),
        ensure_ascii=False,
    )


@tool
def recommend_mode_profile(context_json: str) -> str:
    """Return decision guidance for short horizon, day ahead, anomaly response, and prosumer type."""
    return json.dumps(call_decision_skill("recommend_mode_profile", context_json=context_json), ensure_ascii=False)


def invoke_deepagents_decision_agent(
    *,
    system_prompt: str,
    user_prompt: str,
    stage: str = "alfp_decision",
) -> dict[str, Any]:
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend

    model = get_llm(temperature=0.2, stage=stage)
    strategy_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the portfolio strategist. You must use the available MCP-backed skills to "
            "generate candidate combinations, distinguish scenario mode, adapt to prosumer type, "
            "and propose an initial selected candidate with operator guidance."
        ),
        tools=[generate_strategy_candidates, compare_strategy_candidates, recommend_mode_profile],
        response_format=StrategyCandidatePlan,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_portfolio_strategist",
    )
    strategy_result = strategy_agent.invoke({"messages": [{"role": "user", "content": user_prompt}]})
    strategy_output = extract_structured_response(
        strategy_result,
        error_message="portfolio strategist returned no structured_response",
    )

    risk_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the portfolio risk reviewer. You must use the MCP-backed comparison and mode "
            "skills to stress-test the proposed portfolios for policy violation probability, "
            "operational risk, degradation cost, and explainability."
        ),
        tools=[compare_strategy_candidates, recommend_mode_profile],
        response_format=StrategyRiskReview,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_portfolio_risk_reviewer",
    )
    risk_prompt = (
        "Review the strategist output below. Re-check the selected candidate and return only the "
        "structured risk review.\n\n"
        f"[original_request]\n{user_prompt}\n\n"
        f"[strategist_output]\n{json.dumps(strategy_output, ensure_ascii=False)}"
    )
    risk_result = risk_agent.invoke({"messages": [{"role": "user", "content": risk_prompt}]})
    risk_output = extract_structured_response(
        risk_result,
        error_message="portfolio risk reviewer returned no structured_response",
    )

    coordinator_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the decision coordinator. Merge the strategist and risk reviewer outputs into "
            "the final structured decision plan. Prefer the safer explainable candidate when risk "
            "or policy concerns materially change the ranking."
        ),
        tools=[compare_strategy_candidates, recommend_mode_profile],
        response_format=DecisionPlan,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_decision_coordinator",
    )
    coordinator_prompt = (
        "Combine the following agent outputs into the final structured decision plan.\n\n"
        f"[original_request]\n{user_prompt}\n\n"
        f"[portfolio_strategist_output]\n{json.dumps(strategy_output, ensure_ascii=False)}\n\n"
        f"[portfolio_risk_reviewer_output]\n{json.dumps(risk_output, ensure_ascii=False)}"
    )
    result = coordinator_agent.invoke({"messages": [{"role": "user", "content": coordinator_prompt}]})
    return extract_structured_response(
        result,
        error_message="deepagents decision coordinator returned no structured_response",
    )
