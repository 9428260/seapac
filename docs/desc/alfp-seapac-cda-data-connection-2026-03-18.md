# ALFP 결과 데이터가 seapac_agents와 cda에서 연결되는 방식 설명 (2026-03-18)

## 1. 먼저 결론

이 코드베이스에서 `ALFP` 결과 데이터는 단순 보고서가 아니라, 이후 운영 계층이 참고하는 "초기 운영안"이다.

흐름을 가장 짧게 쓰면 아래와 같다.

`ALFP 결과(decisions)` → `상태 JSON(state_json_list)` 또는 seed decisions → `seapac_agents` 의사결정 → `cda` 시장 체결 → `run_execution()`으로 Mesa 재실행

즉:

- `ALFP`는 "먼저 짜 본 초안 운영계획"
- `seapac_agents`는 "각 담당자가 초안을 보고 실제 운영안을 조정하는 운영본부"
- `cda`는 "그중 거래 파트를 실제 시장 방식으로 체결하는 거래소"
- `Mesa 실행`은 "그 결정대로 하루를 다시 돌려보는 디지털 트윈"

으로 이해하면 가장 정확하다.

---

## 2. 디렉토리 역할을 현실 세계에 빗대면

### 2.1 `seapac_agents`는 무엇인가

`seapac_agents`는 전체 운영 본부에 가깝다.

- `decision.py`: 판매 담당, 저장장치 담당, 절감 담당, 정책 담당이 각각 의견을 낸다.
- `agent_planner.py`: 누가 먼저 일하고, 무엇을 병렬로 할지 실행 순서를 짠다.
- `execution.py`: 최종 결정을 실제 운영 시뮬레이션에 넣어 본다.
- `evaluation.py`: 그 결과가 비용, 피크, 거래, ESS 관점에서 얼마나 좋았는지 채점한다.
- `state_translator.py`: 시뮬레이션 숫자를 에이전트가 읽기 쉬운 상태 카드로 바꾼다.

현실 세계 비유로는:

- 아파트 단지나 산업단지의 에너지 운영센터
- 운영센터 안의 팀장들
- 마지막 승인 전에 모의운영까지 돌려보는 상황

에 가깝다.

### 2.2 `cda`는 무엇인가

`cda`는 전체 운영 본부가 아니라 거래소다.

- `orderbook.py`: 매수/매도 주문판
- `matching.py`: 누가 누구와 얼마에 체결되는지 결정
- `buyer.py`: 부족한 쪽의 구매 주문 생성
- `coordinator.py`: 상태와 에이전트 제안을 받아 시장 체결 결과를 만든다
- `strategy_agent.py`: 거래 전략 초안
- `negotiation.py`: 협상 레이어
- `settlement.py`: 체결 결과를 실행 단계로 넘긴다

현실 세계 비유로는:

- 전력거래소의 호가창
- 중개시장
- 협상 후 최종 계약을 체결하는 장터

에 가깝다.

즉 `seapac_agents`가 "운영 전체", `cda`가 "거래 전문 파트"다.

---

## 3. ALFP 결과 데이터가 어떤 모양으로 들어오는가

코드 기준으로 `ALFP` 결과는 주로 `decisions` 형태로 이어진다.

대표 항목은 아래 3개다.

- `ess_schedule`: 언제 충전/방전/대기할지
- `trading_recommendations`: 언제 얼마를 팔거나 거래할지
- `demand_response_events`: 언제 얼마나 절감 요청할지

이건 현실 세계로 치면 다음과 같다.

- ESS 스케줄: "배터리를 몇 시에 얼마나 쓰자"
- 거래 권고: "남는 전기를 단지 안에서 팔지, 계통으로 넘길지 보자"
- DR 이벤트: "오늘 피크 시간에는 냉난방이나 설비 부하를 조금 낮추자"

즉 ALFP는 먼저 "하루 운영 초안"을 만든다.

---

## 4. 연결의 핵심: ALFP 결과는 두 가지 방식으로 downstream에 들어간다

### 4.1 방식 A: ALFP 결과를 바로 seed decisions로 넣는다

이 경로에서는 `ALFP decisions`가 바로 `seapac_agents`의 입력 참고자료가 된다.

`run_full_pipeline.py`의 `stage_multi_agent_decision()`을 보면:

- `alfp_decisions`가 들어오면 로그로 ESS/거래/DR 건수를 확인하고
- Step 3 결과에 비어 있는 항목이 있으면 ALFP 결과로 보완한다
- `agent_planner.py`에서도 `alfp_decisions` 요약을 보고 계획을 다시 세운다

현실 세계 비유:

- ALFP가 야간에 자동으로 만든 "내일 운영 초안"이 있다
- 아침에 운영회의를 할 때 각 담당자가 그 초안을 보고 수정한다
- 회의 결과에 빈칸이 있으면 초안 값을 그대로 가져다 쓴다

