# LangChain DeepAgent 도메인 특화 자율 운영 구조

PRD: [langchain_deepagent_architecture_prd.md](langchain_deepagent_architecture_prd.md) 기반 구현 매핑.

## High-Level Flow (PRD §3)

```
User / External System
→ DeepAgent Planner (forecast_planner)
→ Task Decomposition (load / pv / net_load forecast)
→ Agent Collaboration Layer (validation → decision)
→ Evidence Curator
→ Critic Agent
→ Policy Gate
→ Simulation Sandbox
→ Execution (save_memory + strategy_memory)
→ Strategy Memory + Evaluation Loop
```

## 구현 위치

| PRD 모듈 | 구현 | 설명 |
|----------|------|------|
| **§4.1 Evidence Curator** | `alfp/governance/evidence_curator.py` | 의사결정 근거 구조화 (task_id, context_summary, data_sources, reasoning_summary, alternatives, chosen_strategy, confidence_score). 파이프라인 노드: `evidence_curator`. |
| **§4.2 Critic / Red-Team Agent** | `alfp/governance/critic_agent.py` | 리스크 분석, 실패 시나리오, 대안 전략. 규칙/LLM 선택 가능. 노드: `critic_agent`. |
| **§4.3 Policy + Approval Gate** | `alfp/governance/policy_gate.py` | 규정·정책 검증. `parallel_agents.policy_agent` 연동. 결과: APPROVED / REJECTED / REPLAN_REQUIRED. 노드: `policy_gate`. |
| **§4.4 Simulation Sandbox** | `alfp/simulation_sandbox/sandbox.py` | 실행 전 전략 검증. 규칙 기반 추정 또는 Mesa 연동(선택). 출력: predicted_cost, peak_load, battery_degradation, expected_profit. 노드: `simulation_sandbox`. |
| **§4.5 Strategy Memory + Evaluation Loop** | `alfp/memory/strategy_memory.py` | 전략 성과 저장(context, strategy, result, performance_score). expected vs actual 평가, 성공/실패 가중치 갱신. `save_memory` 노드에서 호출. |

## 파이프라인 그래프 (LangGraph)

- **진입**: `data_loader` → `data_quality` → `feature_engineering` → `forecast_planner` → `load_forecast` → `pv_forecast` → `net_load_forecast` → `validation`.
- **Validation 후**: KPI 미달 & 재시도 여유 → `replan` → `forecast_planner`; 그 외 → `decision`.
- **Governance**: `decision` → `evidence_curator` → `critic_agent` → `policy_gate`.
- **Policy Gate 후**:
  - `APPROVED` → `simulation_sandbox` → `save_memory` → END.
  - `REPLAN_REQUIRED` (재시도 여유 있음) → `replan` → `forecast_planner`.
  - `REJECTED` 또는 재시도 소진 → `save_memory` → END.

## State 확장 (ALFPState)

- `evidence`: Evidence Curator 출력
- `critic_output`: Critic Agent 출력
- `policy_gate_result`: Policy Gate 결과 (status, risk_score 등)
- `simulation_result`: Simulation Sandbox 출력
- `strategy_memory_entry`: 이번 런 Strategy Memory 저장 요약

## 데이터 저장소 (PRD §6)

- **영구 메모리**: `memory_store/{prosumer_id}.json` (기존)
- **Strategy Memory**: `strategy_memory/{prosumer_id}_strategies.jsonl` (추가)
- **Evidence DB**: 현재는 state 내 evidence 필드; 별도 Evidence DB 연동 시 `evidence_curator`에서 저장 로직 추가 가능

## 실행

기존과 동일하게 `run_pipeline(prosumer_id, data_path, forecast_horizon, run_id, db_path)` 호출 시 위 Governance + Strategy Memory 흐름이 포함됩니다.
