"""
DeepAgents-backed governance critic for ALFP.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from alfp.deepagents.common import extract_structured_response
from alfp.llm import get_llm


def _candidate_payload(context_json: str) -> dict[str, Any]:
    return json.loads(context_json)


@tool
def analyze_governance_landscape(context_json: str) -> str:
    """Summarize confidence, candidate margin, risk, and policy signals for governance review."""
    ctx = _candidate_payload(context_json)
    evidence = ctx.get("evidence") or {}
    decisions = ctx.get("decisions") or {}
    comparisons = decisions.get("candidate_comparisons") or []
    selected = decisions.get("selected_candidate") or {}
    top_margin = 0.0
    if len(comparisons) >= 2:
        top_margin = float(comparisons[0].get("overall_score", 0.0)) - float(comparisons[1].get("overall_score", 0.0))
    out = {
        "confidence_score": evidence.get("confidence_score"),
        "confidence_factors": evidence.get("confidence_factors", []),
        "selected_candidate_id": decisions.get("selected_candidate_id"),
        "candidate_count": len(decisions.get("strategy_candidates") or []),
        "top_margin": round(top_margin, 4),
        "selected_risk_score": selected.get("risk_score"),
        "policy_violation_probability": selected.get("policy_violation_probability"),
        "scenario_mode": decisions.get("scenario_mode"),
    }
    return json.dumps(out, ensure_ascii=False)


@tool
def search_counterexamples(context_json: str) -> str:
    """Search candidate portfolios for counterexamples that outperform or de-risk the selected strategy."""
    ctx = _candidate_payload(context_json)
    decisions = ctx.get("decisions") or {}
    selected_id = decisions.get("selected_candidate_id")
    comparisons = decisions.get("candidate_comparisons") or []
    counterexamples = []
    selected = next((item for item in comparisons if item.get("candidate_id") == selected_id), None)
    selected_score = float(selected.get("overall_score", 0.0)) if selected else 0.0
    selected_risk = float(selected.get("risk_score", 1.0)) if selected else 1.0
    selected_policy = float(selected.get("policy_violation_probability", 1.0)) if selected else 1.0

    for item in comparisons:
        if item.get("candidate_id") == selected_id:
            continue
        score = float(item.get("overall_score", 0.0))
        risk = float(item.get("risk_score", 1.0))
        policy = float(item.get("policy_violation_probability", 1.0))
        if score >= selected_score - 0.02 and (risk < selected_risk or policy < selected_policy):
            counterexamples.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "reason": (
                        f"selected와 유사한 점수({score:.3f})이면서 risk={risk:.2f}, "
                        f"policy={policy:.2f}로 더 안전한 반례입니다."
                    ),
                }
            )
    return json.dumps({"counterexamples": counterexamples[:5]}, ensure_ascii=False)


@tool
def compare_sandbox_candidates(context_json: str) -> str:
    """Compare candidate portfolios by expected profit, degradation cost, and policy-aware sandbox score."""
    ctx = _candidate_payload(context_json)
    decisions = ctx.get("decisions") or {}
    candidates = decisions.get("strategy_candidates") or []
    ranked = []
    for candidate in candidates:
        profit = float(candidate.get("expected_profit_krw", 0.0))
        risk = float(candidate.get("risk_score", 0.5))
        policy = float(candidate.get("policy_violation_probability", 0.2))
        degradation = float(candidate.get("battery_degradation_cost_krw", 0.0))
        sandbox_score = round(profit * 0.0014 + (1 - risk) * 0.35 + (1 - policy) * 0.2 - degradation * 0.0008, 4)
        ranked.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "sandbox_score": sandbox_score,
                "expected_profit_krw": profit,
                "risk_score": risk,
                "policy_violation_probability": policy,
                "battery_degradation_cost_krw": degradation,
            }
        )
    ranked.sort(key=lambda item: item["sandbox_score"], reverse=True)
    return json.dumps({"ranked_candidates": ranked[:5]}, ensure_ascii=False)


class GovernanceCriticPlan(BaseModel):
    risk_score: float
    failure_scenarios: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    alternative_strategy: list[dict[str, Any]] = Field(default_factory=list)
    revised_candidate_id: str = ""
    recommendation: str = ""


class GovernanceLandscapeReport(BaseModel):
    risk_score: float
    failure_scenarios: list[str] = Field(default_factory=list)
    counterexamples: list[str] = Field(default_factory=list)
    alternative_strategy: list[dict[str, Any]] = Field(default_factory=list)
    revised_candidate_id: str = ""
    recommendation: str = ""


def invoke_deepagents_governance_critic(*, context_json: str, stage: str = "governance_critic") -> dict[str, Any]:
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend

    model = get_llm(temperature=0.1, stage=stage)
    landscape_agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are the governance landscape analyst. Use the provided tools to inspect "
            "confidence, counterexamples, and sandbox comparisons, then produce the initial "
            "structured governance critique."
        ),
        tools=[analyze_governance_landscape, search_counterexamples, compare_sandbox_candidates],
        response_format=GovernanceLandscapeReport,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_governance_landscape_analyst",
    )
    landscape_result = landscape_agent.invoke({"messages": [{"role": "user", "content": context_json}]})
    landscape_output = extract_structured_response(
        landscape_result,
        error_message="governance landscape analyst returned no structured_response",
    )

    critic_agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are the governance critic coordinator. Review the landscape analyst output and "
            "return the final structured critique. Tighten the recommendation when risk remains high "
            "or the revised candidate is more defensible."
        ),
        response_format=GovernanceCriticPlan,
        backend=lambda runtime: StateBackend(runtime),
        name="alfp_governance_critic_coordinator",
    )
    critic_prompt = (
        "Finalize the governance critique from the original context and analyst report.\n\n"
        f"[original_context]\n{context_json}\n\n"
        f"[landscape_analyst_output]\n{json.dumps(landscape_output, ensure_ascii=False)}"
    )
    result = critic_agent.invoke({"messages": [{"role": "user", "content": critic_prompt}]})
    return extract_structured_response(
        result,
        error_message="deepagents governance critic coordinator returned no structured_response",
    )
