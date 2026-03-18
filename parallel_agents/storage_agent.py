"""
Storage Management Agent (PRD §5.3 — seapac_parallel_agents_prd.md).

Controls distributed energy resources: PV Operation Manager + ESS Operation Manager.
Has veto authority on device feasibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PVManagerOutput:
    """PV Operation Manager outputs (PRD §5.3)."""
    pv_surplus_availability_kw: float = 0.0
    self_consumption_allocation_kw: float = 0.0
    export_limit_kw: float = 999.0


@dataclass
class ESSManagerOutput:
    """ESS Operation Manager outputs (PRD §5.3)."""
    ess_charge_schedule: list[dict] = field(default_factory=list)
    ess_discharge_schedule: list[dict] = field(default_factory=list)
    soc_projection: list[float] = field(default_factory=list)
    degradation_estimate: float = 0.0
    feasible: bool = True
    modified_actions: list[dict] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    approved_actions_detail: list[dict] = field(default_factory=list)


@dataclass
class StorageAgentOutput:
    """Storage Management Agent combined output."""
    pv: PVManagerOutput = field(default_factory=PVManagerOutput)
    ess: ESSManagerOutput = field(default_factory=ESSManagerOutput)
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    modified_actions: list[dict] = field(default_factory=list)
    approved_actions_detail: list[dict] = field(default_factory=list)
    llm_review: dict = field(default_factory=dict)


def _pv_operation_manager(site_state: dict, export_limit_kw: float = 999.0) -> PVManagerOutput:
    """Forecast PV production, self-consumption priority, exportable surplus, grid export limits."""
    load_kw = float(site_state.get("load_kw", 0) or 0)
    pv_kw = float(site_state.get("pv_kw", 0) or 0)
    self_cons = min(load_kw, pv_kw)
    surplus = max(0, pv_kw - load_kw)
    export = min(surplus, export_limit_kw)
    return PVManagerOutput(
        pv_surplus_availability_kw=round(surplus, 2),
        self_consumption_allocation_kw=round(self_cons, 2),
        export_limit_kw=export_limit_kw,
    )


def _ess_operation_manager(
    site_state: dict,
    candidate_actions: list[dict],
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
    soc_min_pct: float = 10.0,
    soc_max_pct: float = 95.0,
    capacity_kwh: float = 200.0,
) -> ESSManagerOutput:
    """
    Manage SoC, enforce charge/discharge limits, estimate degradation, optimize peak shaving.
    Returns feasible schedule and any modified/rejected ESS actions.
    """
    out = ESSManagerOutput()
    es = site_state.get("ess_state") or {}
    soc_pct = es.get("soc")
    if soc_pct is None:
        soc_pct = 50.0
    soc_pct = float(soc_pct)
    cap = float(site_state.get("ess_capacity_kwh") or es.get("capacity") or capacity_kwh)

    ess_actions = [a for a in candidate_actions if a.get("type") == "ess"]
    soc_now = soc_pct
    deg = 0.0

    for action in ess_actions:
        aid = action.get("action_id", "")
        subtype = action.get("subtype", "idle")
        power = float(action.get("power_kw") or 0)
        modified = dict(action)

        if subtype == "charge":
            if soc_now >= soc_max_pct:
                out.rejected_actions.append(aid)
                out.feasible = False
                continue
            power = min(power, max_charge_kw)
            soc_delta = power * 0.25 / cap * 100 if cap > 0 else 0
            soc_now = min(soc_max_pct, soc_now + soc_delta)
            deg += 0.0001 * power  # simplistic degradation cost proxy
            modified["power_kw"] = round(power, 2)
            out.ess_charge_schedule.append(modified)
            out.approved_actions_detail.append(modified)
        elif subtype == "discharge":
            if soc_now <= soc_min_pct:
                out.rejected_actions.append(aid)
                out.feasible = False
                continue
            power = min(power, max_discharge_kw)
            soc_delta = power * 0.25 / cap * 100 if cap > 0 else 0
            soc_now = max(soc_min_pct, soc_now - soc_delta)
            deg += 0.0002 * power
            modified["power_kw"] = round(power, 2)
            out.ess_discharge_schedule.append(modified)
            out.approved_actions_detail.append(modified)
        else:
            out.approved_actions_detail.append(modified)

        out.soc_projection.append(round(soc_now, 2))

    out.degradation_estimate = round(deg, 6)
    return out


def run_storage_agent(
    site_state: dict,
    candidate_actions: list[dict],
    max_charge_kw: float = 50.0,
    max_discharge_kw: float = 50.0,
    pv_export_limit_kw: float = 999.0,
) -> StorageAgentOutput:
    """
    Run PV Operation Manager and ESS Operation Manager. Merge results.
    For non-ESS/non-PV actions (market_sell, demand_response), pass through as approved.
    """
    pv_out = _pv_operation_manager(site_state, export_limit_kw=pv_export_limit_kw)
    ess_out = _ess_operation_manager(
        site_state,
        candidate_actions,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        capacity_kwh=float(site_state.get("ess_capacity_kwh", 200) or 200),
    )

    out = StorageAgentOutput(pv=pv_out, ess=ess_out)
    # ESS-approved/rejected
    out.approved_actions = [a.get("action_id", "") for a in ess_out.approved_actions_detail if a.get("action_id")]
    out.rejected_actions = list(ess_out.rejected_actions)
    out.modified_actions = list(ess_out.modified_actions)
    out.approved_actions_detail = list(ess_out.approved_actions_detail)

    # Non-ESS actions: Storage agent does not veto market_sell or demand_response (pass through)
    for a in candidate_actions:
        if a.get("type") in ("market_sell", "demand_response"):
            out.approved_actions.append(a.get("action_id", ""))
            out.approved_actions_detail.append(dict(a))

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from alfp.llm import is_llm_enabled, get_llm

        if is_llm_enabled("parallel_storage"):
            system = """당신은 Storage 병렬 심사 보조 분석기입니다.
현재 ESS/PV 상태와 심사 결과를 보고 설비 관점의 핵심 판단을 한국어로 짧게 요약하세요.
JSON only:
{"summary": string, "soc_outlook": string, "device_risk": string}"""
            user = (
                f"site_state={json.dumps(site_state, ensure_ascii=False)}\n"
                f"candidate_actions={json.dumps(candidate_actions, ensure_ascii=False)}\n"
                f"storage_result={json.dumps({'approved_actions': out.approved_actions, 'rejected_actions': out.rejected_actions, 'soc_projection': out.ess.soc_projection, 'degradation_estimate': out.ess.degradation_estimate, 'pv_surplus_availability_kw': out.pv.pv_surplus_availability_kw}, ensure_ascii=False)}\n"
                "Output JSON only."
            )
            llm = get_llm(temperature=0.1, stage="parallel_storage")
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            text = resp.content if hasattr(resp, "content") else str(resp)
            out.llm_review = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        out.llm_review = {}

    return out
