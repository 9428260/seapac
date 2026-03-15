"""
Policy Management Agent (PRD §5.1 — seapac_parallel_agents_prd.md).

Validates candidate actions against regulatory policies, safety rules, and operational constraints.
Has veto authority: rejected actions are not executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PolicyConfig:
    """Market policy and device safety configuration."""
    max_charge_kw: float = 50.0
    max_discharge_kw: float = 50.0
    ess_soc_min_pct: float = 10.0
    ess_soc_max_pct: float = 95.0
    min_trade_kw: float = 0.2
    max_trade_volume_kwh: float = 100.0
    price_floor: float = 0.0
    price_ceiling: float = 500.0
    pv_export_limit_kw: float = 999.0
    dr_reduction_max_kw: float = 500.0


@dataclass
class PolicyAgentOutput:
    """Policy Management Agent output (PRD §5.1)."""
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    modified_actions: list[dict] = field(default_factory=list)
    approved_actions_detail: list[dict] = field(default_factory=list)  # full dict per approved action
    policy_violation_report: list[str] = field(default_factory=list)
    risk_score: float = 0.0  # 0.0 = safe, 1.0 = high risk


def run_policy_agent(
    site_state: dict,
    candidate_actions: list[dict],
    config: PolicyConfig | None = None,
) -> PolicyAgentOutput:
    """
    Validate candidate actions against policies. Returns approved/rejected/modified lists
    and violation report. Policy agent has veto authority.
    """
    cfg = config or PolicyConfig()
    out = PolicyAgentOutput()
    violations: list[str] = []
    risk_accum = 0.0

    ess_soc_pct = site_state.get("ess_soc")
    if isinstance(ess_soc_pct, (int, float)):
        pass
    else:
        es = site_state.get("ess_state") or {}
        cap = float(es.get("capacity", 200) or 200)
        soc_raw = site_state.get("ess_soc")
        if soc_raw is not None:
            ess_soc_pct = (float(soc_raw) / cap * 100.0) if cap > 0 else 0
        else:
            ess_soc_pct = (float(es.get("soc", 50)) if es.get("soc") is not None else 50)

    for action in candidate_actions:
        action_id = action.get("action_id", "")
        atype = action.get("type", "")
        modified = dict(action)
        approved = True
        action_violations: list[str] = []

        if atype == "ess":
            subtype = action.get("subtype", "idle")
            power = float(action.get("power_kw", 0))
            if subtype == "charge":
                if power > cfg.max_charge_kw:
                    modified["power_kw"] = cfg.max_charge_kw
                    action_violations.append(f"ESS charge clamped to {cfg.max_charge_kw} kW")
                    risk_accum += 0.1
                if ess_soc_pct is not None and float(ess_soc_pct) >= cfg.ess_soc_max_pct:
                    modified["subtype"] = "idle"
                    modified["power_kw"] = 0.0
                    action_violations.append(f"ESS charge blocked: SoC >= {cfg.ess_soc_max_pct}%")
                    approved = False
            elif subtype == "discharge":
                if power > cfg.max_discharge_kw:
                    modified["power_kw"] = cfg.max_discharge_kw
                    action_violations.append(f"ESS discharge clamped to {cfg.max_discharge_kw} kW")
                    risk_accum += 0.1
                if ess_soc_pct is not None and float(ess_soc_pct) <= cfg.ess_soc_min_pct:
                    modified["subtype"] = "idle"
                    modified["power_kw"] = 0.0
                    action_violations.append(f"ESS discharge blocked: SoC <= {cfg.ess_soc_min_pct}%")
                    approved = False
            if power < 0:
                modified["power_kw"] = 0.0
                action_violations.append("ESS power cannot be negative")
            if approved or (modified.get("power_kw", 0) == 0 and modified.get("subtype") == "idle"):
                out.approved_actions.append(action_id)
                if action_violations:
                    out.modified_actions.append(modified)
                    violations.extend([f"[{action_id}] {v}" for v in action_violations])
                out.approved_actions_detail.append(modified)
            else:
                out.rejected_actions.append(action_id)
                violations.extend([f"[{action_id}] {v}" for v in action_violations])
            continue

        if atype == "market_sell":
            vol = float(action.get("volume_kwh", 0) or action.get("surplus_kw", 0) * 0.25)
            surplus_kw = float(action.get("surplus_kw", vol * 4))
            price = float(action.get("bid_price", 0))
            if surplus_kw < cfg.min_trade_kw:
                out.rejected_actions.append(action_id)
                violations.append(f"[{action_id}] Trade volume {surplus_kw} kW < min {cfg.min_trade_kw} kW")
                continue
            if vol > cfg.max_trade_volume_kwh:
                modified["volume_kwh"] = cfg.max_trade_volume_kwh
                modified["surplus_kw"] = cfg.max_trade_volume_kwh * 4
                action_violations.append(f"Trade volume clamped to {cfg.max_trade_volume_kwh} kWh")
            if price < cfg.price_floor or price > cfg.price_ceiling:
                out.rejected_actions.append(action_id)
                violations.append(f"[{action_id}] Price {price} outside [{cfg.price_floor}, {cfg.price_ceiling}]")
                continue
            out.approved_actions.append(action_id)
            if action_violations:
                out.modified_actions.append(modified)
                violations.extend([f"[{action_id}] {v}" for v in action_violations])
            out.approved_actions_detail.append(modified)
            continue

        if atype == "demand_response":
            red = float(action.get("recommended_reduction_kw", 0))
            if red < 0:
                out.rejected_actions.append(action_id)
                violations.append(f"[{action_id}] DR reduction cannot be negative")
                continue
            if red > cfg.dr_reduction_max_kw:
                modified["recommended_reduction_kw"] = cfg.dr_reduction_max_kw
                action_violations.append(f"DR reduction clamped to {cfg.dr_reduction_max_kw} kW")
            out.approved_actions.append(action_id)
            if action_violations:
                out.modified_actions.append(modified)
                violations.extend([f"[{action_id}] {v}" for v in action_violations])
            out.approved_actions_detail.append(modified)
            continue

        # Unknown type: approve by default (no veto)
        out.approved_actions.append(action_id)
        out.approved_actions_detail.append(modified)

    out.policy_violation_report = violations
    n = len(candidate_actions)
    out.risk_score = min(1.0, risk_accum + (len(out.rejected_actions) / max(1, n)) * 0.5)
    return out
