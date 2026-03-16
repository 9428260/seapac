"""
Step 3-P — LLM 기반 에이전트 실행 계획 수립 및 실행 (Agent Plan)

ALFP forecast 결과를 입력으로 받아 LLM 또는 규칙 기반 planner가
전력거래 / Storage / Policy 실행 계획을 수립하고 에이전트를 순차 실행합니다.

Plan-and-Execute 패턴:
  1. Plan    : LLM이 상태(state_json_list)와 ALFP decisions를 분석하여
               에이전트 실행 계획(AgentPlan)을 JSON으로 수립
  2. Execute : 계획 단계별로 Policy / SmartSeller / StorageMaster 에이전트 실행
               → trading_recommendations, ess_schedule, policy_violations 누적

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
    agent_logs: list[dict]      # 단계별 실행 로그
    revised: bool = False       # 현재 버전에서는 항상 False


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
            agent_name="trading",
            action="전력거래 권고 생성 (잉여 전력 판매 전략)",
            parameters={
                "peak_risk_price_ratio": 0.90,
            },
            depends_on=[1],
            reason="Policy 제약 조건 확정 후 잉여 전력 판매 전략을 수립합니다.",
        ),
        AgentPlanStep(
            step_id=3,
            agent_name="storage",
            action="ESS 충방전 스케줄 생성 (TOU + 피크 억제)",
            parameters={
                "price_charge_threshold": 85.0,
                "price_discharge_threshold": 115.0,
            },
            depends_on=[1],
            reason="Policy 제약 조건 확정 후 ESS 스케줄을 수립합니다.",
        ),
    ]
    return AgentPlan(
        plan_id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        objective="전력거래 및 Storage 운영 최적화: Policy 제약 → Trading / Storage 실행",
        steps=steps,
        state_summary=state_summary,
    )


# ─────────────────────────────────────────────────────────────────
# LLM 기반 계획 수립
# ─────────────────────────────────────────────────────────────────

_PLAN_SYSTEM_PROMPT = """당신은 에너지 커뮤니티 전력거래 오케스트레이터입니다.

사용 가능한 에이전트:
- "policy"    : 제약 조건 설정 및 검증 (항상 먼저 실행)
- "trading"   : 잉여 전력 판매/거래 권고 생성
- "storage"   : ESS 충방전 스케줄 수립 (TOU + 피크 억제 + 잉여 PV 흡수)

규칙:
1. "policy" 는 반드시 첫 번째 스텝.
2. "trading"과 "storage"는 "policy" 완료 후 실행 (depends_on에 policy step_id 포함).
3. "trading"과 "storage"는 병렬 실행 가능 (서로 depends_on 불필요).
4. 피크 위험이 HIGH인 경우 storage의 discharge를 우선 고려한다.
5. 잉여 PV가 있으면 trading 판매 또는 storage charge 중 더 적절한 전략을 선택한다.

