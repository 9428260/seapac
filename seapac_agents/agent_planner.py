"""
Step 3-P — LLM 기반 에이전트 실행 계획 수립 및 실행 (Agent Plan)

ALFP 결과를 확인해 Policy / Trading / Storage / EcoSaver / Simulation 단계를
계획하고 실행합니다. 실행 시에는 AgentScope Msg 기반 handoff를 사용하며,
독립 가능한 Trading / Storage / EcoSaver 단계는 병렬로 수행합니다.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AgentPlanStep:
    """에이전트 실행 계획의 단일 스텝."""
    step_id: int
    agent_name: str
    action: str
    parameters: dict[str, Any]
    depends_on: list[int]
    reason: str


@dataclass
class AgentPlan:
    """LLM 또는 규칙 기반 planner가 수립한 계획."""
    plan_id: str
    created_at: str
    objective: str
    steps: list[AgentPlanStep]
    state_summary: str
    revision: int = 0


_SUPPORTED_AGENTS = {"policy", "trading", "storage", "eco_saver", "simulate"}


def _build_rule_based_plan(
    state_summary: str,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """LLM 미사용 시 기본 실행 계획 반환."""
    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective="ALFP 예측 결과를 반영해 정책검증 후 거래·ESS/PV·DR을 병렬 조율하고 시뮬레이션으로 승인 여부를 검증한다.",
        state_summary=state_summary,
        steps=[
            AgentPlanStep(
                step_id=1,
                agent_name="policy",
                action="제약 조건을 확정하고 후속 에이전트에 전달한다.",
                parameters={
                    "max_charge_kw": max_charge_kw,
                    "max_discharge_kw": max_discharge_kw,
                    "ess_soc_min_pct": 10.0,
                    "ess_soc_max_pct": 95.0,
                    "min_trade_kw": 0.2,
                    "dr_reduction_factor": 0.30,
                },
                depends_on=[],
                reason="후속 제안은 모두 정책 제약을 공유해야 한다.",
            ),
            AgentPlanStep(
                step_id=2,
                agent_name="trading",
                action="ALFP 잉여/부족 전망을 반영해 전력거래 권고를 생성한다.",
                parameters={"peak_risk_price_ratio": 0.90},
                depends_on=[1],
                reason="정책 제약이 확정되면 거래 에이전트는 독립적으로 실행 가능하다.",
            ),
            AgentPlanStep(
                step_id=3,
                agent_name="storage",
                action="ESS와 PV 잉여 흡수/방전 전략을 수립한다.",
                parameters={
                    "price_charge_threshold": 85.0,
                    "price_discharge_threshold": 115.0,
                },
                depends_on=[1],
                reason="거래와 병렬로 ESS/PV 운용안을 준비해야 한다.",
            ),
            AgentPlanStep(
                step_id=4,
                agent_name="eco_saver",
                action="피크 억제를 위한 DR 권고를 생성한다.",
                parameters={
                    "peak_threshold_kw": peak_threshold_kw,
                    "reduction_factor": 0.30,
                },
                depends_on=[1],
                reason="피크 대응은 거래/스토리지와 병렬 평가되어야 한다.",
            ),
            AgentPlanStep(
                step_id=5,
                agent_name="simulate",
                action="병합된 제안을 시뮬레이션에 적용해 승인 여부를 검증한다.",
                parameters={},
                depends_on=[2, 3, 4],
                reason="실행 전 통합 시뮬레이션 검증이 필요하다.",
            ),
        ],
    )


_PLAN_SYSTEM_PROMPT = """당신은 에너지 커뮤니티 Agent Plan 오케스트레이터입니다.

사용 가능한 에이전트:
- "policy"    : 제약 조건 설정 및 검증
- "trading"   : 전력거래 권고 생성
- "storage"   : ESS/PV 관리 계획 생성
- "eco_saver" : 수요반응(DR) 권고 생성
- "simulate"  : 통합 실행안 시뮬레이션 검증

