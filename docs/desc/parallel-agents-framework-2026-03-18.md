# parallel_agents 디렉토리 기능 설명 (2026-03-18)

## 1. 먼저 한 줄 요약

`parallel_agents`는 Step 3에서 만들어진 후보 액션을 Step 4 실행 직전에 다시 한 번 병렬로 심사하는 "최종 실행 안전 게이트"다.

짧게 쓰면 흐름은 이렇다.

`Step 3 decisions` → `candidate bundle 변환` → `Policy / EcoSaver / Storage 병렬 평가` → `Execution Orchestrator 병합` → `실행 가능한 decisions만 남김` → `run_execution()`

즉 이 디렉토리는 새로 의사결정을 만드는 곳이라기보다, 이미 나온 결정을:

- 규정에 맞는지
- 배터리와 PV가 실제로 가능한지
- 주민 절전 권고를 같이 줄지

를 마지막으로 재검토하는 계층이다.

---

## 2. 현실 세계에 비유하면 무엇인가

`parallel_agents`는 공장이나 아파트 단지의 에너지 운영에서 "최종 실행 승인실"에 가깝다.

예를 들어 Step 3에서 어떤 운영안이 나왔다고 하자.

- ESS는 10kW 방전
- 남는 전력은 커뮤니티에 판매
- 피크 시간이니 일부 절감 요청

이제 이 안을 바로 실행하는 것이 아니라, 실행 직전에 3명의 심사 담당자가 동시에 본다.

### 2.1 Policy Agent

"이거 규정 위반 아니야? 가격 제한, 최소 거래량, 배터리 안전 범위 어긴 것 없나?"

즉 법무팀 + 안전관리팀 같은 역할이다.

### 2.2 Storage Agent

"이 배터리 SoC로 진짜 지금 방전 가능한가? 충전/방전 한도를 넘지 않나? PV 잉여량은 충분한가?"

즉 설비운영팀 같은 역할이다.

### 2.3 Eco Saver Agent

"이 상황이면 주민에게 어떤 절전 안내를 보내면 좋을까?"

즉 고객 안내팀 같은 역할이다.

그리고 `orchestrator.py`는 이 셋의 의견을 받아 최종 승인 목록을 만드는 실장 역할이다.

---

## 3. 디렉토리 구조와 역할

### 3.1 `contracts.py`

이 파일은 기존 `decisions`를 병렬 심사 형식으로 바꾸고, 심사 결과를 다시 `decisions` 형식으로 되돌린다.

핵심 함수는 2개다.

- `decisions_to_candidate_bundle()`
- `orchestrator_output_to_decisions()`

즉 현실 세계로 치면:

- 운영회의 결과를 "심사위원 검토 양식"으로 재작성
- 심사 완료 후 다시 "실행 지시서" 양식으로 환원

하는 변환기다.

### 3.2 `policy_agent.py`

정책, 규제, 안전 제약을 검사한다.

권한이 강하다.

- 승인 가능
- 수정 가능
- 거절 가능

즉 veto 권한이 있다.

### 3.3 `storage_agent.py`

PV와 ESS 관점에서 물리적으로 가능한지 본다.

여기도 veto 권한이 있다.

- ESS SoC 검사
- 충/방전 한도 검사
- SoC 투영
- 간단한 열화 추정

즉 설비가 못 하는 명령은 여기서 막는다.

### 3.4 `eco_saver_agent.py`

절전 권고를 만든다.

하지만 실행 자체를 막지는 않는다.

즉 advisory only다.

### 3.5 `orchestrator.py`

핵심 제어실이다.

- 세 에이전트를 병렬 실행
- 결과를 병합
- Policy와 Storage의 veto를 반영
- 최종 승인 액션 목록 생성
- 다시 Step 4용 `decisions` 형식으로 변환

### 3.6 `audit_log.py`

병렬 심사 결과를 append-only JSONL로 남긴다.

즉 사후에:

- 어떤 액션이 들어왔고
- 몇 개가 거절됐고
- 위험 점수가 얼마였는지

를 추적할 수 있게 한다.

---

## 4. 입력은 무엇이고 출력은 무엇인가

### 4.1 입력

입력은 기존 Step 3 결과다.

주요 입력은 다음 2개다.

