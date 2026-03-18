# 전체 파이프라인에서 의사결정이 일어나는 부분 정리 (2026-03-19)

## 1. 한눈에 보는 전체 흐름

이 프로젝트의 전체 파이프라인은 크게 아래 순서로 움직인다.

1. ALFP가 예측과 1차 운영 전략을 만든다.
2. 필요하면 ALFP 내부에서 `plan -> validate -> replan` 루프를 돈다.
3. ALFP 결과를 바탕으로 Step3 다중 에이전트가 ESS, 거래, DR 결정을 다시 만든다.
4. Step3.5 병렬 에이전트가 그 결정을 안전성 관점에서 다시 검토한다.
5. Step4 실행 엔진이 실제로 시뮬레이션을 돌리고 승인 여부를 판단한다.
6. Step5 평가 엔진이 비용, 피크, 거래 성과를 평가한다.

즉 이 시스템은 한 번 결정하고 끝나는 구조가 아니다.  
"계획", "검증", "재계획", "실행 승인", "사후 평가"가 층층이 쌓인 구조다.

현실 세계로 비유하면, 다음과 비슷하다.

- ALFP: 내일 운영계획을 짜는 에너지 운영팀
- Step3: 각 부서 담당자들이 세부 실행안을 만드는 회의
- Step3.5: 안전팀, 설비팀, 수요반응팀의 동시 검토
- Step4: 실제 운영센터가 실행 승인 버튼을 누르는 단계
- Step5: 운영 결과를 보고 성과평가 보고서를 쓰는 단계

## 2. ALFP 안에서 첫 번째 의사결정이 일어나는 곳

ALFP 내부의 상위 제어는 [`alfp/pipeline/graph.py`](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py) 에서 정의한다.

핵심 흐름은 다음과 같다.

1. `data_loader`
2. `data_quality`
3. `feature_engineering`
4. `forecast_planner`
5. `load_forecast`
6. `pv_forecast`
7. `net_load_forecast`
8. `validation`
9. 조건에 따라 `replan` 또는 `decision`
10. `evidence_curator`
11. `critic_agent`
12. `policy_gate`
13. `simulation_sandbox`
14. `save_memory`

여기서 중요한 것은 ALFP가 단순 예측기가 아니라는 점이다.  
예측을 만든 뒤, 그 예측이 믿을 만한지 보고, 전략을 만들고, 다시 거버넌스로 검토한다.

## 3. `plan`은 왜 일어나는가

`plan`은 예측 모델을 바로 돌리기 전에 "이번 데이터 상황에서 어떤 전략으로 예측할지"를 정하는 단계다.  
구현은 `forecast_planner`에서 이루어진다.

이 단계에서 정하는 내용은 대략 다음과 같다.

- 어떤 모델을 쓸지 (`lgbm`, `xgboost` 등)
- 어떤 후보 전략이 있는지
- 어떤 후보가 더 설명 가능하고 위험이 낮은지
- 실패 가능성이 어디 있는지
- 다시 실험한다면 무엇을 바꿔야 하는지

현실 비유:

"내일 전력 운영 예측을 해야 하는데, 오늘은 PV 변동성이 크고 과거 실패 사례도 있었다.  
그러니 평소 모델 그대로 갈지, 피크 민감 모델로 바꿀지, 날씨를 더 반영할지를 먼저 회의로 정하는 것"이 `plan`이다.

즉 `plan`은 "숫자를 계산하는 단계"가 아니라, "어떤 계산 방법으로 갈지 결정하는 단계"다.

## 4. `replan`은 왜 일어나는가

`replan`은 "처음 세운 계획이 충분히 좋지 않다"는 신호가 나왔을 때 다시 계획을 짜는 단계다.

현재 코드 기준으로 `replan`이 발생하는 조건은 세 군데다.

### 4.1 Validation 이후 재계획

[`_route_after_validation()`](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py) 에서 결정한다.

조건:

- `MAPE_pass == False` 또는 `peak_acc_pass == False`
- 그리고 재계획 횟수가 상한 미만일 때

즉 예측 KPI가 기준 미달이면 다시 계획을 짠다.

현실 비유:

"내일 수요 예측안을 뽑아봤더니 오차가 너무 크다.  
이 상태로 ESS나 거래 전략을 만들면 위험하니, 모델 선택부터 다시 하자"에 해당한다.

### 4.2 Policy Gate 이후 재계획

[`_route_after_policy_gate()`](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py) 에서 결정한다.

조건:

- `policy_gate_result.status == REPLAN_REQUIRED`

이 경우는 예측은 끝났고 전략도 나왔지만, 정책/규정/운영 원칙 관점에서 바로 승인할 수 없을 때다.

현실 비유:

"운영안은 그럴듯하지만 규정상 위험하거나, 지금 조직 원칙에 맞지 않는다.  
바로 실행하지 말고 전략을 다시 짜라"에 가깝다.

