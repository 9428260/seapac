"""
Policy + Approval Gate (PRD §4.3 — langchain_deepagent_architecture_prd.md).

Agent 행동이 규정·정책을 준수하는지 검증. 결과: APPROVED / REJECTED / REPLAN_REQUIRED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Optional: use parallel_agents when available
try:
    from parallel_agents.contracts import decisions_to_candidate_bundle
    from parallel_agents.policy_agent import PolicyConfig, run_policy_agent
    _HAS_PARALLEL_AGENTS = True
except ImportError:
    _HAS_PARALLEL_AGENTS = False


@dataclass
class PolicyGateResult:
    """Policy Gate 결과 (PRD §4.3)."""
    status: str  # "APPROVED" | "REJECTED" | "REPLAN_REQUIRED"
    approved_actions: list[str] = field(default_factory=list)
    rejected_actions: list[str] = field(default_factory=list)
    policy_violation_report: list[str] = field(default_factory=list)
    risk_score: float = 0.0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "approved_actions": self.approved_actions,
            "rejected_actions": self.rejected_actions,
            "policy_violation_report": self.policy_violation_report,
            "risk_score": self.risk_score,
            "details": self.details,
        }


def _site_state_from_alfp(state: dict[str, Any]) -> dict:
    """ALFP state에서 policy agent용 site_state 구성."""
    decisions = state.get("decisions") or {}
    ess = (decisions.get("ess_schedule") or [])
    # 마지막 ESS 스텝의 soc 사용 (또는 기본값)
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
    reject_threshold: float = 0.6,
    replan_threshold: float = 0.4,
) -> PolicyGateResult:
    """
    Policy + Approval Gate 실행.

    - parallel_agents 있으면 decisions → candidate_bundle → run_policy_agent 호출.
    - 없으면 decisions만 검사해 APPROVED 반환 (규칙 기반 최소 검사 가능).

    Args:
        state: ALFPState (decisions, forecast_plan 등)
        policy_config: PolicyConfig (None이면 기본값)
        reject_threshold: risk_score >= 이 값이면 REJECTED
        replan_threshold: risk_score >= 이 값이면 REPLAN_REQUIRED (그 미만이면 APPROVED)

    Returns:
        PolicyGateResult (status = APPROVED | REJECTED | REPLAN_REQUIRED)
    """
    decisions = state.get("decisions") or {}
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
        risk = out.risk_score
    else:
        approved = []
        rejected = []
        violations = []
        risk = 0.0
        for i, row in enumerate(decisions.get("ess_schedule") or []):
            aid = f"ess_{i}"
            pw = float(row.get("power_kw", 0))
            if pw < 0:
                rejected.append(aid)
                violations.append(f"[{aid}] ESS power cannot be negative")
            else:
                approved.append(aid)
        for i in range(len(decisions.get("trading_recommendations") or [])):
            approved.append(f"sell_{i}")
        for i in range(len(decisions.get("demand_response_events") or [])):
            approved.append(f"dr_{i}")
        if rejected:
            risk = 0.5

    # Status 결정
    if risk >= reject_threshold or (rejected and len(rejected) >= len(approved)):
        status = "REJECTED"
    elif risk >= replan_threshold or len(violations) > 2:
        status = "REPLAN_REQUIRED"
    else:
        status = "APPROVED"

    return PolicyGateResult(
        status=status,
        approved_actions=approved,
        rejected_actions=rejected,
        policy_violation_report=violations,
        risk_score=risk,
        details={"site_state_keys": list(site_state.keys())},
    )