- `decisions`
- `state_json_list`

여기서 `decisions`는 보통 다음을 담고 있다.

- `ess_schedule`
- `trading_recommendations`
- `demand_response_events`

그리고 `state_json_list`는 각 시점의 현장 상태다.

- 부하
- PV 발전량
- ESS SoC
- 가격
- 피크 위험

즉 현실 세계로 치면:

- 실행 예정표
- 당시 현장 계기판

이 함께 들어간다.

### 4.2 출력

최종 출력은 다시 `run_execution()`이 바로 받을 수 있는 `decisions`다.

추가로 `parallel_layer` 아래에 병렬 심사 흔적이 붙는다.

- `approved_actions`
- `rejected_actions`
- `modified_actions`
- `recommendations`
- `policy_violation_report`
- `risk_score`
- `notification_payload`
- `evaluated_steps`
- `step_summaries`

즉 최종 실행안과 감사 흔적이 같이 남는다.

---

## 5. 핵심 연결점: 왜 `contracts.py`가 중요한가

이 디렉토리에서 가장 중요한 시작점은 `decisions_to_candidate_bundle()`이다.

이 함수는 Step 3 산출물을 병렬 심사용 표준 묶음으로 바꾼다.

예를 들면:

- ESS 스케줄 → `type: "ess"`
- 거래 권고 → `type: "market_sell"`
- DR 이벤트 → `type: "demand_response"`

로 바뀐다.

그리고 각 액션에:

- `action_id`
- `timestamp`
- `power_kw`
- `volume_kwh`
- `recommended_reduction_kw`

같은 공통 심사 필드를 붙인다.

이건 현실 세계로 치면, 각 부서가 제각각 적어 낸 제안을 중앙 심사 표준 양식으로 통일하는 작업이다.

### 5.1 step별 bundle도 만든다

`state_json_list`가 있으면 이 함수는 단일 묶음만 만드는 것이 아니라, 시점별 `step_bundles`도 만든다.

즉:

- 00:00 상태에서 볼 액션 묶음
- 00:15 상태에서 볼 액션 묶음
- 00:30 상태에서 볼 액션 묶음

처럼 쪼갠다.

이게 중요한 이유는, 같은 "방전 10kW"라도:

- SoC 50%일 때는 가능
- SoC 9%일 때는 불가능

할 수 있기 때문이다.

테스트 코드도 바로 이 점을 검증한다.

즉 `parallel_agents`는 단순 전체 평균 심사가 아니라, 시간별 현장상태를 보고 액션을 걸러낸다.

---

## 6. Policy Agent는 정확히 무엇을 검사하는가

`policy_agent.py`의 `PolicyConfig`를 보면 이 계층이 보는 규칙 범위가 드러난다.

- 최대 충전 전력
- 최대 방전 전력
- 최소/최대 SoC
- 최소 거래량
- 최대 거래량
- 가격 하한/상한
- PV export limit
- DR 최대 감축량

`run_policy_agent()`는 액션 타입별로 다르게 본다.

### 6.1 ESS 액션

- charge가 최대 충전량보다 크면 clamp
- discharge가 최대 방전량보다 크면 clamp
- SoC가 상한 이상이면 charge 차단
- SoC가 하한 이하이면 discharge 차단

즉 일부는 수정하고, 일부는 완전히 막는다.

### 6.2 거래 액션

- 최소 거래량보다 작으면 거절
- 최대 거래량보다 크면 clamp
- 가격 범위를 벗어나면 거절

즉 "시장 규정 위반 주문"을 걸러낸다.

### 6.3 DR 액션

- 음수 감축은 거절
- 너무 큰 감축은 clamp

즉 비상식적 절감 명령을 막는다.

### 6.4 위험 점수도 만든다

단순 승인/거절만 하는 것이 아니라 `risk_score`를 계산한다.

즉 현실 세계로 치면:

- 이번 실행안은 대체로 안전함
- 아니면 위반이나 거절이 많아서 위험도가 높음

을 숫자로 남긴다.

---

## 7. Storage Agent는 정확히 무엇을 하는가

`storage_agent.py`는 둘로 나뉜다.

- PV Operation Manager
- ESS Operation Manager

### 7.1 PV Operation Manager