### 4.3 Simulation Sandbox 이후 재계획

[`_route_after_sandbox()`](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py) 에서 결정한다.

조건:

- `simulation_result.replan_required == True`

즉 문서상으로는 괜찮아 보였던 전략이 가상 실행에서는 더 나쁜 결과를 낼 수 있을 때 다시 계획으로 돌아간다.

현실 비유:

"회의실에서는 좋은 전략처럼 보였는데, 모의훈련을 해보니 피크가 안 줄거나 수익성이 떨어진다.  
그래서 운영안을 다시 짜는 것"이다.

### 4.4 재계획은 몇 번까지 가능한가

현재 상한은 2회다.

- 상수: `MAX_PLAN_REPLANS = 2`
- `replan_node()`에서 카운트를 증가시키되 2회를 넘지 않게 제한

즉 시스템은 무한히 다시 생각하지 않는다.  
실무적으로도 회의를 너무 오래 하면 운영 시간이 늦어지므로, 일정 횟수 안에서 결론을 내리게 한 것이다.

## 5. ALFP의 `decision`은 무엇을 결정하는가

ALFP의 `decision` 단계는 예측 결과를 실제 운영 행동으로 바꾸는 첫 번째 결정층이다.

여기서 주로 만드는 것은 다음 세 가지다.

- `ess_schedule`
- `trading_recommendations`
- `demand_response_events`

쉽게 말해:

- ESS를 언제 충전/방전할지
- 남는 전기를 시장이나 P2P로 팔지
- 피크 시간대에 DR 절감을 요청할지

현실 비유:

"예측상 오후 1시부터 PV가 많이 남고, 저녁 피크가 예상된다.  
그러면 낮에는 배터리를 채우고, 피크 직전에는 방전하고, 잉여가 있으면 거래도 고려하자"라는 운영 스케줄을 짜는 단계다.

즉 ALFP `decision`은 "운영 전략 초안"을 만든다.

## 6. ALFP 이후 Step3에서 왜 또 의사결정을 하는가

전체 파이프라인에서는 ALFP 뒤에 [`stage_multi_agent_decision()`](/Users/a09206/work/ai_master_2603_ai/run_full_pipeline.py) 이 한 번 더 돈다.

이 단계는 ALFP 결과를 받아서 다중 에이전트 관점으로 세부 결정을 다시 조합한다.

주요 역할:

- `Policy-Agent`: 제약 검증
- `SmartSeller-Agent`: 판매 전략
- `StorageMaster-Agent`: ESS 충방전
- `EcoSaver-Agent`: DR 절감 전략
- `MarketCoordinator-Agent`: 최종 조정

이 단계가 필요한 이유는 ALFP가 "전체 전략 초안"에 가깝고, Step3는 "실행 담당자 관점의 세부 조정"이기 때문이다.

현실 비유:

ALFP가 본사 운영기획팀이라면, Step3는 현장 운영회의다.

- 정책 담당자는 "이건 규정 위반 아닌가?"
- ESS 담당자는 "배터리 입장에서 이 충전 계획이 무리 아닌가?"
- 거래 담당자는 "지금 가격이면 팔지 말고 보류해야 하지 않나?"
- DR 담당자는 "지금 사용자에게 절감 요청을 보내도 수용성이 낮지 않나?"

이 논의를 거쳐 최종 실행용 `decisions`가 다시 만들어진다.

## 7. Step3.5 병렬 에이전트는 왜 필요한가

[`stage_parallel_agents()`](/Users/a09206/work/ai_master_2603_ai/run_full_pipeline.py) 와 [`parallel_agents/orchestrator.py`](/Users/a09206/work/ai_master_2603_ai/parallel_agents/orchestrator.py) 는 Step3 결과를 다시 병렬로 심사한다.

여기서 보는 것은 "좋은 전략인가?"보다 "실행해도 안전한가?"에 더 가깝다.

주요 판단:

- 승인할 액션
- 거절할 액션
- 수정할 액션
- 정책 위반 보고
- 위험 점수

즉 이 단계는 전략 생성이 아니라 마지막 안전 필터다.

현실 비유:

비행기 출발 전에 기장, 정비팀, 관제팀이 동시에 "이륙 가능한가"를 보는 단계와 비슷하다.  
운항계획이 있어도 마지막 안전 체크에서 막히면 못 나간다.

## 8. `execution`은 왜 일어나는가

`execution`은 단순히 "결정을 실제로 적용하는 단계"가 아니다.  
현재 구현은 `execute -> simulate -> approve` 순서로 돌아간다.

구현은 [`seapac_agents/execution.py`](/Users/a09206/work/ai_master_2603_ai/seapac_agents/execution.py) 의 `run_execution()`에 있다.

이 단계에서 하는 일:

