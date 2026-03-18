# ALFP에서 사용한 deepagents/MCP/프레임워크 설명 (2026-03-18)

## 1. ALFP 디렉토리에서 확인한 전체 구조

`alfp`는 하나의 단일 모델 프로젝트가 아니라, 전력 수요/태양광/순부하 예측과 운영 의사결정을 연결한 멀티에이전트 파이프라인이다. 디렉토리 구조를 보면 다음 계층으로 나뉜다.

- `agents/`: 데이터 품질, 특성 생성, 부하 예측, PV 예측, 순부하 예측, 검증, 의사결정, forecast planner 같은 실행 노드
- `pipeline/`: 전체 실행 순서를 정의하는 그래프 파이프라인
- `deepagents/`: LLM 기반 하위 에이전트 조합 로직
- `mcp/`: 의사결정 스킬을 외부 도구처럼 제공하는 MCP 서버/클라이언트
- `skills/`: ESS 최적화, 요금 분석, 에너지 예측 같은 도메인 스킬
- `models/`: XGBoost, LightGBM 예측 모델 래퍼
- `governance/`: evidence curator, critic, policy gate 같은 거버넌스 계층
- `memory/`: 전략 메모리, 벡터 스토어, 검색
- `storage/`: SQLite 저장소
- `simulation_sandbox/`: 전략 사전 검증
- `tools/`: 외부 날씨 연동 등 보조 도구
- `config/`: 프롬프트와 스킬 설정

즉 ALFP는 "예측 모델 프로젝트"라기보다, 예측 + 의사결정 + 거버넌스 + 메모리 + 도구 호출을 묶은 에이전트 시스템에 가깝다.

## 2. 핵심 프레임워크 요약

코드와 `requirements.txt` 기준으로 실제 사용된 핵심 프레임워크는 아래와 같다.

- `LangGraph`: 전체 멀티에이전트 실행 순서와 분기 제어
- `deepagents`: 하나의 단계 안에서 여러 LLM 서브에이전트를 역할별로 조합
- `LangChain Core`: tool 데코레이터, 메시지, 출력 파서, 콜백 처리
- `langchain-openai`: Azure OpenAI LLM 래퍼
- `MCP`: 의사결정 스킬을 툴 서버로 노출하고 deepagents가 호출할 수 있게 연결
- `Pydantic`: structured output 스키마 정의
- `Pandas`: 예측/스킬/시계열 데이터 처리
- `XGBoost`, `LightGBM`: 실제 예측 모델
- `OpenAI/Azure OpenAI Embeddings + Chroma`: 전략 메모리 벡터 검색
- `SQLite`: 장기 저장소

이 중 사용자가 질문한 핵심 축은 `ALFP + deepagents + MCP`이므로 아래에서 이 세 가지와 연결 프레임워크를 중심으로 설명한다.

## 3. ALFP에서 LangGraph를 어떻게 쓰는가

`alfp/pipeline/graph.py`를 보면 이 프로젝트의 최상위 실행 프레임워크는 `LangGraph`다. 여기서 `StateGraph`, `END`를 사용해 상태 기반 파이프라인을 정의한다.

이 그래프는 대략 다음 흐름으로 동작한다.

1. 데이터 로드
2. 데이터 품질 점검
3. 피처 엔지니어링
4. forecast planner
5. load/PV/net load 예측
6. validation
7. KPI 결과에 따라 재계획 또는 decision
8. evidence curator
9. critic agent
10. policy gate
11. simulation sandbox
12. strategy memory 저장

중요한 점은 `LangGraph`가 "누가 먼저 실행되는가"를 담당한다는 것이다. 예를 들어 validation KPI가 나쁘면 `_route_after_validation()`이 `replan`으로 보내고, policy gate 결과가 `APPROVED`, `REPLAN_REQUIRED`, `REJECTED`인지에 따라 다음 노드를 바꾼다. 즉 ALFP의 상위 제어면은 LangGraph가 맡고 있다.

## 4. deepagents는 무엇을 담당하는가

