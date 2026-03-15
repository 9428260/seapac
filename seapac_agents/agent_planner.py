"""
Step 3-P — LLM 기반 에이전트 실행 계획 수립 및 실행 (Agent Plan)

ALFP decisions를 입력으로 받아 LLM이 전력거래·ESS·DR 실행 계획을 수립하고,
EcoSaver / Storage / Policy 에이전트를 계획에 따라 순차 실행합니다.

Plan-and-Execute 패턴:
  1. Plan    : LLM이 상태(state_json_list)와 ALFP decisions를 분석하여
               에이전트 실행 계획(AgentPlan)을 JSON으로 수립
  2. Execute : 계획 단계별로 Policy / StorageMaster / EcoSaver 에이전트 실행
               → ess_schedule, trading_recommendations, demand_response_events 누적
  3. Simulate: seapac_agents.execution.run_execution() 으로 시뮬레이션 검증
  4. Revise  : 시뮬레이션 미통과 시 LLM이 계획을 재수립 (최대 max_revisions 회)

사용 예시 (파이프라인):
    from seapac_agents.agent_planner import run_agent_plan

    decisions = run_agent_plan(
        state_json_list=state_json_list,
        alfp_decisions=decisions,          # 기존 AgentScope decisions (None 가능)
        peak_threshold_kw=500.0,
        max_charge_kw=50.0,
        max_discharge_kw=50.0,
        use_llm=True,
        max_revisions=1,
    )
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ─────────────────────────────────────────────────────────────────
# 데이터 클래스
# ─────────────────────────────────────────────────────────────────

@dataclass
class AgentPlanStep:
    """에이전트 실행 계획의 단일 스텝."""
    step_id: int
    agent_name: str        # "policy" | "storage" | "eco_saver" | "simulate"
    action: str            # 에이전트가 수행할 작업 설명
    parameters: dict       # 에이전트에 전달할 파라미터 (overrides)
    depends_on: list[int]  # 선행 완료 필요 step_id 목록
    reason: str            # LLM이 이 단계를 포함한 이유


@dataclass
class AgentPlan:
    """LLM이 수립한 에이전트 실행 계획."""
    plan_id: str
    created_at: str
    objective: str              # LLM의 목표 선언
    steps: list[AgentPlanStep]
    state_summary: str          # 계획 수립 당시 상태 요약
    revision: int = 0           # 재수립 횟수


@dataclass
class AgentPlanResult:
    """에이전트 계획 실행 결과."""
    plan: AgentPlan
    decisions: dict             # 실행된 최종 decisions
    simulation_approved: bool
    simulation_errors: list[str]
    agent_logs: list[dict]      # 단계별 실행 로그
    revised: bool = False       # 재수립 여부


# ─────────────────────────────────────────────────────────────────
# 규칙 기반 폴백 계획 생성
# ─────────────────────────────────────────────────────────────────

def _build_rule_based_plan(
    state_summary: str,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """LLM 미사용 시 기본 실행 계획 반환 (Policy → Storage → EcoSaver → Simulate)."""
    steps = [
        AgentPlanStep(
            step_id=1,
            agent_name="policy",
            action="제약 조건 설정 및 검증 준비",
            parameters={
                "max_charge_kw": max_charge_kw,
                "max_discharge_kw": max_discharge_kw,
                "ess_soc_min_pct": 10.0,
                "ess_soc_max_pct": 95.0,
                "min_trade_kw": 0.2,
            },
            depends_on=[],
            reason="모든 에이전트 제안에 앞서 물리적·운영적 제약 조건을 먼저 확정합니다.",
        ),
        AgentPlanStep(
            step_id=2,
            agent_name="storage",
            action="ESS 충방전 스케줄 생성 (TOU + 피크 억제)",
            parameters={
                "price_charge_threshold": 85.0,
                "price_discharge_threshold": 115.0,
            },
            depends_on=[1],
            reason="Policy 제약 조건 확정 후 ESS 스케줄을 수립합니다.",
        ),
        AgentPlanStep(
            step_id=3,
            agent_name="eco_saver",
            action="수요반응(DR) 이벤트 생성",
            parameters={"peak_threshold_kw": peak_threshold_kw},
            depends_on=[1],
            reason="Policy 제약 확정 후 DR 이벤트를 독립적으로 생성합니다.",
        ),
        AgentPlanStep(
            step_id=4,
            agent_name="simulate",
            action="시뮬레이션 검증 및 승인",
            parameters={},
            depends_on=[2, 3],
            reason="ESS 스케줄 + DR 결합 후 시뮬레이션으로 최종 승인 여부를 판단합니다.",
        ),
    ]
    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective="전력거래 최적화: Policy 제약 → ESS 스케줄 → DR 이벤트 → 시뮬레이션 검증",
        steps=steps,
        state_summary=state_summary,
    )


# ─────────────────────────────────────────────────────────────────
# LLM 기반 계획 수립
# ─────────────────────────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """당신은 에너지 커뮤니티 전력거래 오케스트레이터입니다.

