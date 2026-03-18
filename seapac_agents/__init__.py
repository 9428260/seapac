"""
SEAPAC AgentScope 에이전트 레이어 (Step 3~5 중심)

입력된 Forecast / State를 바탕으로 운영 의사결정, 실행, 평가 단계를 담당합니다.

  Step 3 — decision          : AgentScope 5개 에이전트 의사결정
  Step 4 — execution         : 검증·승인 → Mesa 업데이트
  Step 5 — evaluation        : KPI 평가 및 등급 산정

보조 유틸리티:
  - state_translator         : 상태 JSON 생성/요약 헬퍼 (핵심 파이프라인 필수 단계는 아님)

Pipeline CLI:
  python seapac_agents/run_agentic_pipeline.py --steps 96 --phase 4
"""

from seapac_agents.state_translator import (
    translate_model_state,
    translate_dataframe,
    generate_summary,
    translate_and_summarize,
)
from seapac_agents.decision import (
    PolicyAgentAS,
    SmartSellerAgentAS,
    StorageMasterAgentAS,
    EcoSaverAgentAS,
    MarketCoordinatorAgentAS,
    run_agentscope_decision,
    run_agentscope_decision_series,
)
from seapac_agents.execution import (
    ESSAction,
    TradeAction,
    DemandResponseAction,
    ExecutionResult,
    run_execution,
    approve_after_simulation,
)
from seapac_agents.self_critic import run_self_critic, SelfCriticOutput
from seapac_agents.evaluation import (
    EvaluationConfig,
    EvaluationReport,
    run_evaluation,
    evaluate_from_execution_result,
)

__all__ = [
    # Utility
    "translate_model_state",
    "translate_dataframe",
    "generate_summary",
    "translate_and_summarize",
    # Step 3
    "PolicyAgentAS",
    "SmartSellerAgentAS",
    "StorageMasterAgentAS",
    "EcoSaverAgentAS",
    "MarketCoordinatorAgentAS",
    "run_agentscope_decision",
    "run_agentscope_decision_series",
    # Step 4
    "ESSAction",
    "TradeAction",
    "DemandResponseAction",
    "ExecutionResult",
    "run_execution",
    "approve_after_simulation",
    "run_self_critic",
    "SelfCriticOutput",
    # Step 5
    "EvaluationConfig",
    "EvaluationReport",
    "run_evaluation",
    "evaluate_from_execution_result",
]
