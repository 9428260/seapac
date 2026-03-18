"""
Pipeline Dashboard — DB and UI for SEAPAC pipeline stage results.

Architecture steps stored:
  1. ALFP decision
  2. MESA Simulation Engine
  3. Forecast / State input or optional state shaping
  4. Step3 AgentScope Multi-Agent Decision
  5. Step4 Action Execution / Settlement
  6. Step5 Evaluation Engine
  7. MESA next simulation step
  8. Parallel Agents
"""

from pipeline_dashboard.db import (
    get_db_path,
    init_db,
    create_run,
    add_stage,
    finish_run,
    get_runs,
    get_run_with_stages,
)

__all__ = [
    "get_db_path",
    "init_db",
    "create_run",
    "add_stage",
    "finish_run",
    "get_runs",
    "get_run_with_stages",
]
