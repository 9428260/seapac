"""
Execution Orchestrator (PRD §7 — seapac_parallel_agents_prd.md).

Runs Policy, Eco Saver, and Storage agents in parallel, merges results,
applies veto rules (Policy + Storage), and produces final executable action bundle.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .contracts import decisions_to_candidate_bundle, orchestrator_output_to_decisions
from .policy_agent import PolicyConfig, PolicyAgentOutput, run_policy_agent
from .eco_saver_agent import EcoSaverOutput, run_eco_saver_agent
from .storage_agent import StorageAgentOutput, run_storage_agent


@dataclass
class OrchestratorOutput:
    """Execution Orchestrator output (PRD §7)."""
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    modified_actions: list[dict] = field(default_factory=list)
    approved_actions_detail: list[dict] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    policy_violation_report: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    notification_payload: list[dict] = field(default_factory=list)
    evaluated_steps: int = 0
    step_summaries: list[dict] = field(default_factory=list)
    llm_agent_reviews: dict = field(default_factory=dict)
    llm_merge_summary: dict = field(default_factory=dict)


def _run_policy_sync(site_state: dict, candidate_actions: list[dict], config: PolicyConfig | None) -> PolicyAgentOutput:
    return run_policy_agent(site_state, candidate_actions, config)


def _run_eco_sync(site_state: dict, candidate_actions: list[dict], peak_kw: float) -> EcoSaverOutput:
    return run_eco_saver_agent(site_state, candidate_actions, peak_threshold_kw=peak_kw)


def _run_storage_sync(
    site_state: dict,
    candidate_actions: list[dict],
    max_charge_kw: float,
    max_discharge_kw: float,
) -> StorageAgentOutput:
    return run_storage_agent(
        site_state,
        candidate_actions,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
    )


async def _run_parallel_async(
    site_state: dict,
    candidate_actions: list[dict],
    policy_config: PolicyConfig | None,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> tuple[PolicyAgentOutput, EcoSaverOutput, StorageAgentOutput]:
    """Run all three agents in parallel. PRD §9: on failure, apply safe fallbacks."""

    def safe_policy() -> PolicyAgentOutput:
        try:
            return _run_policy_sync(site_state, candidate_actions, policy_config)
        except Exception:
            # Policy Agent failure → safe mode: reject all (block execution)
            out = PolicyAgentOutput()
            out.rejected_actions = [a.get("action_id", "") for a in candidate_actions if a.get("action_id")]
            out.policy_violation_report = ["Policy agent failure: safe mode — execution blocked"]
            out.risk_score = 1.0
            return out

    def safe_eco() -> EcoSaverOutput:
        try:
            return _run_eco_sync(site_state, candidate_actions, peak_threshold_kw)
        except Exception:
            # Eco Saver failure → continue without recommendations
            return EcoSaverOutput()

    def safe_storage() -> StorageAgentOutput:
        try:
            return _run_storage_sync(site_state, candidate_actions, max_charge_kw, max_discharge_kw)
        except Exception:
            # Storage Agent failure → device control disabled (reject ESS only; pass through market/DR)
            out = StorageAgentOutput()
            for a in candidate_actions:
                if a.get("type") == "ess":
                    out.rejected_actions.append(a.get("action_id", ""))
                else:
                    out.approved_actions.append(a.get("action_id", ""))
                    out.approved_actions_detail.append(dict(a))
            out.ess.feasible = False
            return out

    loop = asyncio.get_event_loop()
    policy_f = loop.run_in_executor(None, safe_policy)
    eco_f = loop.run_in_executor(None, safe_eco)
    storage_f = loop.run_in_executor(None, safe_storage)
    policy_out, eco_out, storage_out = await asyncio.gather(policy_f, eco_f, storage_f)
    return policy_out, eco_out, storage_out


def _merge_results(
    policy_out: PolicyAgentOutput,
    storage_out: StorageAgentOutput,
    eco_out: EcoSaverOutput,
    candidate_actions: list[dict],
) -> OrchestratorOutput:
    """
    Merge agent outputs. Priority (PRD §6): Policy and Storage have veto authority.
    Final approved = action_id in (Policy approved ∩ Storage approved).
    Eco Saver is advisory only → recommendations only.
    """
    policy_approved = set(policy_out.approved_actions)
    storage_approved = set(storage_out.approved_actions)
    policy_rejected = set(policy_out.rejected_actions)
    storage_rejected = set(storage_out.rejected_actions)

    final_approved_ids = policy_approved & storage_approved
    final_rejected = list((policy_rejected | storage_rejected) - final_approved_ids)

    # Build approved_actions_detail: use Policy's modified version if present, else Storage, else original
    policy_detail_by_id = {a.get("action_id"): a for a in policy_out.approved_actions_detail if a.get("action_id")}
    storage_detail_by_id = {a.get("action_id"): a for a in storage_out.approved_actions_detail if a.get("action_id")}
    candidate_by_id = {a.get("action_id"): a for a in candidate_actions if a.get("action_id")}

    approved_detail = []
    for aid in final_approved_ids:
        if aid in policy_detail_by_id:
            approved_detail.append(policy_detail_by_id[aid])
        elif aid in storage_detail_by_id:
            approved_detail.append(storage_detail_by_id[aid])
        elif aid in candidate_by_id:
            approved_detail.append(candidate_by_id[aid])

    modified = list(policy_out.modified_actions) + list(storage_out.modified_actions)
    recommendations = list(eco_out.recommendations)
    notification_payload = list(eco_out.notification_payload)
    llm_agent_reviews = {
        "policy": dict(getattr(policy_out, "llm_review", {}) or {}),
        "eco_saver": dict(getattr(eco_out, "llm_review", {}) or {}),
        "storage": dict(getattr(storage_out, "llm_review", {}) or {}),
    }

    llm_merge_summary: dict[str, Any] = {}
    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from alfp.llm import is_llm_enabled, get_llm

        if is_llm_enabled("execution_merge"):
            system = """당신은 병렬 실행 오케스트레이터 보조 분석기입니다.
