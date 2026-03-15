# Mesa Simulation이 현재 시스템에서 사용된 의미

이 문서는 **Mesa 시뮬레이션(ALFPSimulationModel)**이 이 코드베이스에서 어떤 역할을 하고, 어디서 호출되며, 입력·출력이 어떻게 이어지는지 정리합니다.

---

## 1. Mesa가 무엇인가 (역할 정의)

현재 시스템에서 Mesa는 다음을 동시에 만족합니다.

| 의미 | 설명 |
|------|------|
| **커뮤니티 디지털 트윈** | 단지 내 프로슈머(부하·PV)·공유 ESS·P2P 시장을 15분 단위로 시뮬레이션하는 **단일 실행 모델** |
| **결정 실행 엔진** | `ess_schedule`, `trading_recommendations`, `demand_response_events` 같은 **결정(decisions)**을 받아 스텝별로 적용하고, 그에 따른 부하·SoC·거래량·절감액 등을 **시계열(DataFrame)**로 반환 |
| **상태 생성기** | 실행 결과 DataFrame을 downstream에서 **State Translator**가 소비해, LLM 에이전트용 **state JSON 리스트**를 만드는 원천 데이터 |
| **KPI·평가 데이터 소스** | 실행 결과의 `community_load_kw`, `community_net_kw`, `ess_soc_pct`, `market_matched_kw` 등이 **Step5 Evaluation**에서 비용·피크 감소·ESS 마모·수용도 등 KPI 계산에 사용됨 |

즉, **“에이전트/ALFP가 내린 결정을, 실제 설비가 있다고 가정하고 24시간(96스텝) 돌렸을 때 어떤 시계열과 요약 통계가 나오는지”**를 계산하는 **공통 실행·평가 레이어**입니다.

---

## 2. 시스템 내 호출 위치 (어디서 쓰이는가)

### 2.1 통합 파이프라인(`run_full_pipeline.py`) — 두 번 사용

전체 파이프라인에서 Mesa는 **서로 다른 입력(decisions)**으로 **두 번** 호출됩니다.

```
[ALFP] → alfp_decisions
    ↓
[MESA #1] ALFPSimulationModel(alfp_decisions=alfp_decisions).run()
    ↓
    df (시계열) → [Step2 State Translator] → state_json_list
    ↓
[Step3 Multi-Agent Decision] (AgentScope / CDA) → decisions
    ↓
[Step4 Execution] run_execution(decisions) → 내부에서
[MESA #2] ALFPSimulationModel(alfp_decisions=decisions).run()
    ↓
    ExecutionResult (dataframe, summary) → [Step5 Evaluation] 및 결과 저장
```

- **Mesa #1**  
  - **입력**: ALFP 단계에서 나온 `alfp_decisions` (ESS 스케줄 등).  
  - **역할**: ALFP 전략을 적용한 **가상 24시간**을 돌려, 커뮤니티 부하·PV·Net Load·ESS SoC 등 **시계열(df)** 생성.  
  - **출력 사용처**: 이 df를 **State Translator**가 받아 스텝별 **state JSON**으로 변환 → Step3 Multi-Agent Decision의 입력으로 사용.

- **Mesa #2**  
  - **입력**: Step3에서 나온 **최종 decisions** (정책 검증·Coordinator 승인 후).  
  - **호출 위치**: Step4 `stage_execution()` → `seapac_agents.execution.run_execution()` 또는 `cda.run_execution()` 내부.  
  - **역할**: “승인된 결정”을 적용한 **실행 시뮬레이션** 1회 수행.  
  - **출력 사용처**: `ExecutionResult.dataframe`, `ExecutionResult.summary`가 **Step5 Evaluation**의 비용·피크 감소·ESS·P2P·수용도 계산과 결과 저장(JSON/DB)에 사용됨.

정리하면, **같은 Mesa 모델**이  
- 한 번은 **ALFP 결정으로 상태 궤적 생성**,  
- 한 번은 **Step3 결정으로 “실행 결과” 생성**  
하는 두 가지 목적으로 쓰입니다.

### 2.2 Step4 Action Execution (`seapac_agents/execution.py`)

- **역할**: PRD의 “Agent Proposal → Policy Validation → Coordinator Approval → **Mesa Update**” 중 **Mesa Update** 구현.
- **흐름**:  
  1) decisions에서 ESS·Trade·DR 액션 추출 → 정책 검증 → 승인/거절  
  2) **ALFPSimulationModel(phase, data_path, n_steps, ess_*, alfp_decisions=decisions)** 생성  
  3) **model.run()** 호출 → DataFrame·summary 반환  
  4) `ExecutionResult(dataframe=..., summary=..., approved=..., validation_errors=...)` 로 반환
- **의미**: Mesa는 “검증된 결정을 적용한 **실제 실행 시뮬레이션**”을 수행하는 엔진이며, CDA 경로(`cda.settlement`)에서도 동일한 `run_execution`/Mesa 호출 구조를 사용합니다.

### 2.3 State Translator (`seapac_agents/state_translator.py`)

- **입력**: Mesa **실행 결과** DataFrame (`ALFPSimulationModel.run()` 반환값).  
  - 컬럼: `step`, `hour`, `community_load_kw`, `community_pv_kw`, `community_net_kw`, `avg_forecast_mape`, `ess_soc_pct`, `market_matched_kw` 등.
