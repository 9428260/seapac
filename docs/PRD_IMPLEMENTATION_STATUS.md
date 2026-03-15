# PRD 파이프라인 구현 현황 및 점검 결과

PRD: [seapac_agentic_prd.md](prd/seapac_agentic_prd.md)  
파이프라인: `Mesa Simulation → Step 2 State Translator → Step 3 Multi-Agent Decision → Step 4 Action Execution → Step 5 Evaluation`

---

## 1. AgentScope 사용 여부

| 항목 | 결과 | 설명 |
|------|------|------|
| **AgentScope 사용** | **미사용** | 코드베이스에 AgentScope 라이브러리·임포트·호출이 **전혀 없음**. PRD의 Technology Stack에만 "Agents: AgentScope"로 명시되어 있음. |
| **대체 구현** | LangGraph + 단일 LLM | 에이전트 오케스트레이션은 **LangGraph**(`alfp/pipeline/graph.py`)로 구현되어 있고, 의사결정·해석은 **LangChain + Azure OpenAI(GPT-4o)** 단일 LLM으로 수행됨. |

**결론**: AgentScope는 **미도입** 상태이며, PRD의 “LLM-based multi-agent orchestration (AgentScope)”는 현재 **LangGraph + 단일 LLM** 구조로만 구현되어 있음.

---

## 2. LLM 사용 여부

LLM은 **ALFP 파이프라인 내**에서만 사용됨. AgentScope나 Mesa 실행 단계에서는 호출하지 않음.

| 위치 | 파일 | 용도 |
|------|------|------|
| **ForecastPlannerAgent** | `alfp/agents/forecast_planner.py` | 데이터 특성·날씨 분석, 모델/전략 추론 (`get_llm`, `llm.invoke`) |
| **ValidationAgent** | `alfp/agents/validation.py` | 검증 지표 해석, 개선 방향 제시 (`get_llm`, `llm.invoke`) |
| **DecisionAgent** | `alfp/agents/decision.py` | ESS·거래·DR 운영 전략 및 경보/추천 (`get_llm`, `llm_strategy`) |
| **LLM 팩토리** | `alfp/llm.py` | Azure OpenAI (AzureChatOpenAI) 공통 인스턴스 |

- **Step 2 (State Translator)**: `state_translator.py`가 Mesa 상태를 **LLM 친화적 JSON**으로 변환하지만, **LLM을 호출하지는 않음** (규칙 기반 변환만 수행).
- **Step 3 (Multi-Agent Decision)**: `multi_agent_decision.py`의 5개 에이전트(SmartSeller, StorageMaster, EcoSaver, MarketCoordinator, Policy)는 **전부 규칙 기반 Python 클래스**이며 **LLM 호출 없음**. (ALFP의 DecisionAgent는 별도로 LLM 사용.)
- **Step 4 (Action Execution)**: `seapac_agents/execution.py`, `simulation/run_execution.py`에는 **LLM 호출 없음** (정책 검증·Mesa 업데이트만 수행).
- **Step 5 (Evaluation)**: `simulation/evaluation.py`는 KPI 계산만 수행, **LLM 호출 없음**.

**결론**: LLM은 **ALFP의 ForecastPlanner / Validation / Decision** 세 에이전트에서만 사용됨. **Mesa 파이프라인(Step 2~5)** 에서는 **어느 단계에서도 LLM을 사용하지 않음**.

---

## 3. 파이프라인 단계별 구현 여부

### 전체 요약

```
Mesa Simulation → Step 2 State Translator → Step 3 Multi-Agent Decision
       ✅                    ✅ (신규)                ✅ (신규 5개 에이전트)
                                                        ↓
              Step 4 Action Execution ← decisions
                        ✅ 구현됨
                                                        ↓
              ExecutionResult (summary, dataframe) → Step 5 Evaluation
                                                        ✅ (신규)
```

### Step 2 — State Translator

| PRD 요구사항 | 구현 여부 | 비고 |
|--------------|-----------|------|
| Mesa DataCollector에서 상태 추출 | ✅ | `simulation/state_translator.py` — translate_model_state() |
| 고차원 데이터 압축·LLM용 JSON 생성 | ✅ | community_state / market_state / ess_state JSON, PRD 스펙과 동일 |
| human-readable 요약 | ✅ | generate_summary() — 자연어 요약 텍스트 생성 |
| DataFrame 후처리 변환 | ✅ | translate_dataframe() — DataCollector 출력 DataFrame → state list |

