"""
Simulation Sandbox (PRD §4.4).

Workflow: plan -> simulate -> evaluate -> execute.
Input: 전략, 시스템 상태, 외부 환경.
Output: predicted_cost, peak_load, battery_degradation, expected_profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SimulationSandboxOutput:
    """Simulation Sandbox 출력 (PRD §4.4)."""
    predicted_cost: float = 0.0
    peak_load: float = 0.0
    battery_degradation: float = 0.0
    expected_profit: float = 0.0
    simulated: bool = False
    recommended_candidate_id: str = ""
    candidate_simulations: list[dict[str, Any]] = field(default_factory=list)
    replan_required: bool = False
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted_cost": self.predicted_cost,
            "peak_load": self.peak_load,
            "battery_degradation": self.battery_degradation,
            "expected_profit": self.expected_profit,
            "simulated": self.simulated,
            "recommended_candidate_id": self.recommended_candidate_id,
            "candidate_simulations": self.candidate_simulations,
            "replan_required": self.replan_required,
            "message": self.message,
        }


def _simulate_candidate(candidate: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    tariff = candidate.get("tariff_saving") or {}
    ess_schedule = candidate.get("ess_schedule") or []
    peak_load = 0.0
    for row in ess_schedule:
        peak_load = max(peak_load, float(row.get("net_load_kw", 0) or 0))
    if peak_load == 0.0:
        nl_df = state.get("net_load_forecast")
        if nl_df is not None and hasattr(nl_df, "get"):
            try:
                import pandas as pd
                if isinstance(nl_df, pd.DataFrame):
                    series = nl_df.get("predicted_net_load_kw", nl_df.get("actual_net_load_kw"))
                    if series is not None:
                        peak_load = float(series.max())
            except Exception:
                pass

    degradation_cost = float(candidate.get("battery_degradation_cost_krw", 0.0) or 0.0)
    expected_profit = float(candidate.get("expected_profit_krw", 0.0) or tariff.get("saving_krw", 0.0) or 0.0)
    risk = float(candidate.get("risk_score", 0.5) or 0.5)
    policy = float(candidate.get("policy_violation_probability", 0.2) or 0.2)
    predicted_cost = float(tariff.get("adjusted_cost_krw", 0) or 0)
    simulation_score = round(expected_profit * 0.0013 + (1 - risk) * 0.35 + (1 - policy) * 0.2 - degradation_cost * 0.0008, 4)

    return {
        "candidate_id": candidate.get("candidate_id"),
        "predicted_cost": predicted_cost,
        "peak_load": round(peak_load, 2),
        "battery_degradation_cost_krw": round(degradation_cost, 1),
        "expected_profit": round(expected_profit, 1),
        "risk_score": risk,
        "policy_violation_probability": policy,
        "simulation_score": simulation_score,
    }


def run_simulation_sandbox(
    state: dict[str, Any],
    decisions: dict[str, Any] | None = None,
    use_mesa: bool = False,
) -> SimulationSandboxOutput:
    """
    실행 전 전략 검증. use_mesa=True이고 simulation 모듈 있으면 Mesa 실행, 아니면 후보 비교형 규칙 기반 추정.
    """
    decisions = decisions or state.get("decisions") or {}
    out = SimulationSandboxOutput()

    if use_mesa:
        try:
            return _run_mesa_sandbox(state, decisions)
        except Exception as e:
            out.message = f"Mesa sandbox skipped: {e}"
            out.simulated = False

    candidates = list(decisions.get("strategy_candidates") or [])
    selected = decisions.get("selected_candidate") or {}
    if selected and not any(c.get("candidate_id") == selected.get("candidate_id") for c in candidates):
        candidates.insert(0, selected)
    if not candidates:
        candidates = [{
            "candidate_id": decisions.get("selected_candidate_id", "selected"),
            "ess_schedule": decisions.get("ess_schedule") or [],
            "tariff_saving": decisions.get("tariff_saving") or {},
            "risk_score": 0.5,
            "policy_violation_probability": 0.2,
            "battery_degradation_cost_krw": 0.0,
            "expected_profit_krw": float((decisions.get("tariff_saving") or {}).get("saving_krw", 0.0) or 0.0),
        }]

    simulations = [_simulate_candidate(candidate, state) for candidate in candidates]
    simulations.sort(key=lambda item: item["simulation_score"], reverse=True)
    best = simulations[0]
    selected_id = decisions.get("selected_candidate_id") or selected.get("candidate_id") or best["candidate_id"]
    selected_sim = next((item for item in simulations if item["candidate_id"] == selected_id), best)

    out.predicted_cost = float(selected_sim["predicted_cost"])
    out.peak_load = float(selected_sim["peak_load"])
    out.battery_degradation = round(float(selected_sim["battery_degradation_cost_krw"]) / 1000.0, 4)
    out.expected_profit = float(selected_sim["expected_profit"])
    out.simulated = False
    out.candidate_simulations = simulations[:5]
    out.recommended_candidate_id = str(best["candidate_id"])
    score_gap = float(best["simulation_score"]) - float(selected_sim["simulation_score"])
    out.replan_required = best["candidate_id"] != selected_id and score_gap >= 0.05
    out.message = (
        f"Rule-based sandbox compared {len(simulations)} candidates; selected={selected_id}, "
        f"recommended={best['candidate_id']}, score_gap={score_gap:.3f}"
    )
    return out


def _run_mesa_sandbox(state: dict[str, Any], decisions: dict[str, Any]) -> SimulationSandboxOutput:
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from seapac_agents.execution import run_execution
    except ImportError:
        raise RuntimeError("seapac_agents.execution not available")
    result = run_execution(decisions, state_json_list=None)
    out = SimulationSandboxOutput(simulated=True, message="Mesa execution completed")
    if result.summary is not None and hasattr(result.summary, "get"):
        s = result.summary
        out.predicted_cost = float(s.get("total_cost", 0) or 0)
        out.peak_load = float(s.get("peak_load_kw", 0) or 0)
        out.expected_profit = float(s.get("profit", 0) or 0)
    out.recommended_candidate_id = decisions.get("selected_candidate_id", "")
    return out