즉 ALFP는 최종 답이 아니라, 운영회의의 출발점이다.

### 4.2 방식 B: ALFP 결과로 먼저 상태 장면을 만든 뒤, 그 상태를 seapac/cda가 읽는다

이 경로가 더 중요하다.

`run_full_pipeline.py`의 `_alfp_forecast_to_state_json_list()`와 `seapac_agents/state_translator.py` 역할을 합쳐 보면, ALFP 또는 Mesa 결과를 아래 같은 상태 카드로 바꾼다.

- `community_state`
- `market_state`
- `ess_state`
- `prosumer_states`

즉 에이전트는 원시 예측 테이블을 바로 읽지 않고, "현재 단지 상황 요약 카드"를 읽는다.

현실 세계 비유:

- ALFP는 숫자 많은 예측 원장
- `state_json`은 운영회의용 브리핑 슬라이드

예를 들어 브리핑 슬라이드에는:

- 현재 총부하가 얼마나 되는지
- PV가 얼마나 나오는지
- 잉여인지 부족인지
- 피크 위험이 높은지
- ESS SoC가 얼마인지
- 프로슈머별로 누가 남고 누가 부족한지

가 적혀 있다.

이 슬라이드를 보고 `seapac_agents`와 `cda`가 움직인다.

---

## 5. seapac_agents 안에서 실제로 어떻게 연결되는가

### 5.1 `state_json_list`가 에이전트 회의 자료가 된다

`seapac_agents/decision.py`와 `cda/coordinator.py`를 보면 둘 다 결국 `state_json_list`를 순회하면서 스텝별 결정을 만든다.

각 스텝에서 일어나는 일은 대략 이렇다.

1. 상태 카드 한 장을 꺼낸다
2. `PolicyAgent`가 제약 조건을 본다
3. `SmartSeller`가 판매 제안을 낸다
4. `StorageMaster`가 ESS 충방전 제안을 낸다
5. `EcoSaver`가 DR 제안을 낸다
6. 그 결과를 조정해 최종 `decisions` 형식으로 만든다

현실 세계 비유:

- 15분 단위 운영회의를 96번 연속으로 한다
- 회의마다 "지금은 피크 위험 HIGH", "지금은 잉여 PV 발생", "배터리 SoC 62%" 같은 상황판이 있다
- 각 담당자가 자기 관점으로 행동안을 낸다

즉 `state_json_list`는 에이전트 회의의 안건 묶음이다.

### 5.2 ALFP decisions는 agent planner의 상위 계획 입력이 된다

`seapac_agents/agent_planner.py`는 `alfp_decisions`를 요약해서:

- 거래는 얼마나 있었는지
- ESS 스케줄은 얼마나 있는지
- DR은 얼마나 있는지
- 위반 사항은 있었는지

를 보고 `policy -> trading/storage/eco_saver -> simulate` 순서로 계획을 세운다.

현실 세계 비유:

- 전날 AI 초안 보고서를 읽은 운영실장이
- "정책팀 먼저 확인하고, 거래팀/배터리팀/절감팀은 동시에 검토한 뒤, 마지막으로 모의운전 돌리자"
- 라고 실행 순서를 짜는 것과 같다.

즉 ALFP는 단순 데이터가 아니라, 운영 오케스트레이션의 인풋이다.

---

## 6. cda 안에서 실제로 어떻게 연결되는가

### 6.1 `seapac_agents`의 제안이 `cda`의 주문으로 바뀐다

`cda/coordinator.py`의 `run_cda_step()`이 연결의 중심이다.

여기서 하는 일은:

- `seller_msg`에서 판매 제안을 꺼낸다
- `storage_msg`에서 ESS 제안을 꺼낸다
- `eco_msg`에서 DR 제안을 꺼낸다
- `policy_agent`로 ESS/거래/DR을 검증한다
- `state_json`의 `prosumer_states`를 보고 seller ask를 만든다
- `buyer.py`로 deficit 기준 buyer bid를 만든다
- 오더북에 넣고 매칭한다

즉 `cda`는 ALFP를 직접 읽기보다, ALFP가 만든 상태와 `seapac_agents`가 만든 제안을 시장 주문으로 변환한다.

현실 세계 비유:

- 운영회의에서 "우리는 8kW를 이 가격 이상이면 팔겠다"는 의견이 나옴
- 시장팀은 이걸 실제 매도 주문서로 바꿈
- 부족한 건물들은 자동으로 매수 주문서를 냄
- 거래소가 주문판에서 체결시킴

중요한 점은, 이때 결과가 완전히 새로운 형식으로 끝나는 것이 아니라 다시 `decisions` 형식으로 정리된다는 점이다.

