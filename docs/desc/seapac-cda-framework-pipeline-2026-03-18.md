# seapac_agents / cda 프레임워크 및 파이프라인 설명 (2026-03-18)

## 1. 전체 해석부터 말하면

`seapac_agents`와 `cda`는 서로 별개 프로젝트라기보다 하나의 운영 파이프라인 안에서 역할이 다른 두 계층이다.

- `seapac_agents`: 상위 에이전트 의사결정, 실행, 평가를 담당하는 오케스트레이션 계층
- `cda`: 전력 거래 시장 메커니즘을 담당하는 전문 시장 계층

더 정확히 말하면 현재 설명 기준 구조는 다음에 가깝다.

`Forecast / State -> seapac_agents.decision 또는 cda 시장 계층 -> execution/settlement -> evaluation`

즉 `seapac_agents`는 "운영 파이프라인의 뼈대", `cda`는 그중 거래 시장 파트를 더 정교하게 구현한 "시장 엔진"이다.

## 2. 디렉토리별 역할

### 2.1 seapac_agents

`seapac_agents`는 현재 실질적으로 Step 3~5 중심의 운영 계층이다.

- Step 3: `decision.py`
- Step 3-P: `agent_planner.py`
- Step 4: `execution.py`
- Step 5: `evaluation.py`
- 보조 계층: `self_critic.py`
- 보조/유산 유틸리티: `state_translator.py`
- 실행 진입점: `run_agentic_pipeline.py`

즉 이 디렉토리는 "입력 상태 또는 예측 결과를 보고, 어떻게 판단하고, 어떻게 실행하고, 어떻게 평가할 것인가"를 다루는 에이전트 레이어다.

### 2.2 cda

`cda`는 Continuous Double Auction 기반 시장 메커니즘을 구현한다.

- `orderbook.py`: Bid/Ask 저장
- `matching.py`: 가격 우선 매칭 엔진
- `buyer.py`: 부족 전력 기준 구매 입찰 생성
- `strategy_agent.py`: 전략 초안 생성
- `negotiation.py`: 에이전트 제안 협상
- `online_pricing.py`: 체결 결과 기반 가격 조정
- `coordinator.py`: 전체 시장 조율
- `settlement.py`: 실행 단계 연결

즉 이 디렉토리는 "누가 얼마에 사고팔지, 어떻게 체결할지"를 중심으로 한 시장 레이어다.

## 3. 실제로 사용된 핵심 프레임워크

코드 기준으로 확인되는 핵심 프레임워크는 다음과 같다.

- `AgentScope`: 멀티에이전트 정의와 메시지 기반 상호작용
- `LangChain Core`: 일부 LLM 프롬프트 호출과 출력 파싱
- `Azure OpenAI / OpenAI 경유 LLM 래퍼`: `alfp.llm`을 통해 간접 사용
- `Mesa`: 실행 결과를 반영하는 시뮬레이션 환경
- `Pandas`: 실행 결과 및 KPI 계산
- `dataclasses`: 액션/리포트/계획 스키마 정의
- `JSON 기반 규칙 엔진`: state payload, plan, proposal 직렬화

중요한 점은 이 파이프라인이 프레임워크 하나에 완전히 의존하지 않는다는 것이다.

- 에이전트 협업은 `AgentScope`
- 일부 고차 판단과 계획 수립은 `LangChain Core + LLM`
- 실제 물리/운영 실행 검증은 `Mesa`
- 시장 매칭은 커스텀 `CDA` 엔진

## 4. seapac_agents에서 AgentScope를 어떻게 쓰는가

`seapac_agents/decision.py`는 이 디렉토리의 핵심이다. 파일 주석에도 명시돼 있듯 Step 3 Multi-Agent Decision Engine은 AgentScope 기반이다.

실제로 사용된 요소는 다음과 같다.

- `agentscope`
- `agentscope.agent.AgentBase`
- `agentscope.message.Msg`
- `agentscope.pipeline.MsgHub`

이 구조에서 각 에이전트는 `AgentBase`를 상속한다. 대표 에이전트는 다음과 같다.

- `PolicyAgentAS`
- `SmartSellerAgentAS`
- `StorageMasterAgentAS`
- `EcoSaverAgentAS`
- `MarketCoordinatorAgentAS`

즉 AgentScope는 여기서 "에이전트 정의 프레임워크"이자 "메시지 전달 프레임워크"로 쓰인다.