이 부분은 현재 비교적 단순하다.

- 현재 부하와 PV를 비교
- 자가소비량 계산
- 잉여 PV 계산
- export 가능량 계산

즉 "지금 태양광 전력을 어디까지 현장에서 쓰고, 어디까지 밖으로 내보낼 수 있나"를 본다.

### 7.2 ESS Operation Manager

이 부분이 핵심이다.

ESS 액션만 따로 뽑아:

- 현재 SoC에서 충전 가능한지
- 현재 SoC에서 방전 가능한지
- 충전/방전 한도를 넘지 않는지
- 액션이 순서대로 적용되면 SoC가 어떻게 변할지

를 계산한다.

결과로:

- `soc_projection`
- `ess_charge_schedule`
- `ess_discharge_schedule`
- `degradation_estimate`

를 만든다.

즉 현실 세계 비유로는:

- 설비 담당자가 "이 배터리 상태면 첫 번째 방전은 가능하지만 두 번째 방전은 안 됩니다"
- 라고 순차 검토하는 것과 같다.

### 7.3 Storage Agent의 veto 성격

Storage Agent는 ESS 관련해서는 강한 veto를 가진다.

예를 들어 SoC가 너무 낮으면 해당 ESS 액션을 거절한다.

반면:

- `market_sell`
- `demand_response`

는 스토리지 관점에서 직접 막지 않고 통과시킨다.

즉 설비팀은 자기 관할인 배터리/발전 쪽만 강하게 본다.

---

## 8. Eco Saver Agent는 무엇을 만드는가

`eco_saver_agent.py`는 실행 자체를 바꾸기보다 권고를 만든다.

예를 들어:

- 피크를 넘으면 사용을 21시 이후로 미루라는 권고
- 부하가 적당히 높으면 야간 사용 권고
- PV 잉여가 많으면 지금 자가소비를 늘리라는 권고

를 만든다.

출력은 다음 같은 형태다.

- `recommendations`
- `estimated_savings_krw`
- `acceptance_probability`
- `notification_payload`

즉 현실 세계 비유로는:

- "지금 세탁기 돌리면 손해고, 밤 9시 이후로 미루면 절감됩니다"

라는 주민 앱 푸시 메시지를 자동 생성하는 역할이다.

중요한 점은 알림 피로를 막기 위해 step당 최대 3개까지만 권고한다는 점이다.

즉 실행 게이트이면서 동시에 고객 커뮤니케이션 계층도 겸한다.

---

## 9. Orchestrator는 병렬 평가를 어떻게 합치는가

`orchestrator.py`가 이 디렉토리의 진짜 중심이다.

### 9.1 병렬 실행

`_run_parallel_async()`는:

- Policy
- Eco Saver
- Storage

를 `asyncio` executor로 동시에 돌린다.

즉 현실 세계 비유로는 같은 문서를 세 부서에 동시에 회람하는 방식이다.

### 9.2 장애 시 fallback

여기 설계가 실무적이다.

- Policy Agent 실패 → 안전모드, 모든 액션 거절
- Eco Saver 실패 → 권고 없이 계속 진행
- Storage Agent 실패 → ESS만 거절, 시장/DR은 통과

즉 가장 위험한 쪽은 보수적으로 막고, 부가 기능은 빠져도 시스템은 움직이게 한다.

### 9.3 병합 규칙

병합 우선순위는 명확하다.

- Policy와 Storage는 veto 권한
- Eco Saver는 advisory only

따라서 최종 승인 조건은 사실상:

`Policy approved ∩ Storage approved`

이다.

즉 두 심사관이 모두 통과시킨 액션만 실행된다.

이 규칙은 현실 세계로 치면:

- 법무/안전팀도 OK
- 설비운영팀도 OK

일 때만 현장 지시가 나간다는 뜻이다.

### 9.4 수정본 우선

어떤 액션이 clamp되거나 수정되었으면, 원본보다 수정본을 최종 `approved_actions_detail`에 반영한다.

즉:

- "원안 그대로"
- "수정 후 승인"

을 구분하는 구조다.

---

## 10. 왜 stepwise evaluation이 중요한가

이 디렉토리에서 실무적으로 가장 중요한 포인트는 `step_bundles` 기반 stepwise evaluation이다.

