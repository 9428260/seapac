# 개선 로드맵

## 목표
현재 시스템을 "예측 기반 P2P 시뮬레이션" 수준에서 "단기 운영 가능한 폐루프형 에너지 거래 시스템"으로 끌어올립니다. 우선순위는 실시간 입력, 체결 후 제어, 다음 라운드 전략 업데이트입니다.

## Phase 1. 입력 계층 고도화
목표: 정적 `pkl` 중심 구조를 운영 입력 구조로 확장

- 스마트미터/PV/ESS 실시간 입력 어댑터 추가
  - MQTT, REST, CSV polling, DB reader 중 1개 표준화
  - 출력 스키마를 `timestamp`, `load_kw`, `pv_kw`, `bess_soc_kwh`, `price_*`로 통일
- 날씨 현재값이 아닌 예보 수집 추가
  - OpenWeather forecast 또는 대체 예보 API를 15분~1시간 horizon 기준으로 적재
  - feature engineering 단계에서 forecast weather 사용
- 데이터 품질 체크 강화
  - 결측, 지연, 이상치, 타임존 mismatch 검증 추가

산출물:
- `ingestion` 모듈
- 실시간 입력 스키마 정의
- weather forecast feature 파이프라인

## Phase 2. 단기 예측 운영 모드 추가
목표: 다음 15분~1시간 의사결정에 맞는 short-horizon 모드 구축

- `steps=1~4` 기본 short-horizon 실행 모드 추가
- load/PV/net-load 예측을 rolling inference 방식으로 실행
- short-horizon KPI 분리
  - 15분 ahead MAPE
  - 1시간 ahead MAPE
  - short-term peak hit accuracy
- dashboard/run pipeline에서 운영 모드 선택 가능하게 변경

산출물:
- `--operating-mode short_horizon`
- short-horizon 전용 평가 리포트

## Phase 3. 시장 제출 단위 세분화
목표: community aggregate가 아니라 prosumer 단위 시장 행동으로 전환

- prosumer별 seller/buyer bid/ask 생성
- bid/ask 잔량 관리, partial fill, time priority 보강
- market snapshot을 step 단위로 저장
- 체결률, 미체결량, bid aggressiveness 지표 추가

산출물:
- prosumer-level order book
- 강화된 matching/settlement 로그
- dashboard 시장 지표 확장

## Phase 4. 체결 후 제어 폐루프 구현
목표: 체결 결과가 실제 부하/ESS 상태 변화로 이어지도록 구현

- 거래 체결량을 prosumer energy balance에 반영
- DR 이벤트를 실제 `load_kw` 감소로 모델에 반영
- ESS 제어 결과를 다음 스텝 SoC와 charge/discharge 가능량에 직접 연결
- 시뮬레이션 내 actuator abstraction 추가
  - `apply_trade()`
  - `apply_dr()`
  - `apply_ess_dispatch()`

산출물:
- 거래 후 상태 반영 모델
- DR 실효 반영 로직
- 체결 후 상태 변화 검증 테스트

## Phase 5. 다음 라운드 전략 업데이트
목표: 체결 결과를 반영해 bid/ask 전략이 다음 라운드에 적응하도록 구현

- round-by-round feedback loop 추가
  - 체결 성공/실패
  - 미체결량
  - buyer saving / seller revenue
  - peak impact
- strategy memory에 시장 성과 필드 추가
- 다음 라운드 bid price/quantity 조정 정책 추가
  - aggressive / conservative pricing
  - 잉여 반복 미체결 시 ask 하향
  - shortage 반복 시 bid 상향
- online learning 또는 bandit-style policy 도입 가능성 검토

산출물:
- adaptive bid/ask updater
- feedback-aware strategy memory
- 다음 라운드 전략 변경 로그

## Phase 6. 평가 체계 확장
목표: 운영 적합성 기준 KPI로 확장

- 기존 KPI 유지
  - 비용
  - 거래 수익
  - 피크 저감
  - ESS 열화
- 추가 KPI
  - 체결률
  - 미체결량
  - round response quality
  - forecast regret
  - DR 실효 반영률
  - SoC 안정성
- run detail/dashboard에 KPI 카드 추가

산출물:
- 운영 KPI 세트
- 성능 비교 리포트
- baseline 대비 개선 추적

## 권장 우선순위
1. 실시간/예보 입력
2. short-horizon 운영 모드
3. 체결 후 제어 폐루프
4. 다음 라운드 전략 업데이트
5. 시장 미시구조 고도화
6. 평가 체계 확장

## 개발 순서 제안
1. 입력 스키마와 ingestion 인터페이스 정의
2. short-horizon 실행 경로 추가
3. DR/거래 체결 결과를 시뮬레이션 상태에 반영
4. adaptive strategy updater 추가
5. dashboard와 evaluation 확장
