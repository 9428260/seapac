"""
Final Parallel Execution Layer (PRD: seapac_parallel_agents_prd.md).

Three agents run in parallel before final execution:
  - Policy Management Agent (veto)
  - Eco Saver Agent (advisory)
  - Storage Management Agent (veto)

Execution Orchestrator merges results and produces final executable action bundle.
"""

from .contracts import (
    SiteState,
    decisions_to_candidate_bundle,
    orchestrator_output_to_decisions,
)
from .policy_agent import (
    PolicyConfig,
    PolicyAgentOutput,
    run_policy_agent,
)
from .eco_saver_agent import (
    EcoSaverOutput,
    run_eco_saver_agent,
)
from .storage_agent import (
    PVManagerOutput,
    ESSManagerOutput,
    StorageAgentOutput,
    run_storage_agent,
)
from .orchestrator import (
    OrchestratorOutput,
    run_parallel_evaluation,
    run_parallel_evaluation_and_convert,
)

__all__ = [
    "SiteState",
    "decisions_to_candidate_bundle",
    "orchestrator_output_to_decisions",
    "PolicyConfig",
    "PolicyAgentOutput",
    "run_policy_agent",
    "EcoSaverOutput",
    "run_eco_saver_agent",
    "PVManagerOutput",
    "ESSManagerOutput",
    "StorageAgentOutput",
    "run_storage_agent",
    "OrchestratorOutput",
    "run_parallel_evaluation",
    "run_parallel_evaluation_and_convert",
]
