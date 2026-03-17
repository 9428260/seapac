"""
DeepAgents-backed forecast planner for ALFP.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field
from pydantic import ConfigDict

from alfp.deepagents.common import extract_structured_response
from alfp.llm import get_llm


class CandidateRiskComparison(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str
    risk_score: float
    explainability_score: float
    summary: str


class CandidateStrategy(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_id: str
    model: str
    variant: str
    model_params: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    forecast_horizon: int
    rationale: str
    strengths: list[str] = Field(default_factory=list)
    risk_score: float
    risk_reasons: list[str] = Field(default_factory=list)
    explainability_score: float


class ForecastPlannerDeepPlan(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data_characteristics: list[str] = Field(default_factory=list)
    candidate_strategies: list[CandidateStrategy] = Field(default_factory=list)
    candidate_risk_comparison: list[CandidateRiskComparison] = Field(default_factory=list)
    failure_hypotheses: list[str] = Field(default_factory=list)
    reexperiment_plan: list[str] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selected_model: str = ""
    model_params: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    forecast_horizon: int
    reasoning: str = ""
    data_insights: str = ""
    risk_factors: list[str] = Field(default_factory=list)
    explainability_notes: list[str] = Field(default_factory=list)


class ForecastStrategyDraft(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data_characteristics: list[str] = Field(default_factory=list)
    candidate_strategies: list[CandidateStrategy] = Field(default_factory=list)
    failure_hypotheses: list[str] = Field(default_factory=list)
    reexperiment_plan: list[str] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selected_model: str = ""
    model_params: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    forecast_horizon: int
    reasoning: str = ""
    data_insights: str = ""


class ForecastRiskReview(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    candidate_risk_comparison: list[CandidateRiskComparison] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    explainability_notes: list[str] = Field(default_factory=list)
    selected_candidate_id: str = ""
    selected_model: str = ""
    model_params: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    forecast_horizon: int
    reasoning: str = ""


def invoke_deepagents_forecast_planner(
    *,
    system_prompt: str,
    user_prompt: str,
    stage: str = "alfp_forecast_planner",
) -> dict[str, Any]:
    """
    Execute the ALFP forecast planner through explicit agent-to-agent handoffs.
    """
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend

    model = get_llm(temperature=0.0, stage=stage)
    planner_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the forecast strategy designer. Analyze the forecasting context, derive data "
            "characteristics, generate candidate strategies, propose failure hypotheses and "
            "re-experiment ideas, then recommend an initial candidate."
        ),
        response_format=ForecastStrategyDraft,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_forecast_strategy_designer",
    )
    planner_result = planner_agent.invoke({"messages": [{"role": "user", "content": user_prompt}]})
    planner_output = extract_structured_response(
        planner_result,
        error_message="forecast strategy designer returned no structured_response",
    )

    risk_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the forecast risk reviewer. Review the planner's proposed strategies for "
            "validation risk, explainability tradeoffs, MAPE failure, and peak accuracy degradation. "
            "Return conservative risk ranking and candidate adjustments when needed."
        ),
        response_format=ForecastRiskReview,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_forecast_risk_reviewer",
    )
    risk_prompt = (
        "Review the planner output below and return only the structured risk review.\n\n"
        f"[original_request]\n{user_prompt}\n\n"
        f"[planner_output]\n{json.dumps(planner_output, ensure_ascii=False)}"
    )
    risk_result = risk_agent.invoke({"messages": [{"role": "user", "content": risk_prompt}]})
    risk_output = extract_structured_response(
        risk_result,
        error_message="forecast risk reviewer returned no structured_response",
    )

    coordinator_agent = create_deep_agent(
        model=model,
        system_prompt=(
            f"{system_prompt}\n\n"
            "You are the forecast planning coordinator. Synthesize the strategy designer and risk "
            "reviewer outputs into one final explainable forecast plan. Resolve conflicts explicitly "
            "and prefer safer candidates when the expected quality difference is small."
        ),
        response_format=ForecastPlannerDeepPlan,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_forecast_planner_coordinator",
    )
    coordinator_prompt = (
        "Combine the following agent outputs into the final structured forecast plan.\n\n"
        f"[original_request]\n{user_prompt}\n\n"
        f"[strategy_designer_output]\n{json.dumps(planner_output, ensure_ascii=False)}\n\n"
        f"[risk_reviewer_output]\n{json.dumps(risk_output, ensure_ascii=False)}"
    )
    result = coordinator_agent.invoke(
        {
            "messages": [{"role": "user", "content": coordinator_prompt}],
            "files": {
                "/context/forecast_planner_request.txt": {
                    "content": coordinator_prompt.splitlines(),
                    "modified_at": "",
                }
            },
        }
    )
    return extract_structured_response(
        result,
        error_message="deepagents forecast planner coordinator returned no structured_response",
    )