즉 `cda`는 거래소이지만, 출력은 다시 운영본부가 쓸 수 있는 형식으로 되돌려 준다.

### 6.2 `cda` 출력도 결국 Step 4 실행 형식을 맞춘다

`cda`는 체결 후 아래 형식으로 다시 모은다.

- `ess_schedule`
- `trading_recommendations`
- `demand_response_events`
- 추가로 `cda_trades`, `cda_snapshot`

이 구조는 `seapac_agents.execution.run_execution()`이 그대로 받을 수 있게 설계돼 있다.

현실 세계 비유:

- 거래소가 체결 명세서를 낸다
- 그런데 그 명세서가 운영센터 ERP에서 바로 읽을 수 있는 표준 양식이다

그래서 `cda/settlement.py`도 새 실행 엔진을 따로 쓰지 않고, 내부적으로 `seapac_agents.execution.run_execution()`을 재사용한다.

---

## 7. 가장 중요한 연결 문장

이 코드베이스에서 `ALFP 결과`가 `seapac_agents`와 `cda`에 연결되는 핵심은 아래 한 문장으로 정리된다.

`ALFP는 미래 운영 초안과 상태 단서를 만들고, seapac_agents는 그 초안을 운영안으로 재조정하며, cda는 그중 거래 부분을 시장 체결로 구체화한 뒤, 최종 결과를 다시 공통 decisions 형식으로 되돌려 Mesa 실행에 넣는다.`

---

## 8. 현실 세계 전체 비유

이걸 실제 도시나 산업단지 운영으로 비유하면 아래와 같다.

### 8.1 ALFP = 전날 밤 자동 작성된 운영 초안

- 내일 태양광은 얼마나 나올지
- 부하는 언제 몰릴지
- 배터리는 언제 충전/방전하면 좋을지
- 거래나 절감은 언제 필요할지

를 미리 작성해 둔 AI 운영 초안이다.

### 8.2 seapac_agents = 아침 운영대책회의

- 정책팀: 이 계획은 규정 위반 없는가
- 거래팀: 잉여 전력을 어떻게 팔까
- 배터리팀: ESS는 언제 써야 하나
- 수요관리팀: 피크 때 얼마나 줄일 수 있나

각 팀이 같은 상황판을 보고 수정안을 낸다.

### 8.3 cda = 실제 장터/거래소

- 팔겠다는 쪽은 호가를 낸다
- 사겠다는 쪽은 입찰을 낸다
- 거래소가 맞는 가격과 수량을 찾아 체결한다

즉 회의에서 말로 끝나는 것이 아니라, 실제 계약서 수준으로 구체화된다.

### 8.4 execution + Mesa = 실운영 전 모의가동

- 이렇게 정리된 계획을 디지털 트윈에 넣고
- "정말 피크가 줄었는가"
- "배터리가 과도하게 방전되진 않는가"
- "거래 효과가 있었는가"

를 다시 검증한다.

즉 최종적으로는 "좋아 보이는 말"이 아니라 "돌려 보니 괜찮은 운영안"만 남긴다.

---

## 9. 구현상 주의해서 봐야 할 점

현재 코드상 중요한 점이 하나 있다.

- `seapac_agents/run_agentic_pipeline.py`만 단독으로 보면 `state_json_list`가 빈 리스트로 시작한다
- 실제 ALFP 결과와 `seapac_agents`, `cda`의 연결은 `run_full_pipeline.py` 쪽 설명이 더 현실에 가깝다

즉 현재 저장소에서 "실제 연결 구조"를 설명할 때는 `run_full_pipeline.py` 기준으로 이해하는 것이 안전하다.

현실 세계 비유:

- `run_agentic_pipeline.py`는 회의실만 준비된 상태
- `run_full_pipeline.py`는 회의자료와 초안 운영계획까지 들고 입장하는 전체 운영 시나리오

---

## 10. 최종 요약

- `ALFP`는 초안 운영계획과 상태 단서를 만든다.
- `seapac_agents`는 그 초안을 여러 역할 에이전트가 검토해 운영 가능한 결정으로 바꾼다.
- `cda`는 그중 거래 부분을 실제 시장 체결 구조로 바꾼다.
- 두 디렉토리는 경쟁 관계가 아니라, `seapac_agents` 안에 `cda`가 거래 전문 모듈처럼 끼어드는 관계다.
- 최종 출력은 다시 공통 `decisions` 형식으로 정리돼 `run_execution()`과 Mesa 재실행으로 이어진다.

가장 현실적으로 비유하면:

`ALFP = 초안 작성 AI`, `seapac_agents = 운영본부`, `cda = 전력거래소`, `Mesa = 모의운영 디지털 트윈`

이다.