`deepagents`는 ALFP 전체를 돌리는 프레임워크가 아니라, 그래프 안의 특정 복잡한 의사결정 단계를 "다중 역할 LLM 협업" 방식으로 풀어내는 하위 오케스트레이션 계층이다.

코드상 deepagents가 쓰이는 주요 파일은 다음과 같다.

- `alfp/deepagents/forecast_planner.py`
- `alfp/deepagents/decision.py`
- `alfp/deepagents/governance.py`

공통 패턴은 거의 같다.

1. `create_deep_agent(...)`로 역할별 에이전트를 만든다.
2. 각 에이전트에 `system_prompt`, `name`, `response_format`, `tools`, `backend`를 준다.
3. 첫 번째 에이전트가 초안 생성
4. 두 번째 에이전트가 리스크 리뷰
5. 세 번째 에이전트가 두 결과를 종합
6. 결과는 `Pydantic` 스키마로 구조화한다.

즉 deepagents는 "planner -> reviewer -> coordinator" 같은 계층형 협업 구조를 쉽게 만드는 프레임워크로 사용된다.

### 4.1 forecast planner에서의 deepagents 사용

`alfp/deepagents/forecast_planner.py`에서는 다음 3개 역할이 순차적으로 동작한다.

- `alfp_forecast_strategy_designer`
- `alfp_forecast_risk_reviewer`
- `alfp_forecast_planner_coordinator`

첫 번째 에이전트는 후보 예측 전략과 실패 가설을 만들고, 두 번째는 MAPE/피크 정확도/설명 가능성 리스크를 검토하며, 마지막 에이전트가 최종 선택안을 만든다.

즉 deepagents는 예측 모델 자체를 학습하는 프레임워크가 아니라, "어떤 예측 전략을 선택할지"에 대한 메타 플래닝 프레임워크다.

### 4.2 decision에서의 deepagents 사용

`alfp/deepagents/decision.py`에서는 다음 3개 역할이 보인다.

- `alfp_portfolio_strategist`
- `alfp_portfolio_risk_reviewer`
- `alfp_decision_coordinator`

여기서는 ESS, 거래, DR 조합 후보를 만들고 비교한 뒤 최종 전략을 고른다. 이 부분이 MCP와 가장 강하게 연결된다. strategist와 reviewer는 내부 계산을 직접 다 하지 않고, MCP 기반 스킬 툴을 호출해 후보 생성과 비교를 수행한다.

즉 decision 계층에서 deepagents는 "LLM이 사고하고", MCP는 "계산 가능한 스킬을 실행"하는 분업 구조를 만든다.

### 4.3 governance에서의 deepagents 사용

`alfp/deepagents/governance.py`에서는 다음 두 역할이 있다.

- `alfp_governance_landscape_analyst`
- `alfp_governance_critic_coordinator`

여기서는 선택된 후보의 위험도, 반례(counterexample), sandbox 점수를 재검토한다. 운영 추천이 과도하게 공격적인지, 더 안전한 대안이 있는지 다시 따진다. 따라서 deepagents는 단순 생성기보다 "2차 비판과 조정" 프레임워크로도 쓰인다.

## 5. MCP는 ALFP에서 무엇을 하는가

`MCP(Model Context Protocol)`는 ALFP에서 외부 도구 호출 표준 역할을 한다. 이 프로젝트에서는 `alfp/mcp/decision_skills_server.py`와 `alfp/mcp/decision_skills_client.py`로 구현되어 있다.

핵심 구조는 다음과 같다.

- 서버: `FastMCP("alfp-decision-skills")`
- 클라이언트: `ClientSession`, `StdioServerParameters`, `stdio_client`
- 통신 방식: stdio 기반 로컬 프로세스 호출

즉 별도 HTTP 서버를 띄우는 구조가 아니라, Python 스크립트를 MCP 서버로 실행하고 표준 입출력으로 통신하는 방식이다.

### 5.1 MCP 서버에서 제공하는 실제 스킬

ALFP는 decision 영역에서 최소 3개의 MCP 툴을 노출한다.