### 4.1 AgentScope의 실제 사용 방식

이 프로젝트는 AgentScope를 단순 채팅 프레임워크처럼 쓰지 않는다. 더 구체적으로는 다음 세 가지 역할로 쓴다.

1. 역할 기반 페르소나 주입
2. `Msg` 객체로 제안 전달
3. `MsgHub`로 상태 브로드캐스트

예를 들어 `PolicyAgentAS`는 제약 조건 강제 역할을, `SmartSellerAgentAS`는 잉여 에너지 판매 역할을, `StorageMasterAgentAS`는 ESS 운영 역할을 맡는다. 각 에이전트는 자연어 `sys_prompt`를 받지만, 결과는 실제 실행 가능한 `metadata.proposal` 형태로 반환된다.

즉 이 구조는 "자연어 역할 부여 + 구조화된 제안 반환"이라는 하이브리드 방식이다.

## 5. seapac_agents 파이프라인을 단계별로 보면

### 5.1 입력 상태 계층: Forecast / State

현재 파이프라인 설명에서 가장 중요한 변경점은, 예전처럼 `Mesa 상태 -> State Translator`를 필수 시작점으로 두지 않는다는 점이다.

대시보드 템플릿의 CDA 흐름도도 다음처럼 표현한다.

`Forecast / State -> Strategy Agent (LLM) -> Negotiation Layer -> Policy / Trust -> CDA Market -> Settlement`

즉 현재 문맥에서 `seapac_agents`와 `cda`는 이미 준비된 상태 입력을 받는다고 보는 편이 맞다. 그 상태 입력은 다음처럼 해석할 수 있다.

- 예측 결과에서 온 상태
- 외부에서 조립된 운영 상태 JSON
- 시뮬레이션에서 파생된 상태

`state_translator.py`는 여전히 코드베이스에 남아 있지만, 문서상 핵심 파이프라인의 필수 첫 단계로 보는 것은 맞지 않다. 현재는 "입력 상태를 표준 JSON으로 다루는 보조 유틸리티" 정도로 보는 편이 정확하다.

### 5.2 Step 3: Multi-Agent Decision Engine

`seapac_agents/decision.py`에서는 여러 AgentScope 에이전트가 상태를 보고 각자 제안을 만든다.

파일 상단 설명대로 기본 흐름은 다음과 같다.

`Input State -> MsgHub broadcast -> [Policy, Seller, Storage, EcoSaver] 제안 -> MarketCoordinator 조율 -> decisions dict`

각 에이전트의 역할은 분명하다.

- `Policy`: 물리/운영 제약 확인
- `SmartSeller`: 잉여 전력 판매 전략
- `StorageMaster`: ESS 충방전 전략
- `EcoSaver`: DR 절감 권고
- `MarketCoordinator`: 충돌 조정

즉 Step 3은 하나의 거대한 모델이 모든 결정을 다 하는 것이 아니라, 역할을 쪼갠 뒤 마지막에 coordinator가 합치는 구조다.

### 5.3 Step 3-P: Agent Plan

`seapac_agents/agent_planner.py`는 AgentScope 의사결정 위에 추가된 "계획 오케스트레이션 계층"이다.

이 파일이 중요한 이유는, 단순히 누가 무슨 결정을 했는지가 아니라 "어떤 순서로 실행해야 하는가"를 다루기 때문이다.

여기서 정의된 실행 규칙은 명확하다.

- `policy`는 항상 첫 단계
- `trading`, `storage`, `eco_saver`는 policy 이후 병렬 가능
- `simulate`는 마지막

이 계층은 두 가지 방식으로 동작한다.

- 규칙 기반 계획 생성
- LLM 기반 계획 생성 및 실패 시 계획 재수립

즉 Agent Plan은 전형적인 워크플로 엔진 역할을 하며, AgentScope 위에 한 단계 더 높은 orchestration 레이어를 추가한다.

### 5.4 Step 4: Execution Engine

`seapac_agents/execution.py`는 Step 3 출력인 `decisions`를 실제 실행 가능한 액션으로 바꾼다.

여기서 사용하는 구조는 dataclass 기반이다.

- `ESSAction`
- `TradeAction`
- `DemandResponseAction`
- `ExecutionResult`

이 파일의 핵심 설계는 주석에도 나와 있듯 `execute -> simulate -> approve`다.

즉 순서는 이렇다.