- **역할**:  
  - **translate_dataframe(df, ...)**: 행별(스텝별)로 `community_state`, `market_state`, `ess_state`를 만들어 **state JSON 리스트** 생성.  
  - **translate_model_state(model, ...)**: (선택) 실행 중인 Mesa **모델 인스턴스**에서 직접 현재 스텝 상태를 JSON으로 변환.
- **의미**: Mesa가 만든 **시계열이 곧 “에이전트가 볼 커뮤니티 상태 시퀀스”**의 원천입니다. 즉, Mesa 출력 없이는 Step3 LLM 에이전트용 state를 만들 수 없습니다.

### 2.4 Step5 Evaluation (`seapac_agents/evaluation.py`)

- **입력**: Step4 실행 결과 — `ExecutionResult.dataframe`, `ExecutionResult.summary` (Mesa `model.run()` + `model.summary()`).
- **사용 예**:  
  - **비용**: `community_net_kw`로 계통 구매량 계산 후 요금 적용.  
  - **피크 감소**: `community_load_kw`와 baseline 비교해 피크 수요 감소율 계산.  
  - **ESS**: `summary`의 `ess_total_discharged_kwh` 등으로 마모 비용·활용률.  
  - **P2P**: `summary`의 `total_matched_kwh`, `seller_revenue_krw`, `buyer_saving_krw` 등.
- **의미**: Mesa 실행 결과가 **전체 파이프라인의 최종 KPI·평가 리포트**의 입력이 됩니다.

### 2.5 Simulation Sandbox (`alfp/simulation_sandbox/sandbox.py`)

- **역할**: PRD §4.4 “실행 전 전략 검증” — plan → **simulate** → evaluate → execute 중 **simulate** 단계.
- **현재 설정**: `run_simulation_sandbox(..., use_mesa=False)` (기본값).  
  - `use_mesa=False` → Mesa를 호출하지 않고, decisions·net_load_forecast 기반 **규칙 추정**(peak_load, battery_degradation, predicted_cost, expected_profit)만 수행.  
  - `use_mesa=True` 시에는 `seapac_agents.execution.run_execution(decisions, ...)`를 호출해 **Mesa 실행 1회**로 동일한 결과를 낼 수 있음.
- **의미**: Sandbox는 “전략 검증용 시뮬”을 담당하며, 선택적으로 **Mesa를 그 검증 엔진으로 사용**할 수 있는 구조입니다. 현재는 규칙 기반만 사용 중입니다.

### 2.6 독립 실행 (`simulation/run_simulation.py`, `simulation/run_execution.py`)

- **run_simulation.py**: Phase(1~4)·스텝·프로슈머·데이터 경로를 인자로 받아 **Mesa만** 실행. ALFP 연동 옵션(`--use-alfp`)으로 `alfp_decisions`를 넣어 볼 수 있음.  
  → **테스트·Phase 비교·데이터 검증**용.
- **run_execution.py**: ALFP 파이프라인에서 생성한 decisions를 읽어와 **run_execution** 한 번 호출.  
  → **실행 단계만** 재현할 때 사용.

### 2.7 CDA Settlement (`cda/settlement.py`)

- **역할**: CDA 경로에서 정산·실행 시, 내부적으로 **동일한 실행 엔진**(`run_execution` → Mesa)을 사용한다고 문서화되어 있음.  
  → Mesa는 “SEAPAC 에이전트 실행”과 “CDA 실행”이 **공통으로 쓰는 실행 시뮬레이션**입니다.

---

## 3. 데이터 흐름 요약

```
decisions (ALFP 또는 Step3)
        │
        ▼
┌───────────────────────────────────────┐
│  ALFPSimulationModel(                │
│    alfp_decisions=decisions,           │
│    data_path, n_steps, phase, ESS…    │
│  ).run()                              │
└───────────────────────────────────────┘
        │
        ├─→ DataFrame (시계열)
        │       │
        │       ├─→ State Translator → state_json_list → Step3 (첫 번째 Mesa인 경우)
        │       │
        │       └─→ Evaluation (비용·피크·ESS·P2P 등) (두 번째 Mesa인 경우)
        │
        └─→ model.summary() → ExecutionResult.summary
                │
                └─→ Evaluation, 대시보드, 결과 저장
```

---

## 4. 한 문장으로 정리

**Mesa 시뮬레이션은 “에이전트/ALFP가 낸 결정(ESS·거래·DR)을 15분×96스텝(24시간) 동안 적용했을 때의 커뮤니티 시계열과 요약 통계를 계산하는 **공통 실행·평가 엔진**이며, 그 결과는 (1) State Translator를 통해 Step3 에이전트 입력(state JSON)을 만들고, (2) Step4 실행 결과로 다시 한 번 Mesa를 돌려 Step5 평가와 저장에 사용된다.**  

또한 Simulation Sandbox는 선택적으로 이 같은 Mesa를 “전략 검증용 시뮬”으로 쓸 수 있고, 독립 스크립트와 CDA는 동일한 Mesa 기반 실행을 각각 테스트·정산 경로에서 재사용합니다.