- `generate_strategy_candidates`
- `compare_strategy_candidates`
- `recommend_mode_profile`

이 스킬들은 `structured_output=True`로 등록되어 있어, 문자열이 아니라 구조화된 데이터로 결과를 돌려준다. 이 점이 중요하다. deepagents가 자연어 설명만 받는 것이 아니라, 후보 포트폴리오, 위험 점수, 정책 위반 확률, 배터리 열화 비용 같은 필드를 안정적으로 전달받기 때문이다.

### 5.2 MCP 서버 안에서 실제 계산은 무엇으로 하는가

MCP 서버는 단순 프록시가 아니다. 내부에서 도메인 스킬과 데이터프레임 계산을 수행한다.

- `Pandas`로 시계열 데이터 조작
- `ESSOptimizationSkill`로 ESS 스케줄 생성
- `TariffAnalysisSkill`로 요금 절감 시뮬레이션
- 규칙 기반 리스크 계산 로직으로 위험도/정책 위반 확률 산출

즉 MCP는 "도구 호출 규약"이고, 실제 계산 엔진은 `skills/`와 `pandas` 기반 로직이다.

### 5.3 deepagents와 MCP의 연결 방식

`alfp/deepagents/decision.py`를 보면 `@tool`로 감싼 함수들이 있다.

- `generate_strategy_candidates`
- `compare_strategy_candidates`
- `recommend_mode_profile`

이 함수들은 다시 `call_decision_skill(...)`을 호출한다. 그리고 이 함수는 `alfp/mcp/decision_skills_client.py`에서 MCP 세션을 열어 실제 서버 툴을 호출한다.

흐름을 한 줄로 정리하면 다음과 같다.

`deepagents 에이전트` -> `LangChain tool wrapper` -> `MCP client` -> `FastMCP server` -> `도메인 skill/pandas 계산`

이 구조의 장점은 LLM 추론과 결정론적 계산을 분리할 수 있다는 점이다.

- LLM은 후보를 해석하고 선택 이유를 만든다.
- MCP 스킬은 계산, 비교, 규칙 적용을 수행한다.
- 따라서 설명 가능성과 재현성을 동시에 높일 수 있다.

## 6. LangChain 계열 프레임워크는 어디에 쓰이는가

이 프로젝트는 `LangChain` 전체를 무겁게 사용하는 구조라기보다, `LangChain Core`와 `langchain-openai`를 기반 부품처럼 사용한다.

주요 사용처는 다음과 같다.

- `langchain_core.tools.tool`: deepagents에 연결할 툴 선언
- `langchain_core.messages`: validation 등에서 메시지 객체 사용
- `langchain_core.output_parsers.JsonOutputParser`: 출력 파싱
- `langchain_core.callbacks`: LLM I/O 로깅
- `langchain_openai.AzureChatOpenAI`: Azure OpenAI 모델 연결

즉 ALFP에서 LangChain은 최상위 오케스트레이터가 아니라, LLM 호출과 툴 인터페이스를 안정적으로 다루는 기반 계층이다.

## 7. LLM 프레임워크와 모델 연결 방식