반드시 지켜야 할 규칙:
1. policy 는 반드시 첫 단계다.
2. trading / storage / eco_saver 는 policy 이후 병렬 실행 가능하다.
3. simulate 는 trading / storage / eco_saver 이후에만 실행한다.
4. 계획에는 policy, trading, storage, eco_saver, simulate 가 모두 포함되어야 한다.
5. 출력은 JSON only 이며 steps 각 항목에 step_id, agent_name, action, parameters, depends_on, reason 을 포함한다.
"""


_REVISE_SYSTEM_PROMPT = """당신은 에너지 커뮤니티 Agent Plan 오케스트레이터입니다.
시뮬레이션 실패와 정책 오류를 줄이도록 policy / trading / storage / eco_saver / simulate 계획을 재수립하세요.
JSON only 로 반환하세요."""


def _build_llm_plan(
    state_json_list: list[dict],
    alfp_decisions: dict,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """LLM이 상태와 ALFP 결과를 분석해 에이전트 계획을 수립."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from alfp.llm import get_llm_forced

    state_summary = _summarize_states(state_json_list)
    alfp_summary = _summarize_alfp_decisions(alfp_decisions)
    user_msg = f"""현재 상태 요약:
{state_summary}

ALFP 결정 요약:
{alfp_summary}

설정:
- peak_threshold_kw={peak_threshold_kw}
- max_charge_kw={max_charge_kw}
- max_discharge_kw={max_discharge_kw}

Policy → Trading/Storage/EcoSaver 병렬 → Simulate 순서를 만족하는 실행 계획을 수립하세요."""

    llm = get_llm_forced(temperature=0.1, stage="agent_plan")
    resp = llm.invoke([SystemMessage(content=_PLAN_SYSTEM_PROMPT), HumanMessage(content=user_msg)])
    raw_text = resp.content if hasattr(resp, "content") else str(resp)
    plan_data = _parse_plan_json(raw_text)
    return _plan_from_dict(plan_data, state_summary=state_summary)