1. 결정을 액션 형태로 변환
2. Mesa 시뮬레이션 실행
3. 정책 검증
4. 시뮬레이션 결과 기반 승인

즉 `execution`은 "실행"이면서 동시에 "최종 승인 심사"다.

현실 비유:

발전소나 에너지 운영센터에서 운영 명령을 바로 내리는 것이 아니라,  
"이 명령을 넣었을 때 실제 계통이 어떻게 움직일지"를 한 번 돌려본 뒤 승인하는 것과 같다.

## 9. `execution` 승인은 어떤 이유로 결정되는가

`execution` 승인 조건은 두 갈래다.

### 9.1 정책 검증 통과

액션 자체가 제약을 어기지 않아야 한다.

예:

- 충전/방전 power 한도 초과 여부
- 잘못된 거래 액션 여부
- DR 액션 형식 오류 여부

### 9.2 시뮬레이션 결과 통과

실제로 돌려본 결과가 운영 제한을 넘어가면 승인되지 않는다.

예:

- 최종 SoC가 너무 낮거나 너무 높음
- 피크 부하가 허용 한도를 넘음

즉 "문법상 맞는 명령"이어도, "운영상 위험한 결과"가 나오면 승인되지 않는다.

현실 비유:

공장 자동화에서도 버튼을 누를 수는 있지만,  
그 버튼이 눌렸을 때 압력이 기준을 넘거나 온도가 위험 수준으로 가면 자동 승인되지 않는 것과 같다.

## 10. Step5 평가는 왜 필요한가

[`stage_evaluation()`](/Users/a09206/work/ai_master_2603_ai/run_full_pipeline.py) 는 실행 후 결과를 평가한다.

여기서 보는 것은 다음과 같다.

- 에너지 비용
- 거래 수익
- 피크 감소율
- ESS 마모 비용
- DR 수락률
- 종합 등급

이 단계는 새로운 운영 결정을 직접 만들지는 않지만, 다음 실행의 의사결정 기준을 바꾸는 재료가 된다.

즉 평가는 "보고서"이면서 동시에 다음번 `plan/replan`에 영향을 주는 피드백이다.

현실 비유:

하루 운영이 끝난 뒤,

- 전기요금이 얼마나 줄었는지
- 거래 전략이 실제로 먹혔는지
- 배터리를 너무 혹사했는지
- 사용자 DR 참여가 있었는지

를 보는 사후 리포트에 해당한다.

## 11. 정리하면: 이 시스템은 하나의 결정기가 아니라 다층 의사결정 구조다

이 파이프라인에서 의사결정은 한 번만 일어나지 않는다.

- ALFP `plan`: 어떤 예측 전략으로 갈지 결정
- ALFP `replan`: 결과가 나쁘거나 위험하면 다시 계획
- ALFP `decision`: ESS/거래/DR 초안 생성
- Step3 Multi-Agent Decision: 역할별 관점으로 실행안 재조정
- Step3.5 Parallel Agents: 안전성 중심 승인/거절/수정
- Step4 Execution: 실제 시뮬레이션 기반 최종 승인
- Step5 Evaluation: 성과 평가를 통해 다음 의사결정의 기준 제공

현실 세계로 가장 쉽게 비유하면 이렇다.

- ALFP는 "운영계획팀"
- Step3는 "부서별 실무자 회의"
- Step3.5는 "안전/설비/정책 동시 심사"
- Step4는 "관제실 최종 승인"
- Step5는 "사후 성과평가"

즉 이 시스템은 "AI가 한 번에 답을 내는 구조"가 아니라,  
"계획 -> 검증 -> 반론 -> 재계획 -> 실행 승인 -> 사후 평가"를 여러 층으로 나눈 운영 조직의 디지털 버전에 가깝다.

## 12. 코드 기준으로 특히 봐야 할 파일

- [`alfp/pipeline/graph.py`](/Users/a09206/work/ai_master_2603_ai/alfp/pipeline/graph.py): ALFP 내부의 `plan/replan/decision/governance` 분기
- [`run_full_pipeline.py`](/Users/a09206/work/ai_master_2603_ai/run_full_pipeline.py): ALFP 이후 Step3, Step3.5, Step4, Step5 연결
- [`seapac_agents/decision.py`](/Users/a09206/work/ai_master_2603_ai/seapac_agents/decision.py): Step3 다중 에이전트 의사결정
- [`parallel_agents/orchestrator.py`](/Users/a09206/work/ai_master_2603_ai/parallel_agents/orchestrator.py): 병렬 승인/거절/수정 취합
- [`seapac_agents/execution.py`](/Users/a09206/work/ai_master_2603_ai/seapac_agents/execution.py): 실행 전환, 시뮬레이션, 최종 승인
- [`seapac_agents/evaluation.py`](/Users/a09206/work/ai_master_2603_ai/seapac_agents/evaluation.py): 성과 평가와 다음 의사결정을 위한 피드백