사용 가능한 에이전트:
- "policy"    : 제약 조건 설정 및 검증 (항상 먼저 실행)
- "storage"   : ESS 충방전 스케줄 수립 (TOU + 피크 억제 + 잉여 PV 흡수)
- "eco_saver" : 수요반응(DR) 이벤트 생성 (피크 초과 시)
- "simulate"  : 결정 사항을 시뮬레이션으로 검증 (항상 마지막)

규칙:
1. "policy" 는 반드시 첫 번째 스텝.
2. "simulate" 는 반드시 마지막 스텝.
3. "storage"와 "eco_saver"는 "policy" 완료 후 실행 (depends_on에 policy step_id 포함).
4. "storage"와 "eco_saver"는 병렬 실행 가능 (서로 depends_on 불필요).
5. 피크 위험이 HIGH인 경우 storage의 discharge를 우선, eco_saver의 DR도 병행.
6. 잉여 PV가 있으면 storage에 charge 파라미터 우선 반영.

JSON 형식으로만 출력하세요 (마크다운 코드 블록 없이):
{
  "objective": "한 문장으로 이번 계획의 목표",
  "steps": [
    {
      "step_id": 1,
      "agent_name": "policy|storage|eco_saver|simulate",
      "action": "이 에이전트가 수행할 작업 설명",
      "parameters": {},
      "depends_on": [],
      "reason": "이 단계를 포함한 근거"
    }
  ]
}"""


def _build_llm_plan(
    state_json_list: list[dict],
    alfp_decisions: dict,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """LLM이 상태와 ALFP decisions를 분석하여 에이전트 실행 계획을 수립."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from alfp.llm import get_llm_forced

    # ── 상태 요약 생성 ──────────────────────────────────────────────
    state_summary = _summarize_states(state_json_list)
    alfp_summary = _summarize_alfp_decisions(alfp_decisions)

    user_msg = f"""현재 에너지 커뮤니티 상태 요약:
{state_summary}

ALFP(Load Forecast) 기반 의사결정 요약:
{alfp_summary}

설정 파라미터:
- 피크 임계값: {peak_threshold_kw} kW
- ESS 최대 충전: {max_charge_kw} kW
- ESS 최대 방전: {max_discharge_kw} kW

위 정보를 바탕으로 전력거래 실행을 위한 에이전트 계획을 JSON으로 수립해주세요.
Policy → Storage/EcoSaver → Simulate 순서를 준수하세요."""

    llm = get_llm_forced(temperature=0.1)
    resp = llm.invoke([SystemMessage(content=_PLAN_SYSTEM_PROMPT), HumanMessage(content=user_msg)])
    raw_text = resp.content if hasattr(resp, "content") else str(resp)

    # ── JSON 파싱 ───────────────────────────────────────────────────
    plan_data: dict = {}
    try:
        plan_data = json.loads(raw_text)
    except json.JSONDecodeError:
        # 코드 블록 내 JSON 추출 시도
        import re
        m = re.search(r"```(?:json)?\s*(\{[\s\S]+\})\s*```", raw_text)
        if m:
            try:
                plan_data = json.loads(m.group(1))
            except Exception:
                pass

    if not plan_data or "steps" not in plan_data:
        raise ValueError(f"LLM 계획 파싱 실패: {raw_text[:300]}")

    steps = [
        AgentPlanStep(
            step_id=int(s.get("step_id", i + 1)),
            agent_name=str(s.get("agent_name", "")),
            action=str(s.get("action", "")),
            parameters=dict(s.get("parameters") or {}),
            depends_on=list(s.get("depends_on") or []),
            reason=str(s.get("reason", "")),
        )
        for i, s in enumerate(plan_data.get("steps", []))
    ]

    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective=str(plan_data.get("objective", "")),
        steps=steps,
        state_summary=state_summary,
    )