1. decisions를 액션 리스트로 변환
2. 정책 검증
3. Mesa 시뮬레이션 실행
4. 결과 기반 승인 여부 결정

즉 이 프로젝트는 LLM이 "하라"고 말하면 바로 실행하는 구조가 아니라, 반드시 시뮬레이션을 한 번 거쳐 승인하는 안전 설계를 갖고 있다.

### 5.5 Step 5: Evaluation Engine

`seapac_agents/evaluation.py`는 실행 결과를 KPI로 바꾼다.

여기서 계산하는 KPI는 다음과 같다.

- Energy Cost
- Trading Profit
- Peak Reduction
- ESS Degradation Cost
- User Acceptance
- Execution Quality
- Operational Value

즉 이 계층은 "좋아 보이는 전략"을 "숫자로 검증된 전략"으로 바꾸는 평가 계층이다.

### 5.6 Self-Critic

`seapac_agents/self_critic.py`는 별도 보조 프레임워크라기보다, Step 3 결과를 같은 LLM 시각에서 다시 반박하게 만드는 메타 검토 계층이다.

이 모듈은 두 방식으로 동작한다.

- 규칙 기반 반박
- `LangChain Core` 메시지 + JSON 파서를 통한 LLM 기반 반박

즉 Self-Critic은 직접 실행 엔진은 아니지만, 리스크 노출과 후속 정책 게이트 입력에 쓰일 수 있는 감시 계층이다.

## 6. cda는 무엇을 추가하는가

기본 `seapac_agents` 구조에도 거래/조정 개념은 있지만, `cda`는 그 부분을 더 시장 중심으로 바꾼다.

즉 `MarketCoordinator`가 정적으로 결론을 내리는 대신, `CDA 시장` 안에서 매수/매도 호가를 만들고 실제로 체결시키는 방식이다.

이 점이 두 디렉토리의 가장 큰 차이다.

- `seapac_agents` 기본 decision: 에이전트 제안 조정 중심
- `cda`: 오더북, 매칭, 체결, 가격 피드백 중심

## 7. cda 내부 파이프라인

### 7.1 OrderBook

`cda/orderbook.py`는 매우 단순한 오더북 구조를 제공한다.

- `Bid`
- `Ask`
- `OrderBook`

프레임워크 관점에서 보면 별도 외부 라이브러리를 쓴 것이 아니라, dataclass 기반 커스텀 시장 자료구조를 직접 구현했다.

즉 이 계층은 거래소의 메모리 내 테이블 역할이다.

### 7.2 Matching Engine

`cda/matching.py`는 Continuous Double Auction 매칭 규칙을 구현한다.

규칙은 전형적이다.

- Bid 가격 내림차순 정렬
- Ask 가격 오름차순 정렬
- `highest bid >= lowest ask`이면 체결
- 거래 가격은 `(bid + ask) / 2`

즉 `cda`는 LLM이 가격을 정한다고 끝나는 구조가 아니라, 그 가격을 실제 시장 규칙에 넣어 체결 가능성을 판단한다.

### 7.3 Buyer Generator

`cda/buyer.py`는 부족 전력 기준으로 구매 입찰을 만든다.

여기서는 다음 요소가 반영된다.

- `deficit_energy`
- `peak_risk`
- `grid_price`
- 과거 체결 피드백 기반 `adjust_price()`

즉 매수 측도 단순 고정 가격이 아니라, 상태와 과거 시장 피드백을 반영한 적응형 입찰을 한다.

### 7.4 Online Pricing

`cda/online_pricing.py`는 체결/미체결 결과를 `memory_store/market_feedback.json`에 저장하고 다음 라운드 가격을 미세 조정한다.

이 모듈은 간단하지만 중요하다.

- 자주 미체결되는 buyer는 가격을 조금 올린다.
- 자주 미체결되는 seller는 가격을 조금 내린다.
- 자주 체결되는 쪽은 반대로 과도한 가격을 완화하거나 약간 강화한다.

즉 `cda`는 완전 정적 규칙 엔진이 아니라, 매우 가벼운 온라인 학습 성격도 갖고 있다.

### 7.5 Strategy Agent

`cda/strategy_agent.py`는 CDA 시장에 들어가기 전 전략 초안을 만든다.

여기서 중요한 프레임워크 조합은 다음과 같다.

- 규칙 기반 전략 생성
- `LangChain Core` 메시지 객체 사용
- `alfp.llm`을 통한 LLM 호출

