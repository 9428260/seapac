"""
Policy + Approval Gate (PRD §4.3 — langchain_deepagent_architecture_prd.md).

Agent 행동이 규정·정책을 준수하는지 검증. 결과: APPROVED / REJECTED / REPLAN_REQUIRED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

try:
    from parallel_agents.contracts import decisions_to_candidate_bundle
    from parallel_agents.policy_agent import PolicyConfig, run_policy_agent
    _HAS_PARALLEL_AGENTS = True
except ImportError:
    _HAS_PARALLEL_AGENTS = False


@dataclass
class PolicyGateResult:
    """Policy Gate 결과 (PRD §4.3)."""
    status: str
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    policy_violation_report: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    recommended_candidate_id: str = ""
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "approved_actions": self.approved_actions,
            "rejected_actions": self.rejected_actions,
            "policy_violation_report": self.policy_violation_report,
            "risk_score": self.risk_score,
            "recommended_candidate_id": self.recommended_candidate_id,
            "details": self.details,
        }


def _site_state_from_alfp(state: dict[str, Any]) -> dict:
    decisions = state.get("decisions") or {}
    ess = (decisions.get("ess_schedule") or [])
    soc_kwh = 0.0
    if ess:
        last = ess[-1]
        soc_kwh = float(last.get("soc_kwh", 0))
    cap = 200.0
    return {
        "ess_soc": soc_kwh,
        "ess_state": {"soc": soc_kwh / cap * 100 if cap else 0, "capacity": cap},
        "load_kw": 0.0,
        "pv_kw": 0.0,
    }


def run_policy_gate(
    state: dict[str, Any],
    policy_config: Any = None,
    reject_threshold: float = 0.65,
    replan_threshold: float = 0.35,
) -> PolicyGateResult:
    """
    Policy + Approval Gate 실행.
    """
    decisions = state.get("decisions") or {}
    selected = decisions.get("selected_candidate") or {}
    selected_id = decisions.get("selected_candidate_id") or selected.get("candidate_id", "")
    critic = state.get("critic_output") or {}
    site_state = _site_state_from_alfp(state)

    if _HAS_PARALLEL_AGENTS:
        bundle = decisions_to_candidate_bundle(decisions, state_json_list=None)
        candidate_actions = bundle.get("candidate_actions") or []
        site_state = bundle.get("site_state") or site_state
        cfg = policy_config or PolicyConfig()
        out = run_policy_agent(site_state, candidate_actions, cfg)
        approved = list(out.approved_actions)
        rejected = list(out.rejected_actions)
        violations = list(out.policy_violation_report)
        risk = float(out.risk_score)
    else:
        approved = []
        rejected = []
        violations = []
        risk = 0.0
        for i, row in enumerate(decisions.get("ess_schedule") or []):
            aid = f"ess_{i}"
            pw = float(row.get("power_kw", 0))
            soc = float(row.get("soc_kwh", 0))
            if pw < 0:
                rejected.append(aid)
                violations.append(f"[{aid}] ESS power cannot be negative")
            elif soc < 0:
                rejected.append(aid)
                violations.append(f"[{aid}] ESS SoC cannot be negative")
            else:
                approved.append(aid)
        approved.extend(f"sell_{i}" for i in range(len(decisions.get("trading_recommendations") or [])))
        approved.extend(f"dr_{i}" for i in range(len(decisions.get("demand_response_events") or [])))

    policy_probability = float(selected.get("policy_violation_probability", 0.0) or 0.0)
    candidate_risk = float(selected.get("risk_score", 0.0) or 0.0)
    degradation = float(selected.get("battery_degradation_cost_krw", 0.0) or 0.0)
    scenario_mode = decisions.get("scenario_mode", "day_ahead")
    prosumer_type = (state.get("forecast_plan") or {}).get("prosumer_type", "")

    risk = max(risk, policy_probability * 0.6 + candidate_risk * 0.4)
    if degradation > 180:
        risk += 0.08
        violations.append("Battery degradation cost is too high for current policy tolerance")
    if scenario_mode == "anomaly_response" and selected.get("trading_variant") == "aggressive":
        risk += 0.12
        violations.append("Aggressive trading is restricted during anomaly response mode")
    if prosumer_type == "Residential" and selected.get("dr_variant") == "high":
        risk += 0.08
        violations.append("High-intensity DR is not acceptable for residential comfort policy")
    if critic.get("revised_candidate_id") and critic.get("revised_candidate_id") != selected_id:
        risk += 0.05
        violations.append(
            f"Critic found a safer alternative candidate: {critic.get('revised_candidate_id')}"
        )

    risk = round(min(risk, 0.99), 2)
    recommended_candidate_id = str(critic.get("revised_candidate_id", "") or selected_id)

    if risk >= reject_threshold or (rejected and len(rejected) >= max(len(approved), 1)):
        status = "REJECTED"
    elif risk >= replan_threshold or len(violations) >= 2 or recommended_candidate_id != selected_id:
        status = "REPLAN_REQUIRED"
    else:
        status = "APPROVED"

    return PolicyGateResult(
        status=status,
        approved_actions=approved,
        rejected_actions=rejected,
        policy_violation_report=violations,
        risk_score=risk,
        recommended_candidate_id=recommended_candidate_id,
        details={
            "site_state_keys": list(site_state.keys()),
            "selected_candidate_id": selected_id,
            "scenario_mode": scenario_mode,
            "policy_violation_probability": policy_probability,
            "candidate_risk_score": candidate_risk,
            "battery_degradation_cost_krw": degradation,
        },
    )