`run_parallel_evaluation()`은 `step_bundles`가 있으면 각 시점마다 따로 평가한다.

예를 들어:

- 00:00에는 SoC 50%라 discharge 승인
- 00:15에는 SoC 9%라 discharge 거절

같은 결과가 나올 수 있다.

실제로 테스트 코드도 이 상황을 확인한다.

즉 이 기능은 "하루 전체에 대한 한 번의 추상 검토"가 아니라, "시간대별 현장 상태를 반영한 구체적 실행 필터"다.

현실 세계 비유:

- 오전 회의에서 가능했던 배터리 운전이
- 오후에는 이미 잔량이 내려가서 불가능해질 수 있는데
- 그 차이를 step별로 잡아내는 구조다.

---

## 11. 최종적으로 Step 4에 어떻게 연결되는가

`run_parallel_evaluation_and_convert()`가 연결의 핵심이다.

이 함수는:

1. `decisions`를 candidate bundle로 바꾸고
2. 병렬 심사를 돌리고
3. 그 결과를 다시 `decisions` 형식으로 환원한다

이렇게 반환된 값은 그대로 `run_execution()` 입력으로 들어간다.

즉 `parallel_agents`는 Step 3의 대체물이 아니라, Step 3과 Step 4 사이에 끼어드는 "최종 정제 계층"이다.

파이프라인상 위치를 쓰면:

`Step 3 Multi-Agent Decision` → `Step 3.5 Final Parallel Execution Layer` → `Step 4 Action Execution`

이다.

현실 세계 비유:

- 운영회의에서 초안 결정
- 실행승인실에서 최종 심사
- 현장 지시서 발행

순서다.

---

## 12. 감사 로그는 왜 필요한가

`audit_log.py`는 append-only JSONL 형식으로 기록한다.

남기는 항목은 예를 들면:

- 시각
- run_id
- site_state 요약
- 후보 액션 수
- 승인/거절 수
- 정책 위반 수
- 위험 점수
- 최종 ESS/거래/DR 건수

즉 현실 세계로는 결재 이력 대장이다.

이게 있으면 나중에:

- 왜 이 액션이 막혔는지
- 당시 위험 점수가 왜 높았는지
- 병렬 심사 후 거래 수가 어떻게 줄었는지

를 추적할 수 있다.

SEAPAC 같은 운영 시스템에서는 설명 가능성과 감사 가능성이 중요하므로, 이 파일은 단순 로그 이상으로 의미가 있다.

---

## 13. 이 디렉토리의 본질을 가장 쉽게 설명하면

`parallel_agents`는 "더 똑똑한 의사결정 엔진"이라기보다 "실행 직전 품질보증 레이어"에 가깝다.

ALFP나 `seapac_agents/cda`가 만든 행동 후보를 그대로 현장에 보내지 않고:

- 정책 위반을 제거하고
- 설비 불가능 명령을 제거하고
- 주민 절전 권고를 붙이고
- 실행 가능한 액션만 남긴다

는 점이 핵심이다.

즉 현실 세계로는:

- 초안 작성 부서
- 거래/운영 부서
- 마지막 승인실

중에서 마지막 승인실 역할이다.

---

## 14. 최종 요약

- `parallel_agents`는 Step 3 뒤 Step 4 앞에 붙는 최종 병렬 심사 계층이다.
- 입력은 `decisions + state_json_list`, 출력은 다시 `run_execution()` 가능한 `decisions`다.
- `Policy Agent`와 `Storage Agent`는 veto 권한이 있고, `Eco Saver Agent`는 권고만 만든다.
- `Orchestrator`는 세 결과를 병합해 승인/거절/수정/권고를 정리한다.
- 시간별 `step_bundles`를 써서 각 시점 상태에 맞게 액션을 걸러낸다.
- 감사 로그를 통해 사후 추적과 설명 가능성을 확보한다.

가장 현실적인 비유는 아래와 같다.

`parallel_agents = 실행 직전 병렬 승인위원회`

그리고 그 안에서:

- `Policy Agent = 법무/안전 심사관`
- `Storage Agent = 설비운영 심사관`
- `Eco Saver Agent = 주민 안내 담당`
- `Orchestrator = 최종 승인실장`

이라고 보면 된다.