Strategy Agent는 다음 제안을 구조화해서 낸다.

- seller action / bid price / quantity
- storage action / power
- buyer side suggestion
- DR suggestion
- reasoning log

즉 이 모듈은 "시장에 들어가기 전 전략 브리핑" 역할이다.

### 7.6 Negotiation Layer

`cda/negotiation.py`는 Strategy Agent 초안과 AgentScope 에이전트 제안을 한 번 더 조정한다.

흐름은 다음과 같다.

1. Strategy Agent 초기 제안
2. SmartSeller / StorageMaster / EcoSaver 제안 수집
3. Policy 검증
4. 충돌 해결
5. 합의안 생성

즉 이 계층은 LLM 전략 초안이 곧바로 시장으로 가는 것을 막고, 기존 에이전트 제안과 정책 제약을 함께 반영하게 만드는 협상 계층이다.

### 7.7 CDA Coordinator

`cda/coordinator.py`는 `cda`의 핵심 오케스트레이터다.

이 파일이 하는 일은 다음을 한 번에 묶는 것이다.

- SmartSeller/Storage/EcoSaver 제안 수집
- Policy 검증
- 피크 시 ESS 우선 같은 충돌 해결
- OrderBook 구성
- Bid/Ask 생성
- `match_cda()` 실행
- 체결 결과를 `decisions` 형식으로 변환

즉 Coordinator는 `seapac_agents.execution`과 호환되도록 시장 결과를 다시 상위 파이프라인 형식으로 번역한다.

이 점이 중요하다. `cda`는 완전히 독립된 앱이 아니라, `seapac_agents`에 꽂아 넣을 수 있는 플러그인형 시장 엔진으로 설계돼 있다.

### 7.8 Settlement

`cda/settlement.py`는 새로운 실행 엔진을 전부 다시 쓰지 않는다. 대신 `seapac_agents.execution.run_execution()`을 내부적으로 재사용한다.

즉 구조는 다음과 같다.

- 거래 시장 해석은 `cda`
- 물리 실행 검증과 Mesa 연동은 `seapac_agents.execution`

이 설계는 중복을 줄이고 인터페이스를 유지하는 데 유리하다.

## 8. run_agentic_pipeline.py 기준 전체 실행 흐름

실제 파이프라인 진입점은 `seapac_agents/run_agentic_pipeline.py`다. 현재 코드와 `pipeline_dashboard/templates/run_detail.html` 기준으로 보면 흐름은 크게 네 갈래 모드가 있다.

### 8.1 기본 AgentScope 모드

- `run_agentscope_decision_series()`
- Step 4 `seapac_agents.execution.run_execution()`
- Step 5 `evaluate_from_execution_result()`

즉 순수 `seapac_agents` 모드다. 이때 문서상 시작 입력은 `State Translator`가 아니라 이미 구성된 `state_json_list` 또는 forecast/state payload로 보는 편이 현재 설명과 맞다.

### 8.2 CDA 시장 모드

- AgentScope 에이전트 생성
- `cda.run_cda_decision_series_with_agents()`
- Step 4 `cda.run_execution()` 호출
- 내부적으로 `seapac_agents.execution` 재사용
- Step 5 evaluation

즉 거래 의사결정만 CDA로 대체한 모드다.

### 8.3 CDA + Strategy Agent + Negotiation 모드

- AgentScope 에이전트 생성
- `run_cda_decision_series_with_agents_and_negotiation()`
- 내부에서 Strategy Agent -> Negotiation -> CDA 매칭
- settlement/execution
- evaluation

즉 가장 복합적인 시장 모드다. 대시보드의 CDA 설명도 바로 이 흐름을 중심으로 되어 있다.

### 8.4 Agent Plan / Parallel Layer 추가 모드

기본 decision 이후 다음을 추가할 수 있다.

- `Agent Plan`: policy -> trading/storage/eco 병렬 -> simulate 계획 수립
- `Parallel Layer`: Policy / Eco Saver / Storage 병렬 평가 후 승인/거절/권고

즉 `run_agentic_pipeline.py`는 단순 실행기라기보다 여러 에이전트 프레임워크 조합을 스위치로 전환하는 상위 orchestrator다.

## 9. 프레임워크 중심으로 다시 한 번 정리

### 9.1 AgentScope의 위치

AgentScope는 `seapac_agents`의 핵심 멀티에이전트 프레임워크다.