**파일**: `simulation/state_translator.py`

---

### Step 3 — Multi-Agent Decision Engine

| PRD 요구사항 | 구현 여부 | 비고 |
|--------------|-----------|------|
| SmartSeller-Agent | ✅ | `SmartSellerAgent` — 잉여 에너지 bid_price/bid_quantity 결정 |
| StorageMaster-Agent | ✅ | `StorageMasterAgent` — TOU+피크 기반 charge/discharge/idle 결정 |
| EcoSaver-Agent | ✅ | `EcoSaverAgent` — 피크 초과 시 DR 권고 생성 |
| MarketCoordinator-Agent | ✅ | `MarketCoordinatorAgent` — 에이전트 충돌 조율, 최종 decisions 생성 |
| Policy-Agent | ✅ | `PolicyAgent` — ESS/거래/DR 제약 검증 및 클램핑 |
| 오케스트레이터 | ✅ | `run_multi_agent_decision()` / `run_multi_agent_decision_series()` |

**파일**: `simulation/multi_agent_decision.py`
**비고**: AgentScope 대신 독립 클래스 구조로 구현 (ALFP DecisionAgent와 병행 운용 가능)

---

### Step 4 — Action Execution Engine

| PRD 요구사항 | 구현 여부 | 비고 |
|--------------|-----------|------|
| Agent Proposal → Policy Validation → Coordinator Approval → Mesa Update | ✅ | `seapac_agents/execution.py`에서 구현 |
| TradeAction / ESSAction / DemandResponseAction | ✅ | `execution.py`에 타입·검증·Mesa 반영 |
| decisions를 Mesa에 적용 | ✅ | `ALFPSimulationModel(alfp_decisions=...)` + `run_execution()` |
| CLI로 실행 단계 수행 | ✅ | `simulation/run_execution.py` (--use-alfp, --decisions-file 등) |

**결론**: Step 4는 **PRD대로 구현됨**.

---

### Step 5 — Evaluation Engine

| PRD 요구사항 | 구현 여부 | 비고 |
|--------------|-----------|------|
| Energy Cost | ✅ | 계통 구매 총 비용 (deficit_kw × dt × 단가) |
| Trading Profit | ✅ | P2P 거래 수익·절감액 집계 |
| Peak Reduction | ✅ | 기준 피크 대비 감소율 (%) 계산 |
| ESS Degradation Cost | ✅ | 총 방전량 × 사이클 비용 (원/kWh) |
| User Acceptance | ✅ | DR 이벤트 수·수락율 (기본값 75%) |
| 종합 등급 산정 | ✅ | A/B/C/D 등급 자동 산정 |
| EvaluationReport | ✅ | KPI dict + 자연어 요약 + to_dict() / print_report() |

**파일**: `simulation/evaluation.py`

---

## 4. 요약 표

| 단계 | PRD 명칭 | 구현 여부 | 비고 |
|------|----------|-----------|------|
| - | Mesa Simulation | ✅ | ALFPSimulationModel, run_simulation.py |
| 2 | State Translator | ✅ | simulation/state_translator.py |
| 3 | Multi-Agent Decision | ✅ | simulation/multi_agent_decision.py (5개 에이전트) |
| 4 | Action Execution | ✅ | seapac_agents/execution.py, simulation/run_execution.py |
| 5 | Evaluation Engine | ✅ | simulation/evaluation.py (5개 KPI) |

**전체 파이프라인 통합 CLI**: `simulation/run_agentic_pipeline.py`

---

## 5. 구현 완료 사항

- **전 단계 구현 완료** (Step 2~5)
- **Step 2**: PRD 예시 JSON과 동일한 스키마 (`time`, `community_state`, `market_state`, `ess_state`)
- **Step 3**: PRD 명세 5개 에이전트 모두 구현 (`SmartSeller`, `StorageMaster`, `EcoSaver`, `MarketCoordinator`, `Policy`)
- **Step 4**: 기존 구현 유지 (변경 없음)
- **Step 5**: PRD 5개 KPI 모두 계산, 등급 산정 포함
- **AgentScope**: 미도입 유지 — 독립 Python 클래스로 동일 역할 구현

이 문서는 PRD 대비 구현 상태를 기록한 점검 결과입니다.