`alfp/llm.py`를 보면 LLM 연결은 `AzureChatOpenAI`를 통해 이뤄진다. 환경 변수는 다음 계열을 사용한다.

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT`
- `AZURE_OPENAI_API_VERSION`

기본 배포 이름은 `gpt-4o`로 잡혀 있다. 또한 `SEAPAC_LLM_MODE`와 `ALFP_DISABLE_LLM`으로 stage별 활성화를 제어한다. 이 구조는 실무적으로 중요하다. 모든 노드가 항상 LLM을 쓰는 것이 아니라, forecast/plan/core/market/all 같은 단계별 모드로 켜고 끌 수 있기 때문이다.

즉 ALFP는 "LLM always-on" 구조가 아니라, 필요 단계만 LLM을 활성화하고 나머지는 규칙 기반 폴백도 가능한 하이브리드 설계다.

## 8. 예측 모델 프레임워크

`alfp/models/`에는 두 가지 전통 ML 모델 래퍼가 있다.

- `XGBForecastModel`
- `LGBMForecastModel`

각각 `xgboost.XGBRegressor`, `lightgbm.LGBMRegressor`를 감싼 형태다. 이는 ALFP가 LLM만으로 예측하지 않고, 실제 부하 예측은 트리 기반 회귀 모델로 수행한다는 뜻이다.

정리하면 역할 분담은 다음과 같다.

- 전통 ML: 숫자 예측
- deepagents: 전략 생성과 검토
- MCP 스킬: 계산 가능한 의사결정 보조
- LangGraph: 전체 워크플로우 제어

이 조합이 ALFP 아키텍처의 핵심이다.

## 9. 메모리와 저장 프레임워크

ALFP는 한 번 실행하고 끝나는 구조가 아니다. `storage/db.py`와 `memory/vector_store.py`를 보면 장기 기억 계층도 갖고 있다.

- `SQLite`: 영구 메모리, 전략 메모리, LLM 로그 저장
- `Chroma`: 전략 메모리 벡터 인덱스
- `OpenAI/Azure OpenAI Embeddings`: 검색용 임베딩 생성

즉 과거 전략과 문맥을 다음 실행에 재사용할 수 있게 설계돼 있다. 이것은 일반적인 예측 파이프라인보다 agent 시스템에 더 가까운 특징이다.

## 10. ALFP에서 각 프레임워크의 역할을 한 문장씩 정리

- `LangGraph`: 전체 실행 순서와 분기 제어를 맡는 상위 워크플로우 엔진
- `deepagents`: 하나의 복잡한 문제를 여러 역할 에이전트로 나눠 협업시키는 하위 멀티에이전트 엔진
- `MCP`: 계산 가능한 스킬을 표준 툴 인터페이스로 노출하는 프로토콜/실행 계층
- `LangChain Core`: 툴, 메시지, 파서, 콜백 같은 LLM 애플리케이션 기본 부품
- `langchain-openai`: Azure OpenAI 모델 어댑터
- `Pydantic`: 에이전트 응답을 구조화하고 강제하는 스키마 계층
- `Pandas`: 시계열/테이블 데이터 처리
- `XGBoost`, `LightGBM`: 실제 수요 예측 모델
- `Chroma + Embeddings`: 장기 전략 메모리 검색
- `SQLite`: 운영 상태와 메모리 영속화

## 11. 최종 해석

ALFP에서 가장 중요한 설계 포인트는 프레임워크를 한 개만 쓰지 않는다는 점이다. 이 프로젝트는 다음처럼 층을 나눠서 사용한다.

- 최상위 제어는 `LangGraph`
- 복잡한 추론 단계는 `deepagents`
- 계산형 스킬 호출은 `MCP`
- LLM 연결과 툴 정의는 `LangChain Core/OpenAI`
- 수치 예측은 `XGBoost/LightGBM`
- 기억과 검색은 `SQLite + Chroma + Embeddings`

따라서 "ALFP는 deepagents 기반인가"라고 물으면 부분적으로 맞지만 정확하지 않다. 더 정확한 표현은 다음과 같다.

`ALFP는 LangGraph 위에서 동작하는 멀티에이전트 파이프라인이며, 일부 핵심 추론 단계는 deepagents로 구현되고, 계산형 의사결정 스킬은 MCP 서버로 분리된 하이브리드 아키텍처다.`

## 12. 참고로 확인한 주요 파일

- `alfp/pipeline/graph.py`
- `alfp/deepagents/forecast_planner.py`
- `alfp/deepagents/decision.py`
- `alfp/deepagents/governance.py`
- `alfp/mcp/decision_skills_server.py`
- `alfp/mcp/decision_skills_client.py`
- `alfp/llm.py`
- `alfp/models/xgboost_model.py`
- `alfp/models/lgbm_model.py`
- `alfp/memory/vector_store.py`
- `alfp/storage/db.py`
- `requirements.txt`
