# ALFP 디렉토리 분석과 LangGraph DeepAgent 필요성

## 1. ALFP 디렉토리 구조 요약

`alfp`는 단순 예측 모델 모음이 아니라, 예측부터 운영 의사결정과 사후 저장까지 연결하는 에이전트형 파이프라인이다.

- `alfp/pipeline`: LangGraph 상태 그래프와 실행 진입점
- `alfp/agents`: 데이터 품질, 피처 생성, 예측, 검증, 의사결정 에이전트
- `alfp/governance`: evidence curator, critic, policy gate
- `alfp/memory`: 영구 메모리와 strategy memory
- `alfp/simulation_sandbox`: 실행 전 전략 검증
- `alfp/skills`: ESS 최적화, 요금 분석, 예측 보조 로직
- `alfp/tools`: 외부 도구 연동, 예: OpenWeather

즉, 구조 자체는 이미 "멀티 스텝 에이전트 시스템"을 지향하고 있다.

## 2. 현재 ALFP의 실제 동작 방식

핵심 실행 흐름은 [alfp/pipeline/graph.py](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py#L34) 에 정의되어 있다.

`data_loader -> data_quality -> feature_engineering -> forecast_planner -> load_forecast -> pv_forecast -> net_load_forecast -> validation -> decision -> evidence_curator -> critic_agent -> policy_gate -> simulation_sandbox -> save_memory`

중간에 두 개의 재진입 루프가 있다.

- 검증 KPI가 미달이면 `replan -> forecast_planner`로 재계획한다. [alfp/pipeline/graph.py](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py#L63)
- 정책 게이트가 `REPLAN_REQUIRED`를 내리면 다시 `replan`으로 돌아간다. [alfp/pipeline/graph.py](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py#L156)

공유 상태도 꽤 넓다. 예측 결과, 검증 지표, 의사결정, 거버넌스 산출물, 시뮬레이션 결과, 메모리가 모두 한 상태에 축적된다. [alfp/agents/state.py](/Users/a09206/work/ai_master_2603_ai/alfp/agents/state.py)

따라서 ALFP는 이미 "LangGraph가 필요한" 유형의 문제를 다루고 있다. 단일 함수 호출이나 단발성 LLM 질의로는 관리하기 어렵다.

## 3. 그런데 왜 DeepAgent가 추가로 필요한가

현재 구조는 LangGraph 기반의 "오케스트레이션"은 갖고 있지만, 각 노드 내부의 추론은 아직 얕다. 즉, 그래프는 깊지만 에이전트의 사고는 아직 깊지 않다.

### 3.1 ForecastPlanner가 여전히 1회성 판단에 가깝다

[alfp/agents/forecast_planner.py](/Users/a09206/work/ai_master_2603_ai/alfp/agents/forecast_planner.py#L126) 를 보면 Planner는 요약 통계, 날씨, 이전 검증 결과를 모아 한 번 LLM을 호출하거나 실패 시 규칙 기반 fallback으로 끝낸다.

한계는 명확하다.

- 모델 후보를 단계적으로 비교하지 않는다.
- horizon 변경, feature 전략 변경, 데이터 분할 전략 변경을 독립 하위 과제로 분해하지 않는다.
- 재계획도 사실상 `lgbm <-> xgboost` 전환 수준이다. [alfp/agents/forecast_planner.py](/Users/a09206/work/ai_master_2603_ai/alfp/agents/forecast_planner.py#L52)

DeepAgent가 필요한 이유는 Planner가 "무슨 모델을 쓸까"를 한 번 답하는 수준이 아니라, 아래처럼 다단계 검토를 해야 하기 때문이다.

- 데이터 특성 해석
- 후보 전략 생성
- 후보별 리스크 비교
- 실패 원인 가설 생성
- 재실험 계획 수립
- 가장 설명 가능한 계획 채택

이건 단일 프롬프트보다 하위 작업 분해와 중간 검증이 강한 DeepAgent 쪽이 맞다.

### 3.2 DecisionAgent는 계산은 규칙 기반, LLM은 설명 보강 수준이다

[alfp/agents/decision.py](/Users/a09206/work/ai_master_2603_ai/alfp/agents/decision.py#L132) 를 보면 실제 의사결정의 중심은 다음 규칙 로직이다.

- ESS 스케줄은 `ESSOptimizationSkill`
- 거래 권고는 임계값 기반 surplus 탐색
- DR은 peak threshold 초과분 기반 계산
- 비용 절감은 `TariffAnalysisSkill`

그리고 LLM은 마지막에 전략 문구를 생성한다. [alfp/agents/decision.py](/Users/a09206/work/ai_master_2603_ai/alfp/agents/decision.py#L194)

즉 현재는:

- "행동 생성"은 규칙 기반
- "행동 설명"은 LLM 기반

구조다.

하지만 실제 운영 환경에서는 다음이 필요하다.

- 여러 ESS/거래/DR 조합안을 동시에 만들기
- 수익, 리스크, 정책 위반 가능성, 배터리 열화 비용을 비교하기
- short horizon, day ahead, 이상상황 대응 모드를 구분하기
- 프로슈머 타입별 전략을 다르게 세우기

이런 다목적 최적화는 단일 규칙 체인보다 DeepAgent형 탐색이 적합하다.

### 3.3 Governance는 존재하지만, 검토 깊이는 아직 제한적이다

거버넌스 체인은 분명히 잘 분리되어 있다.

- Evidence Curator: 근거 구조화 [alfp/governance/evidence_curator.py](/Users/a09206/work/ai_master_2603_ai/alfp/governance/evidence_curator.py)
- Critic Agent: 리스크와 실패 시나리오 [alfp/governance/critic_agent.py](/Users/a09206/work/ai_master_2603_ai/alfp/governance/critic_agent.py)
- Policy Gate: 승인/거절/재계획 [alfp/governance/policy_gate.py](/Users/a09206/work/ai_master_2603_ai/alfp/governance/policy_gate.py)
- Sandbox: 실행 전 추정 [alfp/simulation_sandbox/sandbox.py](/Users/a09206/work/ai_master_2603_ai/alfp/simulation_sandbox/sandbox.py)

하지만 실제 구현을 보면:

- Evidence confidence는 KPI pass 여부에 크게 의존한다.
- Critic은 low confidence와 ESS 횟수 정도를 중심으로 본다.
- Sandbox는 기본적으로 rule-based estimate다.
- Policy Gate도 `parallel_agents`가 없으면 최소 규칙 검사 수준이다.

즉 파이프라인은 깊지만, 각 심사 단계의 실질적인 탐색 폭은 아직 좁다.

DeepAgent가 필요한 이유는 여기서 "의사결정 후보 생성 -> 반례 탐색 -> 정책 점검 -> 시뮬레이션 비교 -> 수정안 재제안"의 반복을 더 깊게 해야 하기 때문이다.

### 3.4 Memory가 저장은 하지만, 적극적으로 학습 전략을 조립하지는 않는다

[alfp/memory/strategy_memory.py](/Users/a09206/work/ai_master_2603_ai/alfp/memory/strategy_memory.py) 는 이미 전략 결과를 JSONL로 축적하고, 성과에 따라 전략 weight를 미세 조정하는 구조를 갖고 있다.

즉 "무엇이 있었는가"를 기록하는 메모리는 있다. 하지만 아직 "그 기록을 어떻게 다시 써서 더 나은 계획을 만들 것인가"까지는 가지 못한다.

현재 가능한 일은 주로 다음 수준에 머문다.

- 최근 전략과 결과를 저장한다.
- 성공/실패 신호를 반영해 weight를 소폭 조정한다.
- Planner 재호출 시 일부 최근 문맥을 참고한다.

반면 아직 부족한 것은 사례 기반 재사용과 구조적 retrieval이다.

- 현재 상황과 유사한 과거 케이스를 검색해 전략을 재조합하는 기능
- 계절, 날씨, tariff, prosumer type별로 축적된 best practice를 retrieval하는 기능
- "이번 실패가 과거 어떤 실패 패턴과 닮았는가"를 판별해 재계획에 반영하는 기능

즉 지금의 memory는 로그와 약한 preference 업데이트에 가깝고, 아직 planner와 decision이 활용할 수 있는 case-based reasoning 엔진은 아니다.

이 단계로 가려면 단순 저장소보다, 메모리 검색, 실패 패턴 비교, 전략 후보 재조합을 반복적으로 수행하는 DeepAgent형 구조가 훨씬 자연스럽다.

## 4. 정리하면: ALFP에는 LangGraph가 이미 맞고, DeepAgent가 필요한 이유는 내부 추론 심화를 위해서다

ALFP는 이미 다단계 상태 전이, 재계획, 거버넌스, 메모리, 사후 저장을 포함하므로 LangGraph 사용 자체는 타당하다. 실제 코드도 그 방향으로 작성되어 있다. [alfp/pipeline/graph.py](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py#L299)

반면 DeepAgent가 필요한 이유는 다음 네 가지다.

1. Planner를 단발 모델 선택기에서 다단계 실험 설계자로 바꾸기 위해
2. Decision을 단일 규칙 실행기에서 다목적 전략 탐색기로 바꾸기 위해
3. Governance를 형식적 후처리에서 실질적 반례 탐색 루프로 강화하기 위해
4. Memory를 저장소에서 사례 기반 전략 재사용 엔진으로 바꾸기 위해

즉 현재 ALFP는 "에이전트 파이프라인의 뼈대"는 있다. 그러나 복잡한 에너지 운영 문제를 풀기 위한 "깊은 추론 에이전트"는 아직 부족하다. 그래서 `LangGraph + DeepAgent` 조합이 필요한 것이다.

## 5. 한 문장 결론

`alfp`는 이미 LangGraph로 관리해야 할 만큼 상태와 분기가 복잡한 시스템이고, 여기에 DeepAgent가 필요한 이유는 각 노드가 아직 단발 판단과 규칙 기반 계산에 머물러 있어서, 실제 운영 수준의 후보 탐색, 반례 검토, 사례 재사용, 재계획 심화를 수행하기 어렵기 때문이다.