# ─────────────────────────────────────────────────────────────────
# LLM 재수립 (시뮬레이션 실패 시)
# ─────────────────────────────────────────────────────────────────

_REVISE_SYSTEM_PROMPT = """당신은 에너지 커뮤니티 전력거래 오케스트레이터입니다.
이전 계획이 시뮬레이션 검증을 통과하지 못했습니다. 실패 원인을 분석하고 개선된 계획을 재수립하세요.

에이전트: policy, storage, eco_saver, simulate (규칙 동일)
JSON 형식으로만 출력 (마크다운 코드 블록 없이)."""


def _revise_plan_with_llm(
    original_plan: AgentPlan,
    simulation_errors: list[str],
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
) -> AgentPlan:
    """시뮬레이션 실패 시 LLM이 계획을 재수립."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from alfp.llm import get_llm_forced

    prev_steps_str = json.dumps(
        [
            {"step_id": s.step_id, "agent_name": s.agent_name, "action": s.action,
             "parameters": s.parameters, "reason": s.reason}
            for s in original_plan.steps
        ],
        ensure_ascii=False, indent=2
    )

    user_msg = f"""이전 계획:
{prev_steps_str}

시뮬레이션 실패 원인:
{chr(10).join(f'- {e}' for e in simulation_errors)}

상태 요약:
{original_plan.state_summary}

설정: 피크 임계값={peak_threshold_kw}kW, 충전={max_charge_kw}kW, 방전={max_discharge_kw}kW

