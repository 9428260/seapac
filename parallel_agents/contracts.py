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
    def from_state_json(
        cls,
        state_json: dict,
        *,
        peak_threshold_kw: float = 500.0,
    ) -> "SiteState":
        """Build from Step 2 state_translator output."""
        cs = state_json.get("community_state") or {}
        ms = state_json.get("market_state") or {}
        es = state_json.get("ess_state") or {}
        load = float(cs.get("total_load", 0) or 0)
        pv = float(cs.get("pv_generation", 0) or 0)
        soc_pct = es.get("soc")
        cap = float(es.get("capacity", 200))
        soc = float(soc_pct) if soc_pct is not None else 0.0
        return cls(
            load_kw=load,
            pv_kw=pv,
            ess_soc=soc,
            ess_capacity_kwh=cap,
            peak_threshold_kw=peak_threshold_kw,
            grid_price=float(ms.get("grid_price") or 100),
            time=str(state_json.get("time", "")),
            community_state=cs,
            market_state=ms,
            ess_state=es,
        )


def decisions_to_candidate_bundle(
    decisions: dict,
    state_json_list: list[dict] | None = None,
    *,
    peak_threshold_kw: float = 500.0,
) -> dict:
    """
    Convert Step 3 decisions (ess_schedule, trading_recommendations, demand_response_events)
    to PRD-style parallel layer input: site_state (per step or aggregated) + candidate_actions.

    If state_json_list is provided, we build one bundle per step; else one aggregated bundle.
    Returns structure suitable for run_parallel_evaluation().
    """
    actions = []
    step_index = 0
    def _f(val, default: float = 0.0) -> float:
        """Safe float: returns default when val is None or missing."""
        return float(val if val is not None else default)

    for row in decisions.get("ess_schedule") or []:
        pw = _f(row.get("power_kw"))
        actions.append({
            "action_id": f"ess_{step_index}",
            "type": "ess",
            "subtype": row.get("action", "idle"),
            "power_kw": pw,
            "volume_kwh": pw * 0.25,
            "soc_kwh": _f(row.get("soc_kwh")),
            "net_load_kw": _f(row.get("net_load_kw")),
            "timestamp": row.get("timestamp", ""),
            "reason": row.get("reason", ""),
            "_source_index": step_index,
        })
        step_index += 1
    for i, row in enumerate(decisions.get("trading_recommendations") or []):
        actions.append({
            "action_id": f"sell_{i}",
            "type": "market_sell",
            "volume_kwh": _f(row.get("surplus_kw")) * 0.25,
            "surplus_kw": _f(row.get("surplus_kw")),
            "bid_price": _f(row.get("bid_price")),
            "timestamp": row.get("timestamp", ""),
            "_source_index": i,
        })
    for i, row in enumerate(decisions.get("demand_response_events") or []):
        actions.append({
            "action_id": f"dr_{i}",
            "type": "demand_response",
            "net_load_kw": _f(row.get("net_load_kw")),
            "recommended_reduction_kw": _f(row.get("recommended_reduction_kw")),
            "timestamp": row.get("timestamp", ""),
            "_source_index": i,
        })

    # Single aggregated site_state for backward compatibility.
    site_state: dict[str, Any] = {"load_kw": 0, "pv_kw": 0, "ess_soc": 0}
    step_bundles: list[dict[str, Any]] = []
    if state_json_list:
        cs = state_json_list[0].get("community_state") or {}
        es = state_json_list[0].get("ess_state") or {}
        site_state = {
            "load_kw": float(cs.get("total_load", 0) or 0),
            "pv_kw": float(cs.get("pv_generation", 0) or 0),
            "ess_soc": float(es.get("soc", 0) or 0) if es.get("soc") is not None else 0.0,
            "ess_capacity_kwh": float(es.get("capacity", 200) or 200),
            "peak_threshold_kw": peak_threshold_kw,
            "grid_price": float((state_json_list[0].get("market_state") or {}).get("grid_price") or 100),
            "time": state_json_list[0].get("time", ""),
            "community_state": cs,
            "market_state": state_json_list[0].get("market_state") or {},
            "ess_state": es,
        }
        step_bundles = _build_step_bundles(
            state_json_list,
            actions,
            peak_threshold_kw=peak_threshold_kw,
        )

    return {
        "site_state": site_state,
        "candidate_actions": actions,
        "step_bundles": step_bundles,
        "state_json_list": state_json_list,
        "raw_decisions": decisions,
    }


def _build_step_bundles(
    state_json_list: list[dict],
    actions: list[dict],
    *,
    peak_threshold_kw: float,
) -> list[dict[str, Any]]:
    """Build one evaluation bundle per time step using timestamp first, then source index."""
    step_bundles: list[dict[str, Any]] = []
    time_to_indices: dict[str, list[int]] = {}
    for idx, state in enumerate(state_json_list):
        time_to_indices.setdefault(str(state.get("time", "")), []).append(idx)
        step_bundles.append({
            "step_index": idx,
            "site_state": SiteState.from_state_json(
                state,
                peak_threshold_kw=peak_threshold_kw,
            ).__dict__,
            "candidate_actions": [],
        })

    for action in actions:
        action_time = str(action.get("timestamp", ""))
        source_index = action.get("_source_index")
        target_idx: int | None = None

        if action_time and action_time in time_to_indices:
            if len(time_to_indices[action_time]) == 1:
                target_idx = time_to_indices[action_time][0]
            elif isinstance(source_index, int) and 0 <= source_index < len(state_json_list):
                target_idx = source_index
            else:
                target_idx = time_to_indices[action_time][0]
        elif isinstance(source_index, int) and 0 <= source_index < len(state_json_list):
            target_idx = source_index

        if target_idx is None:
            continue
        step_bundles[target_idx]["candidate_actions"].append(dict(action))

    return step_bundles


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
        "modified_actions": output.get("modified_actions"),
        "policy_violation_report": output.get("policy_violation_report"),
        "recommendations": output.get("recommendations"),
        "risk_score": output.get("risk_score"),
        "notification_payload": output.get("notification_payload"),
        "evaluated_steps": output.get("evaluated_steps"),
        "step_summaries": output.get("step_summaries"),
    }
    return result
