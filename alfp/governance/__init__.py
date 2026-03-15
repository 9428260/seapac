"""
Governance layer for domain-specific autonomous operation (PRD: langchain_deepagent_architecture_prd.md).

- Evidence Curator: 구조화된 의사결정 근거 저장
- Critic Agent: 리스크·반례·대안 전략 검토
- Policy Gate: 규정·정책 준수 검증 (APPROVED / REJECTED / REPLAN_REQUIRED)
"""

from alfp.governance.evidence_curator import curate_evidence, EvidenceCuratorOutput
from alfp.governance.critic_agent import run_critic_agent, CriticAgentOutput
from alfp.governance.policy_gate import run_policy_gate, PolicyGateResult

__all__ = [
    "curate_evidence",
    "EvidenceCuratorOutput",
    "run_critic_agent",
    "CriticAgentOutput",
    "run_policy_gate",
    "PolicyGateResult",
]
