"""
Simulation Sandbox (PRD §4.4).

Workflow: plan → simulate → evaluate → execute.
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
    simulated: bool = False  # True if full simulation ran
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "predicted_cost": self.predicted_cost,
            "peak_load": self.peak_load,
            "battery_degradation": self.battery_degradation,
            "expected_profit": self.expected_profit,
            "simulated": self.simulated,
            "message": self.message,
        }


def run_simulation_sandbox(
    state: dict[str, Any],
    decisions: dict[str, Any] | None = None,
    use_mesa: bool = False,
) -> SimulationSandboxOutput:
    """
    실행 전 전략 검증. use_mesa=True이고 simulation 모듈 있으면 Mesa 실행, 아니면 규칙 기반 추정.

    Args:
        state: ALFPState (decisions, net_load_forecast, feature_df 등)
        decisions: override state["decisions"] (optional)
        use_mesa: True면 Mesa/run_execution 연동 시도

    Returns:
        SimulationSandboxOutput
    """
    decisions = decisions or state.get("decisions") or {}
    out = SimulationSandboxOutput()

    if use_mesa:
        try:
            return _run_mesa_sandbox(state, decisions)
        except Exception as e:
            out.message = f"Mesa sandbox skipped: {e}"
            out.simulated = False

    # Rule-based estimate from decisions + net_load
    nl_df = state.get("net_load_forecast")
    if nl_df is not None and hasattr(nl_df, "get"):
        try:
            import pandas as pd
            if isinstance(nl_df, pd.DataFrame):
                net_load = nl_df.get("predicted_net_load_kw", nl_df.get("actual_net_load_kw"))
                if net_load is not None:
                    out.peak_load = float(net_load.max())
        except Exception:
            pass
    if not out.peak_load and decisions.get("ess_schedule"):
        for row in decisions["ess_schedule"]:
            nl = float(row.get("net_load_kw", 0))
            if nl > out.peak_load:
                out.peak_load = nl

    ess_summary = decisions.get("ess_summary") or {}
    n_charge = ess_summary.get("charge_steps", 0) or 0
    n_discharge = ess_summary.get("discharge_steps", 0) or 0
    # 간단 추정: 방전/충전 스텝 비율로 열화 가중치
    out.battery_degradation = min(1.0, (n_charge + n_discharge) * 0.001)

    tariff = decisions.get("tariff_saving") or {}
    out.predicted_cost = float(tariff.get("adjusted_cost_krw", 0) or 0)
    out.expected_profit = float(tariff.get("saving_krw", 0) or 0)
    if not out.message:
        out.message = "Rule-based sandbox estimate (Mesa not used)"
    return out


def _run_mesa_sandbox(state: dict[str, Any], decisions: dict[str, Any]) -> SimulationSandboxOutput:
    """Mesa 시뮬레이션 연동 (선택적)."""
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
    return out