def _revise_plan_with_llm(
    original_plan: AgentPlan,
    simulation_errors: list[str],
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """시뮬레이션 실패 시 계획 재수립."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from alfp.llm import get_llm_forced

    prev_steps_str = json.dumps(
        [
            {
                "step_id": s.step_id,
                "agent_name": s.agent_name,
                "action": s.action,
                "parameters": s.parameters,
                "depends_on": s.depends_on,
                "reason": s.reason,
            }
            for s in original_plan.steps
        ],
        ensure_ascii=False,
        indent=2,
    )
    user_msg = f"""이전 계획:
{prev_steps_str}

실패 원인:
{chr(10).join(f"- {e}" for e in simulation_errors)}

상태 요약:
{original_plan.state_summary}

설정:
- peak_threshold_kw={peak_threshold_kw}
- max_charge_kw={max_charge_kw}
- max_discharge_kw={max_discharge_kw}

실패 원인을 해결하는 개선 계획을 재수립하세요."""
    llm = get_llm_forced(temperature=0.2, stage="agent_plan")
    resp = llm.invoke([SystemMessage(content=_REVISE_SYSTEM_PROMPT), HumanMessage(content=user_msg)])
    raw_text = resp.content if hasattr(resp, "content") else str(resp)
    plan = _plan_from_dict(_parse_plan_json(raw_text), state_summary=original_plan.state_summary)
    plan.revision = original_plan.revision + 1
    return plan


def _parse_plan_json(raw_text: str) -> dict[str, Any]:
    plan_data: dict[str, Any] = {}
    try:
        plan_data = json.loads(raw_text)
    except json.JSONDecodeError:
        import re

        m = re.search(r"```(?:json)?\s*(\{[\s\S]+\})\s*```", raw_text)
        if m:
            plan_data = json.loads(m.group(1))
    if not plan_data or "steps" not in plan_data:
        raise ValueError(f"Agent plan JSON 파싱 실패: {raw_text[:300]}")
    return plan_data


def _plan_from_dict(plan_data: dict[str, Any], *, state_summary: str) -> AgentPlan:
    steps = [
        AgentPlanStep(
            step_id=int(s.get("step_id", i + 1)),
            agent_name=str(s.get("agent_name", "")).strip(),
            action=str(s.get("action", "")).strip(),
            parameters=dict(s.get("parameters") or {}),
            depends_on=[int(v) for v in (s.get("depends_on") or [])],
            reason=str(s.get("reason", "")).strip(),
        )
        for i, s in enumerate(plan_data.get("steps", []))
    ]
    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective=str(plan_data.get("objective", "")).strip() or "에너지 커뮤니티 실행 계획",
        steps=steps,
        state_summary=state_summary,
    )


def _normalize_plan(
    plan: AgentPlan,
    *,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """필수 에이전트 누락/잘못된 의존성을 보정해 실행 가능한 계획으로 정규화."""
    default_plan = _build_rule_based_plan(
        plan.state_summary,
        peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
    )
    defaults = {s.agent_name: s for s in default_plan.steps}
    incoming = [s for s in plan.steps if s.agent_name in _SUPPORTED_AGENTS]
    by_agent = {s.agent_name: s for s in incoming}
    ordered: list[AgentPlanStep] = []
    next_step_id = 1

    policy_step = by_agent.get("policy") or defaults["policy"]
    policy_step = AgentPlanStep(
        step_id=next_step_id,
        agent_name="policy",
        action=policy_step.action or defaults["policy"].action,
        parameters={**defaults["policy"].parameters, **policy_step.parameters},
        depends_on=[],
        reason=policy_step.reason or defaults["policy"].reason,
    )
    ordered.append(policy_step)
    policy_id = next_step_id
    next_step_id += 1

    parallel_ids: list[int] = []
    for agent_name in ("trading", "storage", "eco_saver"):
        src = by_agent.get(agent_name) or defaults[agent_name]
        params = {**defaults[agent_name].parameters, **src.parameters}
        if agent_name == "eco_saver":
            params["peak_threshold_kw"] = float(params.get("peak_threshold_kw", peak_threshold_kw))
        step = AgentPlanStep(
            step_id=next_step_id,
            agent_name=agent_name,
            action=src.action or defaults[agent_name].action,
            parameters=params,
            depends_on=[policy_id],
            reason=src.reason or defaults[agent_name].reason,
        )
        ordered.append(step)
        parallel_ids.append(next_step_id)
        next_step_id += 1

    simulate_src = by_agent.get("simulate") or defaults["simulate"]
    ordered.append(
        AgentPlanStep(
            step_id=next_step_id,
            agent_name="simulate",
            action=simulate_src.action or defaults["simulate"].action,
            parameters=dict(simulate_src.parameters or {}),
            depends_on=parallel_ids,
            reason=simulate_src.reason or defaults["simulate"].reason,
        )
    )

    return AgentPlan(
        plan_id=plan.plan_id,
        created_at=plan.created_at,
        objective=plan.objective,
        steps=ordered,
        state_summary=plan.state_summary,
        revision=plan.revision,
    )


def _topological_layers(steps: list[AgentPlanStep]) -> list[list[AgentPlanStep]]:
    """depends_on 관계를 wave 단위 레이어로 변환."""
    remaining = {s.step_id: s for s in steps}
    completed: set[int] = set()
    layers: list[list[AgentPlanStep]] = []
    while remaining:
        ready = [s for s in remaining.values() if set(s.depends_on).issubset(completed)]
        if not ready:
            raise ValueError("Agent plan 의존성이 순환하거나 잘못되었습니다.")
        ready = sorted(ready, key=lambda s: s.step_id)
        layers.append(ready)
        for step in ready:
            completed.add(step.step_id)
            remaining.pop(step.step_id, None)
    return layers


def _build_state_msg(state_json: dict, alfp_decisions: dict, constraints: dict[str, Any] | None) -> Any:
    from agentscope.message import Msg

    cs = state_json.get("community_state") or {}
    return Msg(
        name="AgentPlanner-State",
        content=(
            f"ALFP 기반 상태[{state_json.get('time', '?')}] "
            f"load={cs.get('total_load', 0)}kW peak_risk={cs.get('peak_risk', 'N/A')}"
        ),
        role="user",
        metadata={
            "state": state_json,
            "alfp_context": {
                "llm_strategy": alfp_decisions.get("llm_strategy") or {},
                "ess_summary": alfp_decisions.get("ess_summary") or {},
                "trading_summary": alfp_decisions.get("trading_summary") or {},
                "dr_summary": alfp_decisions.get("dr_summary") or {},
            },
            "policy_constraints": constraints or {},
        },
    )


async def _run_a2a_state_async(
    state_json: dict,
    *,
    alfp_decisions: dict,
    policy_agent: Any,
    trading_agent: Any | None,
    storage_agent: Any | None,
    eco_agent: Any | None,
    coordinator: Any,
    constraints: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """하나의 상태에 대해 A2A handoff 기반으로 병렬 제안 후 coordinator 조율."""
    from agentscope.message import Msg
    from agentscope.pipeline import MsgHub

    state_msg = _build_state_msg(state_json, alfp_decisions, constraints)
    policy_msg = await policy_agent.reply(state_msg)
    policy_constraints = (policy_msg.metadata or {}).get("constraints") or constraints
    handoff_msg = Msg(
        name="Policy-Handoff",
        content="Validated constraints and ALFP context",
        role="assistant",
        metadata={
            "state": state_json,
            "constraints": policy_constraints,
            "alfp_context": (state_msg.metadata or {}).get("alfp_context") or {},
        },
    )

    participants = [policy_agent, coordinator]
    for agent in (trading_agent, storage_agent, eco_agent):
        if agent is not None:
            participants.append(agent)

    async with MsgHub(
        participants=participants,
        announcement=handoff_msg,
        enable_auto_broadcast=False,
    ):
        tasks = []
        if trading_agent is not None:
            tasks.append(trading_agent.reply(handoff_msg))
        if storage_agent is not None:
            tasks.append(storage_agent.reply(handoff_msg))
        if eco_agent is not None:
            tasks.append(eco_agent.reply(handoff_msg))
        results = await asyncio.gather(*tasks) if tasks else []

    seller_msg = None
    storage_msg = None
    eco_msg = None
    for msg in results:
        name = getattr(msg, "name", "")
        if "SmartSeller" in name:
            seller_msg = msg
        elif "StorageMaster" in name:
            storage_msg = msg
        elif "EcoSaver" in name:
            eco_msg = msg

    final_msg = await coordinator.reply(
        state_msg,
        seller_msg=seller_msg,
        storage_msg=storage_msg,
        eco_msg=eco_msg,
    )
    decisions = (final_msg.metadata or {}).get("decisions", {}) or {}
    protocol_trace = {
        "time": state_json.get("time", ""),
        "handoffs": [
            {"from": "AgentPlanner", "to": "Policy-Agent", "type": "state"},
            {"from": "Policy-Agent", "to": "Trading-Agent", "type": "constraint"} if trading_agent is not None else None,
            {"from": "Policy-Agent", "to": "Storage-Agent", "type": "constraint"} if storage_agent is not None else None,
            {"from": "Policy-Agent", "to": "EcoSaver-Agent", "type": "constraint"} if eco_agent is not None else None,
            {"from": "Trading/Storage/EcoSaver", "to": "MarketCoordinator-Agent", "type": "proposal_merge"},
        ],
        "policy_constraints": policy_constraints,
        "seller_action": ((seller_msg.metadata or {}).get("proposal", {}) if seller_msg else {}).get("action"),
        "storage_action": ((storage_msg.metadata or {}).get("proposal", {}) if storage_msg else {}).get("action"),
        "eco_dr_count": len((((eco_msg.metadata or {}).get("proposal", {}) or {}).get("dr_events") or []) if eco_msg else []),
    }
    protocol_trace["handoffs"] = [item for item in protocol_trace["handoffs"] if item is not None]
    return decisions, protocol_trace


async def _execute_parallel_wave_async(
    state_json_list: list[dict],
    *,
    alfp_decisions: dict,
    policy_agent: Any,
    trading_agent: Any | None,
    storage_agent: Any | None,
    eco_agent: Any | None,
    coordinator: Any,
    constraints: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Trading / Storage / EcoSaver wave를 상태별 병렬 수행."""
    tasks = [
        _run_a2a_state_async(
            state,
            alfp_decisions=alfp_decisions,
            policy_agent=policy_agent,
            trading_agent=trading_agent,
            storage_agent=storage_agent,
            eco_agent=eco_agent,
            coordinator=coordinator,
            constraints=constraints,
        )
        for state in state_json_list
    ]
    results = await asyncio.gather(*tasks) if tasks else []

    merged = {
        **alfp_decisions,
        "ess_schedule": [],
        "trading_recommendations": [],
        "trading_evidence": [],
        "demand_response_events": [],
        "policy_violations": [],
    }
    protocol_trace: list[dict[str, Any]] = []
    for item, trace in results:
        merged["ess_schedule"].extend(item.get("ess_schedule", []))
        merged["trading_recommendations"].extend(item.get("trading_recommendations", []))
        merged["trading_evidence"].extend(item.get("trading_evidence", []))
        merged["demand_response_events"].extend(item.get("demand_response_events", []))
        merged["policy_violations"].extend(item.get("policy_violations", []))
        protocol_trace.append(trace)

    merged["ess_summary"] = {
        "charge_steps": sum(1 for row in merged["ess_schedule"] if row.get("action") == "charge"),
        "discharge_steps": sum(1 for row in merged["ess_schedule"] if row.get("action") == "discharge"),
        "idle_steps": sum(1 for row in merged["ess_schedule"] if row.get("action") == "idle"),
    }
    merged["trading_summary"] = {
        "total_surplus_events": len(merged["trading_recommendations"]),
        "total_surplus_kw": round(
            sum(float(r.get("surplus_kw", 0.0)) for r in merged["trading_recommendations"]),
            2,
        ),
    }
    merged["dr_summary"] = {
        "dr_event_count": len(merged["demand_response_events"]),
        "total_reduction_kw": round(
            sum(float(r.get("recommended_reduction_kw", 0.0)) for r in merged["demand_response_events"]),
            2,
        ),
    }
    return merged, protocol_trace


def _simulate_plan_decisions(
    decisions: dict[str, Any],
    *,
    peak_threshold_kw: float,
    data_path: str,
    n_steps: int,
    phase: int,
    seed: int,
    ess_capacity_kwh: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    max_peak_load_kw: float | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """시뮬레이션으로 계획 실행안을 검증."""
    from seapac_agents.execution import run_execution

    exec_result = run_execution(
        decisions,
        data_path=data_path,
        n_steps=n_steps,
        phase=phase,
        seed=seed,
        ess_capacity_kwh=ess_capacity_kwh,
        ess_peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        strict_validation=False,
        max_peak_load_kw=max_peak_load_kw,
    )
    errors = list(exec_result.validation_errors or []) + list(exec_result.simulation_approval_errors or [])
    return exec_result.approved, errors, exec_result.summary or {}


def _execute_plan(
    plan: AgentPlan,
    state_json_list: list[dict],
    alfp_decisions: dict,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    llm_used: bool = False,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    max_peak_load_kw: float | None = None,
    verbose: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], bool | None, list[str], dict[str, Any]]:
    """계획을 실행하고 decisions, 로그, simulation 결과를 반환."""
    from seapac_agents.decision import (
        _init_agentscope,
        EcoSaverAgentAS,
        MarketCoordinatorAgentAS,
        PolicyAgentAS,
        SmartSellerAgentAS,
        StorageMasterAgentAS,
    )

    _init_agentscope()
    layers = _topological_layers(plan.steps)
    agent_logs: list[dict[str, Any]] = []
    simulation_approved: bool | None = None
    simulation_errors: list[str] = []
    simulation_summary: dict[str, Any] = {}
    decisions: dict[str, Any] = {
        **(alfp_decisions or {}),
        "ess_schedule": [],
        "trading_recommendations": [],
        "trading_evidence": [],
        "demand_response_events": [],
        "policy_violations": [],
    }

    policy_agent = None
    constraints: dict[str, Any] = {}
    coordinator = None

    for wave_idx, layer in enumerate(layers, start=1):
        if verbose:
            print(f"    [AgentPlan Wave {wave_idx}] {[step.agent_name for step in layer]}")

        layer_names = {step.agent_name for step in layer}
        if "policy" in layer_names:
            step = next(s for s in layer if s.agent_name == "policy")
            constraints = {
                "max_charge_kw": float(step.parameters.get("max_charge_kw", max_charge_kw)),
                "max_discharge_kw": float(step.parameters.get("max_discharge_kw", max_discharge_kw)),
                "ess_soc_min_pct": float(step.parameters.get("ess_soc_min_pct", 10.0)),
                "ess_soc_max_pct": float(step.parameters.get("ess_soc_max_pct", 95.0)),
                "min_trade_kw": float(step.parameters.get("min_trade_kw", 0.2)),
                "dr_reduction_factor": float(step.parameters.get("dr_reduction_factor", 0.30)),
            }
            policy_agent = PolicyAgentAS(**constraints)
            coordinator = MarketCoordinatorAgentAS(policy_agent)
            agent_logs.append(
                {
                    "step_id": step.step_id,
                    "agent": "policy",
                    "status": "ok",
                    "wave": wave_idx,
                    "parallel": False,
                    "constraints": constraints,
                    "a2a_protocol": "state -> policy constraints handoff",
                }
            )

        operational_steps = [s for s in layer if s.agent_name in {"trading", "storage", "eco_saver"}]
        if operational_steps:
            if policy_agent is None or coordinator is None:
                raise ValueError("policy step 없이 operational agent를 실행할 수 없습니다.")

            trading_step = next((s for s in operational_steps if s.agent_name == "trading"), None)
            storage_step = next((s for s in operational_steps if s.agent_name == "storage"), None)
            eco_step = next((s for s in operational_steps if s.agent_name == "eco_saver"), None)

            trading_agent = (
                SmartSellerAgentAS(
                    peak_risk_price_ratio=float((trading_step.parameters or {}).get("peak_risk_price_ratio", 0.90))
                )
                if trading_step is not None
                else None
            )
            storage_agent = (
                StorageMasterAgentAS(
                    price_charge_threshold=float((storage_step.parameters or {}).get("price_charge_threshold", 85.0)),
                    price_discharge_threshold=float((storage_step.parameters or {}).get("price_discharge_threshold", 115.0)),
                )
                if storage_step is not None
                else None
            )
            eco_agent = (
                EcoSaverAgentAS(
                    peak_threshold_kw=float((eco_step.parameters or {}).get("peak_threshold_kw", peak_threshold_kw)),
                    reduction_factor=float((eco_step.parameters or {}).get("reduction_factor", constraints.get("dr_reduction_factor", 0.30))),
                )
                if eco_step is not None
                else None
            )

            merged, protocol_trace = asyncio.run(
                _execute_parallel_wave_async(
                    state_json_list,
                    alfp_decisions=alfp_decisions,
                    policy_agent=policy_agent,
                    trading_agent=trading_agent,
                    storage_agent=storage_agent,
                    eco_agent=eco_agent,
                    coordinator=coordinator,
                    constraints=constraints,
                )
            )
            decisions.update(merged)
            parallel_enabled = len(operational_steps) > 1
            for step in operational_steps:
                agent_logs.append(
                    {
                        "step_id": step.step_id,
                        "agent": step.agent_name,
                        "status": "ok",
                        "wave": wave_idx,
                        "parallel": parallel_enabled,
                        "result_count": (
                            len(decisions.get("trading_recommendations", []))
                            if step.agent_name == "trading"
                            else len(decisions.get("ess_schedule", []))
                            if step.agent_name == "storage"
                            else len(decisions.get("demand_response_events", []))
                        ),
                        "a2a_protocol": "policy handoff -> agent proposal -> market coordinator merge",
                        "protocol_trace": protocol_trace[:5],
                    }
                )

        if "simulate" in layer_names:
            step = next(s for s in layer if s.agent_name == "simulate")
            simulation_approved, simulation_errors, simulation_summary = _simulate_plan_decisions(
                decisions,
                peak_threshold_kw=peak_threshold_kw,
                data_path=data_path,
                n_steps=n_steps,
                phase=phase,
                seed=seed,
                ess_capacity_kwh=ess_capacity_kwh,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
                max_peak_load_kw=max_peak_load_kw,
            )
            agent_logs.append(
                {
                    "step_id": step.step_id,
                    "agent": "simulate",
                    "status": "ok" if simulation_approved else "rejected",
                    "wave": wave_idx,
                    "parallel": False,
                    "simulation_approved": simulation_approved,
                    "simulation_errors": simulation_errors,
                    "simulation_summary": simulation_summary,
                }
            )

    decisions["agent_plan"] = {
        "plan_id": plan.plan_id,
        "objective": plan.objective,
        "revision": plan.revision,
        "llm_used": llm_used,
        "state_summary": plan.state_summary,
        "steps": [
            {
                "step_id": s.step_id,
                "agent_name": s.agent_name,
                "action": s.action,
                "reason": s.reason,
                "parameters": s.parameters,
                "depends_on": s.depends_on,
            }
            for s in plan.steps
        ],
        "execution_completed": True,
    }
    return decisions, agent_logs, simulation_approved, simulation_errors, simulation_summary


def _summarize_states(state_json_list: list[dict]) -> str:
    if not state_json_list:
        return "(상태 없음)"

    total_steps = len(state_json_list)
    peak_risk_counts: dict[str, int] = {}
    total_load_sum = 0.0
    surplus_sum = 0.0
    soc_vals: list[float] = []
    grid_price_vals: list[float] = []

    for state in state_json_list:
        cs = state.get("community_state", {}) or {}
        ms = state.get("market_state", {}) or {}
        es = state.get("ess_state", {}) or {}

        risk = cs.get("peak_risk", "LOW")
        peak_risk_counts[risk] = peak_risk_counts.get(risk, 0) + 1
        total_load_sum += float(cs.get("total_load", 0.0))
        surplus_sum += float(cs.get("surplus_energy", 0.0))
        if es.get("soc") is not None:
            soc_vals.append(float(es["soc"]))
        if ms.get("grid_price") is not None:
            grid_price_vals.append(float(ms["grid_price"]))

    avg_load = total_load_sum / total_steps if total_steps else 0.0
    avg_soc = sum(soc_vals) / len(soc_vals) if soc_vals else 0.0
    avg_price = sum(grid_price_vals) / len(grid_price_vals) if grid_price_vals else 0.0
    risk_str = ", ".join(f"{k}={v}스텝" for k, v in sorted(peak_risk_counts.items()))
    return (
        f"총 {total_steps}스텝 | 평균 부하={avg_load:.1f}kW | 총 잉여={surplus_sum:.1f}kWh "
        f"| 평균 ESS SoC={avg_soc:.1f}% | 평균 계통단가={avg_price:.0f}원/kWh | 피크 위험 분포: {risk_str}"
    )


def _summarize_alfp_decisions(alfp_decisions: dict | None) -> str:
    if not alfp_decisions:
        return "(ALFP 결정 없음)"
    n_ess = len(alfp_decisions.get("ess_schedule") or [])
    n_trade = len(alfp_decisions.get("trading_recommendations") or [])
    n_dr = len(alfp_decisions.get("demand_response_events") or [])
    violations = alfp_decisions.get("policy_violations") or []
    llm_strategy = alfp_decisions.get("llm_strategy") or {}
    alert_level = llm_strategy.get("alert_level", "N/A")
    ess_strategy = llm_strategy.get("ess_strategy", "")
    return (
        f"ESS={n_ess}건, 거래={n_trade}건, DR={n_dr}건, 정책위반={len(violations)}건, "
        f"LLM alert={alert_level}, ESS전략={str(ess_strategy)[:80] or '(없음)'}"
    )


def run_agent_plan(
    state_json_list: list[dict],
    alfp_decisions: dict | None = None,
    peak_threshold_kw: float = 500.0,
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
    use_llm: bool = True,
    max_revisions: int = 1,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    max_peak_load_kw: float | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    ALFP 결과를 기반으로 Agent Plan을 수립하고 실행한다.

    실행 순서:
    1. Plan: LLM 또는 규칙 기반 계획 생성
    2. Normalize: policy -> parallel(trading, storage, eco_saver) -> simulate 보정
    3. Execute: AgentScope Msg handoff 기반 A2A 실행
    4. Validate: 시뮬레이션 승인 실패 시 LLM 재수립
    """
    alfp_decisions = alfp_decisions or {}
    state_summary = _summarize_states(state_json_list)

    def build_initial_plan() -> tuple[AgentPlan, str]:
        if use_llm:
            try:
                if verbose:
                    print("  [AgentPlanner] LLM으로 실행 계획 수립 중...")
                llm_plan = _build_llm_plan(
                    state_json_list=state_json_list,
                    alfp_decisions=alfp_decisions,
                    peak_threshold_kw=peak_threshold_kw,
                    max_charge_kw=max_charge_kw,
                    max_discharge_kw=max_discharge_kw,
                )
                return llm_plan, "llm"
            except Exception as exc:
                if verbose:
                    print(f"  [AgentPlanner] LLM 계획 수립 실패 ({exc}) → 규칙 기반 폴백")
        return _build_rule_based_plan(state_summary, peak_threshold_kw, max_charge_kw, max_discharge_kw), "rule_based"

    plan, planning_mode = build_initial_plan()
    revised = False

    for attempt in range(max_revisions + 1):
        plan = _normalize_plan(
            plan,
            peak_threshold_kw=peak_threshold_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
        )
        if verbose:
            print(f"  [AgentPlanner] 실행 계획 ID={plan.plan_id} revision={plan.revision}")
            for step in plan.steps:
                print(f"    Step {step.step_id} [{step.agent_name}] deps={step.depends_on} {step.action}")

        decisions, logs, simulation_approved, simulation_errors, simulation_summary = _execute_plan(
            plan=plan,
            state_json_list=state_json_list,
            alfp_decisions=alfp_decisions,
            llm_used=planning_mode == "llm",
            peak_threshold_kw=peak_threshold_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            data_path=data_path,
            n_steps=n_steps,
            phase=phase,
            seed=seed,
            ess_capacity_kwh=ess_capacity_kwh,
            max_peak_load_kw=max_peak_load_kw,
            verbose=verbose,
        )
        decisions["agent_plan"]["simulation_approved"] = simulation_approved
        decisions["agent_plan"]["simulation_errors"] = simulation_errors
        decisions["agent_plan"]["simulation_summary"] = simulation_summary
        decisions["agent_plan"]["simulation_skipped"] = simulation_approved is None
        decisions["agent_plan"]["revised"] = revised
        decisions["agent_plan"]["planning_mode"] = planning_mode
        decisions["agent_plan"]["agent_logs"] = logs
        decisions["agent_plan"]["alfp_summary"] = _summarize_alfp_decisions(alfp_decisions)
        decisions["agent_plan"]["parallel_groups"] = [
            [step.agent_name for step in layer]
            for layer in _topological_layers(plan.steps)
        ]
        decisions["agent_plan"]["a2a_protocol"] = "AgentPlanner -> Policy -> Trading/Storage/EcoSaver -> MarketCoordinator -> Simulation"

        if simulation_approved is not False:
            return decisions
        if planning_mode != "llm" or attempt >= max_revisions:
            return decisions

        revised = True
        if verbose:
            print("  [AgentPlanner] 시뮬레이션 미승인 → LLM 계획 재수립")
        try:
            plan = _revise_plan_with_llm(
                original_plan=plan,
                simulation_errors=simulation_errors,
                peak_threshold_kw=peak_threshold_kw,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
        except Exception as exc:
            if verbose:
                print(f"  [AgentPlanner] 계획 재수립 실패 ({exc})")
            return decisions

    return decisions
