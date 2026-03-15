"""
Data contracts for Final Parallel Execution Layer (PRD: seapac_parallel_agents_prd.md §8).

Input: site_state + candidate_actions (from negotiation stage).
Output: approved_actions, rejected_actions, modified_actions, recommendations, policy_violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SiteState:
    """Current site state passed to all parallel agents."""
    load_kw: float = 0.0
    pv_kw: float = 0.0
    ess_soc: float = 0.0
    ess_capacity_kwh: float = 200.0
    peak_threshold_kw: float = 500.0
    grid_price: float = 100.0
    time: str = ""
    # Optional extended state (e.g. from state_translator)
    community_state: dict = field(default_factory=dict)
    market_state: dict = field(default_factory=dict)
    ess_state: dict = field(default_factory=dict)

    @classmethod
    def from_state_json(cls, state_json: dict) -> "SiteState":
        """Build from Step 2 state_translator output."""
        cs = state_json.get("community_state") or {}
        ms = state_json.get("market_state") or {}
        es = state_json.get("ess_state") or {}
        load = float(cs.get("total_load", 0) or 0)
        pv = float(cs.get("pv_generation", 0) or 0)
        soc_pct = es.get("soc")
        if soc_pct is not None:
            cap = float(es.get("capacity", 200))
            soc = (soc_pct / 100.0) * cap
        else:
            soc = 0.0
            cap = float(es.get("capacity", 200))
        return cls(
            load_kw=load,
            pv_kw=pv,
            ess_soc=soc,
            ess_capacity_kwh=cap,
            peak_threshold_kw=500.0,
            grid_price=float(ms.get("grid_price") or 100),
            time=str(state_json.get("time", "")),
            community_state=cs,
            market_state=ms,
            ess_state=es,
        )


def decisions_to_candidate_bundle(
    decisions: dict,
    state_json_list: list[dict] | None = None,
) -> dict:
    """
    Convert Step 3 decisions (ess_schedule, trading_recommendations, demand_response_events)
    to PRD-style parallel layer input: site_state (per step or aggregated) + candidate_actions.

    If state_json_list is provided, we build one bundle per step; else one aggregated bundle.
    Returns structure suitable for run_parallel_evaluation().
    """
    actions = []
    step_index = 0
    for row in decisions.get("ess_schedule") or []:
        actions.append({
            "action_id": f"ess_{step_index}",
            "type": "ess",
            "subtype": row.get("action", "idle"),
            "power_kw": float(row.get("power_kw", 0.0)),
            "volume_kwh": float(row.get("power_kw", 0.0)) * 0.25,
            "soc_kwh": float(row.get("soc_kwh", 0.0)),
            "net_load_kw": float(row.get("net_load_kw", 0.0)),
            "timestamp": row.get("timestamp", ""),
            "reason": row.get("reason", ""),
            "_source_index": step_index,
        })
        step_index += 1
    for i, row in enumerate(decisions.get("trading_recommendations") or []):
        actions.append({
            "action_id": f"sell_{i}",
            "type": "market_sell",
            "volume_kwh": float(row.get("surplus_kw", 0.0)) * 0.25,
            "surplus_kw": float(row.get("surplus_kw", 0.0)),
            "bid_price": float(row.get("bid_price", 0.0)),
            "timestamp": row.get("timestamp", ""),
            "_source_index": i,
        })
    for i, row in enumerate(decisions.get("demand_response_events") or []):
        actions.append({
            "action_id": f"dr_{i}",
            "type": "demand_response",
            "net_load_kw": float(row.get("net_load_kw", 0.0)),
            "recommended_reduction_kw": float(row.get("recommended_reduction_kw", 0.0)),
            "timestamp": row.get("timestamp", ""),
            "_source_index": i,
        })

    # Single aggregated site_state for orchestrator (e.g. first/last or mean)
    site_state: dict[str, Any] = {"load_kw": 0, "pv_kw": 0, "ess_soc": 0}
    if state_json_list:
        cs = state_json_list[0].get("community_state") or {}
        es = state_json_list[0].get("ess_state") or {}
        site_state = {
            "load_kw": float(cs.get("total_load", 0) or 0),
            "pv_kw": float(cs.get("pv_generation", 0) or 0),
            "ess_soc": float(es.get("soc", 0) or 0) / 100.0 if es.get("soc") is not None else 0.0,
            "ess_capacity_kwh": float(es.get("capacity", 200) or 200),
            "peak_threshold_kw": 500.0,
            "grid_price": float((state_json_list[0].get("market_state") or {}).get("grid_price") or 100),
            "time": state_json_list[0].get("time", ""),
            "community_state": cs,
            "market_state": state_json_list[0].get("market_state") or {},
            "ess_state": es,
        }

    return {
        "site_state": site_state,
        "candidate_actions": actions,
        "state_json_list": state_json_list,
        "raw_decisions": decisions,
    }


def orchestrator_output_to_decisions(
    output: dict,
    original_decisions: dict,
) -> dict:
    """
    Convert Execution Orchestrator output back to decisions format expected by run_execution().
    approved_actions_detail = list of approved (possibly modified) action dicts with action_id, type, and fields.
    """
    ess_schedule = []
    trading_recommendations = []
    demand_response_events = []

    for a in output.get("approved_actions_detail") or []:
        t = a.get("type", "")
        if t == "ess":
            ess_schedule.append({
                "action": a.get("subtype", "idle"),
                "power_kw": a.get("power_kw", 0),
                "soc_kwh": a.get("soc_kwh", 0),
                "net_load_kw": a.get("net_load_kw", 0),
                "timestamp": a.get("timestamp", ""),
                "reason": a.get("reason", ""),
            })
        elif t == "market_sell":
            trading_recommendations.append({
                "surplus_kw": a.get("surplus_kw", a.get("volume_kwh", 0) * 4),
                "bid_price": a.get("bid_price", 0),
                "action": "sell_p2p",
                "timestamp": a.get("timestamp", ""),
            })
        elif t == "demand_response":
            demand_response_events.append({
                "net_load_kw": a.get("net_load_kw", 0),
                "recommended_reduction_kw": a.get("recommended_reduction_kw", 0),
                "action": "demand_response",
                "timestamp": a.get("timestamp", ""),
            })

    result = dict(original_decisions)
    result["ess_schedule"] = ess_schedule
    result["trading_recommendations"] = trading_recommendations
    result["demand_response_events"] = demand_response_events
    result["parallel_layer"] = {
        "approved_actions": output.get("approved_actions"),
        "rejected_actions": output.get("rejected_actions"),
        "policy_violation_report": output.get("policy_violation_report"),
        "recommendations": output.get("recommendations"),
    }
    return result