- 에이전트 정의
- 메시지 교환
- 페르소나 주입
- 브로드캐스트 기반 협업

즉 "누가 어떤 역할로 말하고 제안하는가"를 담당한다.

### 9.2 LangChain Core의 위치

LangChain Core는 전체 파이프라인을 지배하는 프레임워크는 아니고, LLM 호출이 필요한 일부 단계에서만 사용된다.

- `agent_planner.py`: LLM 계획 수립
- `self_critic.py`: 전략 반박
- `cda/strategy_agent.py`: 시장 전략 생성

즉 LangChain Core는 "LLM 메시지/출력 처리 부품" 역할이다.

### 9.3 Mesa의 위치

Mesa는 실행 후 검증을 맡는다.

- decisions를 바로 신뢰하지 않음
- 시뮬레이션 결과를 보고 승인
- KPI 평가는 시뮬레이션 결과 기반

즉 Mesa는 이 프로젝트의 현실성 검증 장치다.

### 9.4 CDA 엔진의 위치

`cda`는 외부 프레임워크라기보다 이 프로젝트가 직접 구현한 시장 프레임워크다.

- OrderBook
- Matching
- Buyer/Seller bid/ask 생성
- Online pricing feedback
- Negotiation

즉 거래 부분만 놓고 보면 작은 커스텀 거래소 엔진이라고 볼 수 있다.

## 10. pipeline_dashboard 기준으로 수정된 해석

`pipeline_dashboard`를 보면 현재 UI는 다음 두 가지 관점을 함께 사용한다.

- 실행/평가 쪽: `Mesa Execution Engine`, `Settlement`, `Evaluation`
- 시장 설명 쪽: `Forecast / State -> Strategy Agent -> Negotiation -> Policy / Trust -> CDA Market -> Settlement`

따라서 설명을 현재 기준으로 정리하면, 핵심 시작점은 더 이상 `Mesa 시뮬레이션 상태 -> State Translator`가 아니다. 현재의 중심 설명은 "예측 또는 외부 입력으로 준비된 상태"에서 시작해 시장/에이전트 계층으로 들어가는 구조다.

즉 지금 문서에서 가장 정확한 파이프라인 문장은 다음과 같다.

`Forecast / State -> AgentScope 기반 운영 의사결정 또는 CDA 시장 계층 -> Settlement/Execution -> Mesa 반영 -> Evaluation`

여기서 `state_translator.py`는 남아 있어도 파이프라인의 대표 출발점이라기보다, 상태 JSON을 만드는 보조 유틸리티 또는 이전 구조의 호환 계층으로 보는 편이 맞다.

## 11. 최종 해석

`seapac_agents`와 `cda`는 각각 이런 성격을 가진다.

- `seapac_agents`: AgentScope 기반 멀티에이전트 운영 파이프라인
- `cda`: 그 파이프라인 안에 삽입 가능한 Continuous Double Auction 시장 엔진

조합 구조를 가장 정확하게 표현하면 다음과 같다.

`입력 Forecast/State를 바탕으로 AgentScope 에이전트들이 운영 제안을 만들고, 거래 파트는 기본 Coordinator 또는 CDA 시장 엔진이 처리하며, 최종 액션은 Settlement/Execution을 거쳐 Mesa 반영 및 KPI 평가까지 이어지는 하이브리드 멀티에이전트 파이프라인`

즉 이 시스템의 핵심은 한 프레임워크에 의존하지 않고 다음을 층으로 나눠 쓴다는 점이다.

- 상위 멀티에이전트 협업: `AgentScope`
- 선택적 LLM 계획/전략 생성: `LangChain Core + LLM`
- 거래 메커니즘: `CDA` 커스텀 엔진
- 실행 검증: `Mesa`
- 평가: `Pandas + KPI 계산 로직`

## 12. 참고한 주요 파일

- `seapac_agents/run_agentic_pipeline.py`
- `seapac_agents/__init__.py`
- `seapac_agents/state_translator.py`
- `seapac_agents/decision.py`
- `seapac_agents/agent_planner.py`
- `seapac_agents/execution.py`
- `seapac_agents/evaluation.py`
- `seapac_agents/self_critic.py`
- `cda/__init__.py`
- `cda/coordinator.py`
- `cda/orderbook.py`
- `cda/matching.py`
- `cda/buyer.py`
- `cda/strategy_agent.py`
- `cda/negotiation.py`
- `cda/online_pricing.py`
- `cda/settlement.py`