JSON 형식으로만 출력하세요 (마크다운 코드 블록 없이):
{
  "objective": "한 문장으로 이번 계획의 목표",
  "steps": [
    {
      "step_id": 1,
      "agent_name": "policy|trading|storage",
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
Policy → Trading/Storage 순서를 준수하세요."""

    llm = get_llm_forced(temperature=0.1, stage="agent_plan")
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
정책 위반 또는 실행 상의 문제를 줄이기 위한 개선된 계획을 재수립하세요.

에이전트: policy, trading, storage
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

    llm = get_llm_forced(temperature=0.2, stage="agent_plan")
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
    llm_used: bool = False,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    verbose: bool = False,
) -> tuple[dict, list[dict]]:
    """
    AgentPlan의 각 스텝을 순서대로 실행하고 decisions를 조립합니다.

    Returns:
        (decisions, agent_logs)
    """
    import asyncio
    from agentscope.message import Msg
    from seapac_agents.decision import (
        _init_agentscope,
        PolicyAgentAS,
        SmartSellerAgentAS,
        StorageMasterAgentAS,
    )

    _init_agentscope()

    # ── 에이전트 파라미터 기본값 (계획 파라미터로 오버라이드 가능) ──
    default_policy_params: dict[str, Any] = {
        "max_charge_kw": max_charge_kw,
        "max_discharge_kw": max_discharge_kw,
    }
    default_storage_params: dict[str, Any] = {}
    default_trading_params: dict[str, Any] = {"peak_risk_price_ratio": 0.90}

    # ── step 결과 누적 ──────────────────────────────────────────────
    ess_schedule: list[dict] = []
    trading_recommendations: list[dict] = []
    policy_violations: list[str] = []
    agent_logs: list[dict] = []
    completed_steps: set[int] = set()

    # ── 정렬된 실행 순서 결정 (depends_on 위상 정렬) ─────────────────
    ordered_steps = _topological_sort(plan.steps)

    for step in ordered_steps:
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

        elif step.agent_name == "trading":
            params = {**default_trading_params, **step.parameters}
            trading_agent = SmartSellerAgentAS(
                peak_risk_price_ratio=float(params.get("peak_risk_price_ratio", 0.90)),
            )
            for state in state_json_list:
                msg = Msg(
                    name="state",
                    content="state",
                    role="user",
                    metadata={"state": state},
                )
                trading_reply = asyncio.run(trading_agent.reply(msg))
                proposal = (trading_reply.metadata or {}).get("proposal", {})
                validated_trade, errs = policy_agent.validate_trade(proposal)
                policy_violations.extend(errs)
                if validated_trade and validated_trade.get("action") in ("sell_p2p", "sell_grid"):
                    trading_recommendations.append({
                        "timestamp": state.get("time", ""),
                        "surplus_kw": float(validated_trade.get("bid_quantity_kw", 0.0)),
                        "bid_price": float(validated_trade.get("bid_price", 0.0)),
                        "action": validated_trade.get("action", "sell_p2p"),
                        "reason": validated_trade.get("reason", ""),
                    })
            agent_logs.append({
                "step_id": step.step_id,
                "agent": "trading",
                "status": "ok",
                "trading_count": len(trading_recommendations),
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
        "demand_response_events": [],
        "policy_violations": policy_violations,
        "ess_summary": {
            "charge_steps": sum(1 for row in ess_schedule if row.get("action") == "charge"),
            "discharge_steps": sum(1 for row in ess_schedule if row.get("action") == "discharge"),
            "idle_steps": sum(1 for row in ess_schedule if row.get("action") == "idle"),
        },
        "trading_summary": {
            "total_surplus_events": len(trading_recommendations),
            "total_surplus_kw": round(sum(float(r.get("surplus_kw", 0)) for r in trading_recommendations), 2),
        },
        "dr_summary": {"dr_event_count": 0},
        "agent_plan": {
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
        },
    }
    return decisions, agent_logs


# ─────────────────────────────────────────────────────────────────
# 위상 정렬 (topological sort)
# ─────────────────────────────────────────────────────────────────

def _topological_sort(steps: list[AgentPlanStep]) -> list[AgentPlanStep]:
    """depends_on 관계에 따라 스텝을 순서 정렬."""
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

    for s in steps:
        visit(s)
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
                        agent_plan 포함)
    """
    alfp_decisions = alfp_decisions or {}

    state_summary = _summarize_states(state_json_list)

    # ── 1. Plan 수립 ─────────────────────────────────────────────────
    plan: AgentPlan
    planning_mode = "rule_based"
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
            planning_mode = "llm"
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

    # ── 2. Execute ────────────────────────────────────────────────────
    decisions, logs = _execute_plan(
        plan=plan,
        state_json_list=state_json_list,
        alfp_decisions=alfp_decisions,
        llm_used=planning_mode == "llm",
        peak_threshold_kw=peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        verbose=verbose,
    )

    # ── 3. 실행 메타데이터 추가 ──────────────────────────────────────
    decisions["agent_plan"]["simulation_approved"] = None
    decisions["agent_plan"]["simulation_errors"] = []
    decisions["agent_plan"]["simulation_skipped"] = True
    decisions["agent_plan"]["revised"] = False
    decisions["agent_plan"]["planning_mode"] = planning_mode
    decisions["agent_plan"]["agent_logs"] = logs

    return decisions
