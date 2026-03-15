"""
Simulation Sandbox (PRD §4.4 — langchain_deepagent_architecture_prd.md).

실행 전 전략을 가상 환경에서 검증: plan → simulate → evaluate → execute.
"""

from alfp.simulation_sandbox.sandbox import run_simulation_sandbox, SimulationSandboxOutput

__all__ = ["run_simulation_sandbox", "SimulationSandboxOutput"]