실패 원인을 해결하는 개선된 에이전트 계획을 JSON으로 재수립해주세요."""

    llm = get_llm_forced(temperature=0.2)
    resp = llm.invoke([SystemMessage(content=_REVISE_SYSTEM_PROMPT), HumanMessage(content=user_msg)])
    raw_text = resp.content if hasattr(resp, "content") else str(resp)

    plan_data: dict = {}
    try:
        plan_data = json.loads(raw_text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"```(?:json)?\s*(\{[\s\S]+\})\s*```", raw_text)
        if m:
            try:
                plan_data = json.loads(m.group(1))
            except Exception:
                pass

    if not plan_data or "steps" not in plan_data:
        # 재수립 실패 시 원래 계획 반환
        return original_plan

    steps = [
        AgentPlanStep(
            step_id=int(s.get("step_id", i + 1)),
            agent_name=str(s.get("agent_name", "")),
            action=str(s.get("action", "")),
            parameters=dict(s.get("parameters") or {}),
            depends_on=list(s.get("depends_on") or []),
            reason=str(s.get("reason", "")),
        )
        for i, s in enumerate(plan_data.get("steps", []))
    ]

    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective=str(plan_data.get("objective", original_plan.objective)),
        steps=steps,
        state_summary=original_plan.state_summary,
        revision=original_plan.revision + 1,
    )


# ─────────────────────────────────────────────────────────────────
# 계획 실행 (Execute)
# ─────────────────────────────────────────────────────────────────

def _execute_plan(
    plan: AgentPlan,
    state_json_list: list[dict],
    alfp_decisions: dict,
    peak_threshold_kw: float,
    max_charge_kw: float,
    max_discharge_kw: float,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    verbose: bool = False,
) -> tuple[dict, bool, list[str], list[dict]]:
    """
    AgentPlan의 각 스텝을 순서대로 실행하고 decisions를 조립합니다.

    Returns:
        (decisions, simulation_approved, simulation_errors, agent_logs)
    """
    import asyncio
    from agentscope.message import Msg
    from seapac_agents.decision import (
        _init_agentscope,
        PolicyAgentAS,
        StorageMasterAgentAS,
        EcoSaverAgentAS,
    )

    _init_agentscope()

    # ── 에이전트 파라미터 기본값 (계획 파라미터로 오버라이드 가능) ──
    default_policy_params: dict[str, Any] = {
        "max_charge_kw": max_charge_kw,
        "max_discharge_kw": max_discharge_kw,
    }
    default_storage_params: dict[str, Any] = {}
    default_eco_params: dict[str, Any] = {"peak_threshold_kw": peak_threshold_kw}

    # ── step 결과 누적 ──────────────────────────────────────────────
    ess_schedule: list[dict] = []
    trading_recommendations: list[dict] = alfp_decisions.get("trading_recommendations", [])
    demand_response_events: list[dict] = []
    policy_violations: list[str] = []
    agent_logs: list[dict] = []
    completed_steps: set[int] = set()

    # ── 정렬된 실행 순서 결정 (depends_on 위상 정렬) ─────────────────
    ordered_steps = _topological_sort(plan.steps)

    for step in ordered_steps:
        if step.agent_name == "simulate":
            # simulate는 별도로 처리
            completed_steps.add(step.step_id)
            continue

        # depends_on 대기 (순차 실행이므로 이미 완료됨)
        if not all(dep in completed_steps for dep in step.depends_on):
            agent_logs.append({
                "step_id": step.step_id,
                "agent": step.agent_name,
                "status": "skipped",
                "reason": f"의존 스텝 미완료: {step.depends_on}",
            })
            continue

        if verbose:
            print(f"    [AgentPlan Step {step.step_id}] {step.agent_name}: {step.action}")

        if step.agent_name == "policy":
            params = {**default_policy_params, **step.parameters}
            policy_agent = PolicyAgentAS(
                max_charge_kw=float(params.get("max_charge_kw", max_charge_kw)),
                max_discharge_kw=float(params.get("max_discharge_kw", max_discharge_kw)),
                ess_soc_min_pct=float(params.get("ess_soc_min_pct", 10.0)),
                ess_soc_max_pct=float(params.get("ess_soc_max_pct", 95.0)),
                min_trade_kw=float(params.get("min_trade_kw", 0.2)),
                dr_reduction_factor=float(params.get("dr_reduction_factor", 0.30)),
            )
            agent_logs.append({
                "step_id": step.step_id,
                "agent": "policy",
                "status": "ok",
                "constraints": params,
            })
            completed_steps.add(step.step_id)

        elif step.agent_name == "storage":
            params = {**default_storage_params, **step.parameters}
            storage_agent = StorageMasterAgentAS(
                price_charge_threshold=float(params.get("price_charge_threshold", 85.0)),
                price_discharge_threshold=float(params.get("price_discharge_threshold", 115.0)),
            )
            # 각 state에 대해 ESS 제안 생성
            for state in state_json_list:
                msg = Msg(
                    name="state",
                    content="state",
                    role="user",
                    metadata={"state": state},
                )
                storage_reply = asyncio.run(storage_agent.reply(msg))
                proposal = (storage_reply.metadata or {}).get("proposal", {})
                if proposal and proposal.get("action") != "idle":
                    # Policy 검증 (policy_agent가 이미 생성된 경우)
                    validated, errs = policy_agent.validate_ess(proposal)
                    policy_violations.extend(errs)
                    ess_schedule.append({
                        "timestamp": state.get("time", ""),
                        "action": validated.get("action", "idle"),
                        "power_kw": float(validated.get("power_kw", 0.0)),
                        "soc_kwh": float(
                            (state.get("ess_state", {}) or {}).get("soc", 50.0)
                        ) / 100.0 * float(
                            (state.get("ess_state", {}) or {}).get("capacity", 200.0)
                        ),
                        "net_load_kw": float(
                            (state.get("community_state", {}) or {}).get("total_load", 0.0)
                        ),
                        "reason": validated.get("reason", ""),
                    })
            agent_logs.append({
                "step_id": step.step_id,
                "agent": "storage",
                "status": "ok",
                "ess_schedule_count": len(ess_schedule),
            })
            completed_steps.add(step.step_id)

        elif step.agent_name == "eco_saver":
            params = {**default_eco_params, **step.parameters}
            eco_agent = EcoSaverAgentAS(
                peak_threshold_kw=float(params.get("peak_threshold_kw", peak_threshold_kw)),
                reduction_factor=float(params.get("reduction_factor", 0.30)),
            )
            for state in state_json_list:
                msg = Msg(
                    name="state",
                    content="state",
                    role="user",
                    metadata={"state": state},
                )
                eco_reply = asyncio.run(eco_agent.reply(msg))
                dr_events = ((eco_reply.metadata or {}).get("proposal", {}) or {}).get("dr_events", [])
                # Policy DR 검증
                for dr in dr_events:
                    validated_dr, errs = policy_agent.validate_dr(dr)
                    policy_violations.extend(errs)
                    if validated_dr:
                        demand_response_events.append(validated_dr)
            agent_logs.append({
                "step_id": step.step_id,
                "agent": "eco_saver",
                "status": "ok",
                "dr_events_count": len(demand_response_events),
            })
            completed_steps.add(step.step_id)

        else:
            agent_logs.append({
                "step_id": step.step_id,
                "agent": step.agent_name,
                "status": "unknown_agent",
            })
            completed_steps.add(step.step_id)

    # ── decisions 조립 ──────────────────────────────────────────────
    decisions: dict[str, Any] = {
        **alfp_decisions,
        "ess_schedule": ess_schedule,
        "trading_recommendations": trading_recommendations,
        "demand_response_events": demand_response_events,
        "policy_violations": policy_violations,
        "agent_plan": {
            "plan_id": plan.plan_id,
            "objective": plan.objective,
            "revision": plan.revision,
            "steps": [
                {
                    "step_id": s.step_id,
                    "agent_name": s.agent_name,
                    "action": s.action,
                    "reason": s.reason,
                }
                for s in plan.steps
            ],
        },
    }

    # ── Simulate 스텝 실행 ──────────────────────────────────────────
    simulation_approved = False
    simulation_errors: list[str] = []

    simulate_step = next((s for s in plan.steps if s.agent_name == "simulate"), None)
    if simulate_step and all(dep in completed_steps for dep in simulate_step.depends_on):
        if verbose:
            print(f"    [AgentPlan Simulate] 시뮬레이션 검증 실행...")
        try:
            from seapac_agents.execution import run_execution
            result = run_execution(
                decisions,
                data_path=data_path,
                n_steps=n_steps,
                phase=phase,
                seed=seed,
                ess_capacity_kwh=ess_capacity_kwh,
                ess_peak_threshold_kw=peak_threshold_kw,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
            simulation_approved = result.approved
            simulation_errors = result.validation_errors + result.simulation_approval_errors
            decisions["simulation_summary"] = result.summary
            agent_logs.append({
                "step_id": simulate_step.step_id,
                "agent": "simulate",
                "status": "ok",
                "approved": simulation_approved,
                "errors": simulation_errors,
            })
        except Exception as e:
            simulation_errors = [str(e)]
            agent_logs.append({
                "step_id": simulate_step.step_id,
                "agent": "simulate",
                "status": "error",
                "errors": simulation_errors,
            })

    return decisions, simulation_approved, simulation_errors, agent_logs


# ─────────────────────────────────────────────────────────────────
# 위상 정렬 (topological sort)
# ─────────────────────────────────────────────────────────────────

def _topological_sort(steps: list[AgentPlanStep]) -> list[AgentPlanStep]:
    """depends_on 관계에 따라 스텝을 순서 정렬 (simulate는 마지막)."""
    id_to_step = {s.step_id: s for s in steps}
    visited: set[int] = set()
    ordered: list[AgentPlanStep] = []

    def visit(step: AgentPlanStep) -> None:
        if step.step_id in visited:
            return
        for dep_id in step.depends_on:
            if dep_id in id_to_step:
                visit(id_to_step[dep_id])
        visited.add(step.step_id)
        ordered.append(step)

    # simulate는 항상 마지막
    non_simulate = [s for s in steps if s.agent_name != "simulate"]
    simulate = [s for s in steps if s.agent_name == "simulate"]
    for s in non_simulate:
        visit(s)
    ordered.extend(simulate)
    return ordered


# ─────────────────────────────────────────────────────────────────
# 상태 요약 헬퍼
# ─────────────────────────────────────────────────────────────────

def _summarize_states(state_json_list: list[dict]) -> str:
    """state_json_list를 LLM에 전달할 텍스트 요약으로 변환."""
    if not state_json_list:
        return "(상태 없음)"

    total_steps = len(state_json_list)
    peak_risk_counts: dict[str, int] = {}
    total_load_sum = 0.0
    surplus_sum = 0.0
    soc_vals: list[float] = []
    grid_price_vals: list[float] = []

    for s in state_json_list:
        cs = s.get("community_state", {}) or {}
        ms = s.get("market_state", {}) or {}
        es = s.get("ess_state", {}) or {}

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
        f"| 평균 ESS SoC={avg_soc:.1f}% | 평균 계통단가={avg_price:.0f}원/kWh "
        f"| 피크 위험 분포: {risk_str}"
    )


def _summarize_alfp_decisions(alfp_decisions: dict | None) -> str:
    """ALFP decisions를 LLM에 전달할 텍스트 요약으로 변환."""
    if not alfp_decisions:
        return "(ALFP 결정 없음)"
    n_ess = len(alfp_decisions.get("ess_schedule") or [])
    n_trade = len(alfp_decisions.get("trading_recommendations") or [])
    n_dr = len(alfp_decisions.get("demand_response_events") or [])
    violations = alfp_decisions.get("policy_violations") or []
    return (
        f"ESS 스케줄={n_ess}건, 거래권고={n_trade}건, DR={n_dr}건, "
        f"정책위반={len(violations)}건"
    )


# ─────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────

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
    verbose: bool = False,
) -> dict:
    """
    LLM 기반 에이전트 계획(AgentPlan)을 수립하고 실행하여 decisions를 반환합니다.

    Args:
        state_json_list   : Step 2 State Translator 출력 리스트
        alfp_decisions    : 기존 AgentScope/CDA decisions (없으면 빈 dict 사용)
        peak_threshold_kw : 피크 임계값 (kW)
        max_charge_kw     : ESS 최대 충전 전력 (kW)
        max_discharge_kw  : ESS 최대 방전 전력 (kW)
        use_llm           : True=LLM 계획 수립, False=규칙 기반 기본 계획
        max_revisions     : 시뮬레이션 실패 시 재수립 최대 횟수
        data_path         : Mesa 시뮬레이션 데이터 경로
        n_steps           : 시뮬레이션 스텝 수
        phase             : Mesa 시뮬레이션 단계
        seed              : 랜덤 시드
        ess_capacity_kwh  : ESS 배터리 용량 (kWh)
        verbose           : 상세 출력 여부

    Returns:
        decisions dict (ess_schedule, trading_recommendations, demand_response_events,
                        agent_plan, simulation_summary 포함)
    """
    alfp_decisions = alfp_decisions or {}

    state_summary = _summarize_states(state_json_list)

    # ── 1. Plan 수립 ─────────────────────────────────────────────────
    plan: AgentPlan
    if use_llm:
        try:
            if verbose:
                print("  [AgentPlanner] LLM으로 실행 계획 수립 중...")
            plan = _build_llm_plan(
                state_json_list=state_json_list,
                alfp_decisions=alfp_decisions,
                peak_threshold_kw=peak_threshold_kw,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
            if verbose:
                print(f"  [AgentPlanner] 계획 수립 완료 (ID={plan.plan_id}): {plan.objective}")
                for s in plan.steps:
                    print(f"    Step {s.step_id} [{s.agent_name}] {s.action}")
        except Exception as e:
            if verbose:
                print(f"  [AgentPlanner] LLM 계획 수립 실패 ({e}) → 규칙 기반 폴백")
            plan = _build_rule_based_plan(
                state_summary, peak_threshold_kw, max_charge_kw, max_discharge_kw
            )
    else:
        plan = _build_rule_based_plan(
            state_summary, peak_threshold_kw, max_charge_kw, max_discharge_kw
        )
        if verbose:
            print(f"  [AgentPlanner] 규칙 기반 계획 사용 (ID={plan.plan_id})")

    # ── 2. Execute → Simulate ─────────────────────────────────────────
    decisions, sim_approved, sim_errors, logs = _execute_plan(
        plan=plan,
        state_json_list=state_json_list,
        alfp_decisions=alfp_decisions,
        peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        data_path=data_path,
        n_steps=n_steps,
        phase=phase,
        seed=seed,
        ess_capacity_kwh=ess_capacity_kwh,
        verbose=verbose,
    )

    if verbose:
        status = "승인" if sim_approved else "미승인"
        print(f"  [AgentPlanner] 시뮬레이션 결과: {status} (오류 {len(sim_errors)}건)")

    # ── 3. Revise Loop ────────────────────────────────────────────────
    revised = False
    for _rev in range(max_revisions):
        if sim_approved or not sim_errors:
            break
        if not use_llm:
            break

        if verbose:
            print(f"  [AgentPlanner] 계획 재수립 시도 {_rev + 1}/{max_revisions}...")

        try:
            revised_plan = _revise_plan_with_llm(
                original_plan=plan,
                simulation_errors=sim_errors,
                peak_threshold_kw=peak_threshold_kw,
                max_charge_kw=max_charge_kw,
                max_discharge_kw=max_discharge_kw,
            )
        except Exception as e:
            if verbose:
                print(f"  [AgentPlanner] 재수립 실패 ({e}), 중단")
            break

        decisions, sim_approved, sim_errors, logs = _execute_plan(
            plan=revised_plan,
            state_json_list=state_json_list,
            alfp_decisions=alfp_decisions,
            peak_threshold_kw=peak_threshold_kw,
            max_charge_kw=max_charge_kw,
            max_discharge_kw=max_discharge_kw,
            data_path=data_path,
            n_steps=n_steps,
            phase=phase,
            seed=seed,
            ess_capacity_kwh=ess_capacity_kwh,
            verbose=verbose,
        )
        plan = revised_plan
        revised = True

        if verbose:
            status = "승인" if sim_approved else "미승인"
            print(f"  [AgentPlanner] 재수립 후 시뮬레이션: {status}")

    # ── 4. 실행 메타데이터 추가 ──────────────────────────────────────
    decisions["agent_plan"]["simulation_approved"] = sim_approved
    decisions["agent_plan"]["simulation_errors"] = sim_errors
    decisions["agent_plan"]["revised"] = revised
    decisions["agent_plan"]["agent_logs"] = logs

    return decisions