Policy, Storage, Eco Saver 결과를 합친 최종 승인 판단을 한국어로 짧게 요약하세요.
JSON only:
{"summary": string, "approval_rationale": string, "operator_note": string}"""
            user = (
                f"candidate_actions={json.dumps(candidate_actions, ensure_ascii=False)}\n"
                f"llm_agent_reviews={json.dumps(llm_agent_reviews, ensure_ascii=False)}\n"
                f"final_approved_ids={json.dumps(list(final_approved_ids), ensure_ascii=False)}\n"
                f"final_rejected_ids={json.dumps(final_rejected, ensure_ascii=False)}\n"
                "Output JSON only."
            )
            llm = get_llm(temperature=0.1, stage="execution_merge")
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            text = resp.content if hasattr(resp, "content") else str(resp)
            llm_merge_summary = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        llm_merge_summary = {}

    return OrchestratorOutput(
        approved_actions=list(final_approved_ids),
        rejected_actions=final_rejected,
        modified_actions=modified,
        approved_actions_detail=approved_detail,
        recommendations=recommendations,
        policy_violation_report=list(policy_out.policy_violation_report),
        risk_score=policy_out.risk_score,
        notification_payload=notification_payload,
        evaluated_steps=1,
        step_summaries=[],
        llm_agent_reviews=llm_agent_reviews,
        llm_merge_summary=llm_merge_summary,
    )


def _run_single_bundle(
    bundle: dict,
    *,
    policy_config: PolicyConfig | None,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    use_async: bool,
) -> OrchestratorOutput:
    site_state = bundle.get("site_state") or {}
    candidate_actions = bundle.get("candidate_actions") or []

    if use_async:
        policy_out, eco_out, storage_out = asyncio.run(
            _run_parallel_async(
                site_state,
                candidate_actions,
                policy_config,
                peak_threshold_kw,
                max_charge_kw,
                max_discharge_kw,
            )
        )
    else:
        policy_out = _run_policy_sync(site_state, candidate_actions, policy_config)
        eco_out = _run_eco_sync(site_state, candidate_actions, peak_threshold_kw)
        storage_out = _run_storage_sync(site_state, candidate_actions, max_charge_kw, max_discharge_kw)

    return _merge_results(policy_out, storage_out, eco_out, candidate_actions)


def _run_stepwise_evaluation(
    step_bundles: list[dict],
    *,
    policy_config: PolicyConfig | None,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    use_async: bool,
) -> OrchestratorOutput:
    """Evaluate each time step against its own site state and aggregate the results."""
    aggregate = OrchestratorOutput()
    approved_order: list[str] = []
    rejected_order: list[str] = []

    for step_bundle in step_bundles:
        out = _run_single_bundle(
            step_bundle,
            policy_config=policy_config,
            peak_threshold_kw=peak_threshold_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            use_async=use_async,
        )
        aggregate.evaluated_steps += 1
        aggregate.approved_actions_detail.extend(out.approved_actions_detail)
        aggregate.modified_actions.extend(out.modified_actions)
        aggregate.recommendations.extend(out.recommendations)
        aggregate.policy_violation_report.extend(out.policy_violation_report)
        aggregate.notification_payload.extend(out.notification_payload)
        aggregate.step_summaries.append({
            "step_index": step_bundle.get("step_index"),
            "time": (step_bundle.get("site_state") or {}).get("time", ""),
            "candidate_actions": len(step_bundle.get("candidate_actions") or []),
            "approved_actions": len(out.approved_actions),
            "rejected_actions": len(out.rejected_actions),
            "risk_score": out.risk_score,
            "llm_merge_summary": out.llm_merge_summary,
        })
        aggregate.risk_score = max(aggregate.risk_score, out.risk_score)
        if out.llm_agent_reviews:
            aggregate.llm_agent_reviews[str(step_bundle.get("step_index"))] = out.llm_agent_reviews

        for action_id in out.approved_actions:
            if action_id and action_id not in approved_order:
                approved_order.append(action_id)
        for action_id in out.rejected_actions:
            if action_id and action_id not in rejected_order and action_id not in approved_order:
                rejected_order.append(action_id)

    aggregate.approved_actions = approved_order
    aggregate.rejected_actions = rejected_order
    if aggregate.step_summaries:
        aggregate.llm_merge_summary = {
            "summary": f"{len(aggregate.step_summaries)}개 step에 대해 병렬 심사 완료",
            "approval_rationale": "step별 veto 결과를 집계한 최종 승인 목록",
        }
    return aggregate


def run_parallel_evaluation(
    bundle: dict,
    *,
    policy_config: PolicyConfig | None = None,
    peak_threshold_kw: float = 500.0,
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
    use_async: bool = True,
) -> OrchestratorOutput:
    """
    Run the Final Parallel Execution Layer: Policy, Eco Saver, Storage agents in parallel,
    then merge with veto rules. Uses asyncio for parallel execution when use_async=True.

    Args:
        bundle: output of decisions_to_candidate_bundle() — site_state, candidate_actions
        policy_config: Policy agent config
        peak_threshold_kw, max_charge_kw, max_discharge_kw: agent params
        use_async: if True, run agents via asyncio.gather

    Returns:
        OrchestratorOutput (approved_actions, approved_actions_detail, recommendations, etc.)
    """
    step_bundles = bundle.get("step_bundles") or []
    if step_bundles:
        return _run_stepwise_evaluation(
            step_bundles,
            policy_config=policy_config,
            peak_threshold_kw=peak_threshold_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            use_async=use_async,
        )

    return _run_single_bundle(
        bundle,
        policy_config=policy_config,
        peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        use_async=use_async,
    )


def run_parallel_evaluation_and_convert(
    decisions: dict,
    state_json_list: list[dict] | None = None,
    *,
    policy_config: PolicyConfig | None = None,
    peak_threshold_kw: float = 500.0,
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
    use_async: bool = True,
) -> dict:
    """
    Convenience: decisions + optional state_json_list → candidate bundle → parallel evaluation
    → orchestrator output → decisions format for run_execution().
    """
    bundle = decisions_to_candidate_bundle(
        decisions,
        state_json_list,
        peak_threshold_kw=peak_threshold_kw,
    )
    out = run_parallel_evaluation(
        bundle,
        policy_config=policy_config,
        peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        use_async=use_async,
    )
    output_dict = {
        "approved_actions": out.approved_actions,
        "rejected_actions": out.rejected_actions,
        "modified_actions": out.modified_actions,
        "approved_actions_detail": out.approved_actions_detail,
        "recommendations": out.recommendations,
        "policy_violation_report": out.policy_violation_report,
        "risk_score": out.risk_score,
        "notification_payload": out.notification_payload,
        "evaluated_steps": out.evaluated_steps,
        "step_summaries": out.step_summaries,
        "llm_agent_reviews": out.llm_agent_reviews,
        "llm_merge_summary": out.llm_merge_summary,
    }
    return orchestrator_output_to_decisions(output_dict, decisions)
