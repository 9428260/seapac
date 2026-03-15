# Mesa 시뮬레이션 실행 검토 및 결과 해석 (현실 세계 대비)

## 1. Mesa 실행 부분 검토

### 1.1 진입점 및 실행 흐름

- **진입점**: `run_full_pipeline.py`의 `stage_mesa(args, alfp_decisions)`  
  ALFP 단계에서 생성된 `alfp_decisions`(ESS 스케줄·거래 추천·DR 이벤트)를 받아 Mesa 모델에 넘긴 뒤 시뮬레이션을 한 번 실행합니다.

- **모델 생성** (`simulation/model.py` — `ALFPSimulationModel`):
  - `data_path`: 시계열 pkl (예: `data/train_2026_seoul.pkl`)
  - `n_steps`: 시뮬레이션 스텝 수 (기본 96)
  - `phase`: 1~4 (1=부하예측만, 2=에이전트 예측, 3=+ESS, 4=+P2P 시장)
  - `ess_capacity_kwh`, `ess_peak_threshold_kw`: ESS 파라미터
  - `alfp_decisions`: 있으면 스텝별 ESS/거래/DR 적용, 없으면 규칙 기반 fallback

- **에이전트 구성**:
  - **ProsumerAgent**: pkl의 `prosumer_id`별 1개씩 생성. 각 에이전트는 15분 단위 시계열(`load_kw`, `pv_kw`, 가격 등)을 가짐.
  - **ESSAgent**: Phase ≥ 3일 때 1개 (커뮤니티 공유 ESS).
  - **EnergyMarketAgent**: Phase ≥ 4일 때 1개 (P2P 매칭).

### 1.2 한 스텝(15분) 실행 순서 (`model.step()`)

1. **ProsumerAgent.step()**  
   해당 스텝의 관측값(부하, PV, 가격) 읽기 → Phase에 따라 부하/PV 예측 →  
   `net_load_kw`, `surplus_kw`, `deficit_kw`, `forecast_mape` 계산.

2. **ESSAgent.step()**  
   `alfp_decisions`에 해당 스텝 ESS 스케줄이 있으면 그대로 적용, 없으면 TOU·피크·잉여PV 기반 규칙으로 충/방전 결정 → SoC·절감액 업데이트.

3. **EnergyMarketAgent.step()**  
   `surplus_kw ≥ 0.2`인 에이전트(판매자)와 `deficit_kw ≥ 0.2`인 에이전트(구매자)를 그리디 매칭 → 체결량(`matched_kw_this_step`) 및 수익/절감 반영.

4. **DataCollector**  
   모델 레벨 지표 수집: `community_load_kw`, `community_pv_kw`, `community_net_kw`, `avg_forecast_mape`, `ess_soc_pct`, `market_matched_kw` 등.

5. **current_step += 1**

### 1.3 통계 집계 (요약 출력)

`run_full_pipeline.py`의 `stage_mesa()` 안에서 `model.run()` 후 DataFrame으로부터 다음을 계산해 요약에 넣습니다.

- 시뮬레이션 스텝 수: `len(df)`
- 커뮤니티 최대/평균 부하: `community_load_kw`의 max/mean
- 평균 PV: `community_pv_kw`의 mean
- 평균 Net Load: `community_net_kw`의 mean
- 평균 예측 MAPE: `avg_forecast_mape`의 mean
- ESS 평균 SoC: `ess_soc_pct`의 mean
- P2P 거래량 합계: `market_matched_kw`의 sum
- ALFP 연동 여부: `alfp_decisions` 존재 여부

---

## 2. 제시하신 결과 항목별 해석 (현실 세계와 비교)

### 2.1 시뮬레이션 스텝: 96

- **의미**: 15분 간격 96구간 = **24시간(1일)** 시뮬레이션.
- **현실 대비**: 실제 계통/수요관리도 15분 단위(일부는 30분·1시간)로 계획·실행되므로, 96스텝 1일 시뮬은 현실의 “하루 단위 운영 시나리오”와 같은 시간 해상도라고 보면 됨.

### 2.2 커뮤니티 최대 부하: 922.3 kW

- **의미**: 96스텝 중 `community_load_kw`의 최댓값.  
  `community_load_kw` = 모든 ProsumerAgent의 `current_load_kw` 합.
- **현실 대비**:
  - 소규모 상가·오피스 단지 또는 중규모 공동주택 단지 한 블록 규모에 해당할 수 있는 수준.
  - 실제로는 변전소·수용가 계약 전력(kW)과 비교해 “피크 부하가 계약 전력 대비 어느 정도인지”가 중요함. 시뮬에서는 단일 일자 패턴이라, 실제로는 여러 일·계절을 돌려 피크 분포를 보는 것이 현실에 가깝다.

### 2.3 커뮤니티 평균 부하: 659.0 kW

- **의미**: 24시간 동안 부하의 평균.
- **현실 대비**:  
  - 평균 659 kW, 피크 922 kW → **부하율(평균/피크) 약 71%**.  
  - 상가·오피스는 주간에 부하가 몰리므로 이런 형태가 나오기 쉽고, 야간 부하가 줄어드는 패턴이 반영된 결과로 해석 가능.

### 2.4 평균 PV 발전: 129.5 kW

- **의미**: 96스텝 동안 `community_pv_kw`의 평균.
- **현실 대비**:
  - 야간 0 + 주간만 발전하므로, “전체 24시간 평균”은 당연히 설비용량 대비 낮게 나옴.
  - 실제로는 “일사량·설비용량(kWp)·발전시간”으로 연간/일일 발전량을 추정하는 것과 동일한 개념.  
  - 129.5 kW 평균이면, 피크 대비 비율·일조 시간 등을 고려해 설비 규모를 역산해 볼 수 있음 (현실의 단지 태양광 용량 검토와 같은 맥락).

### 2.5 평균 Net Load: 529.4 kW

- **의미**: `community_net_kw` = `community_load_kw` − `community_pv_kw` 의 평균.  
  즉 “부하 − PV”의 24시간 평균.
- **현실 대비**:
  - 계통에서 공급해야 할 순수요(수전)에 해당.  
  - 실제 수요관리·요금(TOU)·ESS 운영은 이 “Net Load” 곡선을 기준으로 함.  
  - 529.4 kW 평균은 “PV가 있음에도 여전히 평균적으로 이만큼은 계통/ESS로 채워야 한다”는 의미로, 현실의 “순수전량” 개념과 동일함.

### 2.6 평균 예측 MAPE: 18.78 %

- **의미**: 각 스텝에서 ProsumerAgent별 부하 예측값과 실제 관측 부하의 MAPE를 구한 뒤, 스텝별로 프로슈머 평균 MAPE를 내고, 다시 96스텝 평균한 값.
- **현실 대비**:
  - 단기 수요 예측에서 MAPE 15~25% 구간은 흔히 나오는 수준. 18.78%는 “보통 수준의 예측 정확도”로 해석 가능.
  - 현장에서는 날씨·휴일·이벤트 등으로 MAPE가 더 나빠질 수 있음.  
  - Phase 1(단순 이동평균)이면 이보다 나쁠 수 있고, Phase 2(에이전트 파이프라인) 또는 외부 예측 모델을 쓰면 개선 여지가 있음.

### 2.7 ESS 평균 SoC: 45.7 %

- **의미**: 96스텝 동안 ESS의 SoC(%) 평균.  
  ESS는 Phase ≥ 3에서 동작하며, ALFP decisions가 있으면 그 스케줄대로, 없으면 TOU·피크·잉여PV 규칙으로 충/방전.
- **현실 대비**:
  - 45.7%는 “전반적으로 중간 구간에서 움직이며, 과충전/과방전을 피한 상태”로 해석 가능.
  - 실제 BESS는 SoC 상·하한(예: 10~90%) 내에서 운영하는데, 평균이 45%대면 수명·안전 측면에서 무리 없는 운영에 가깝다.
  - 초기 SoC 50% 기준이면, 하루 동안 충·방전이 어느 정도 균형을 이룬 결과로 볼 수 있음.

### 2.8 P2P 거래량 합계: 0.0 kW

- **의미**: 96스텝 동안 `market_matched_kw`의 합.  
  EnergyMarketAgent가 “같은 스텝에서 surplus 있는 에이전트 ↔ deficit 있는 에이전트”를 매칭한 양의 합계.
- **현실 대비**:
  - **0인 이유 (시뮬 구조)**  
    - P2P가 성립하려면 **동일 15분 구간 안에** “잉여(surplus ≥ 0.2 kW) 있는 프로슈머”와 “부족(deficit ≥ 0.2 kW) 있는 프로슈머”가 **동시에** 있어야 함.
    - **프로슈머가 1개만 있으면**: 그 한 명은 매 스텝 surplus 또는 deficit 둘 중 하나만 가지므로, 매칭 상대가 없어 **항상 0**.
    - **프로슈머가 여러 명이어도**:  
      - 모두 같은 유형(예: 상가만)이면 부하·PV 패턴이 비슷해, 같은 시간대에 전원 surplus이거나 전원 deficit인 경우가 많음.  
      - 그 결과 “같은 스텝에 판매자와 구매자가 동시에 존재”하는 구간이 없으면 P2P는 0.
  - **현실에서 P2P가 일어나려면**:  
    - 주거(낮 외출·낮에는 PV 잉여) + 상가(낮 부하 큼·부족)처럼 **수요 패턴이 다른 수용가가 섞인 커뮤니티**이거나,  
    - 설비·부하 분포가 달라서 “같은 시간대에 일부는 잉여, 일부는 부족”인 데이터/설정이 필요함.  
  - 따라서 **시뮬 결과 0 kW는 현재 데이터/구성(단일 프로슈머 또는 동질 커뮤니티) 하에서는 자연스러운 결과**이며, P2P 기능 자체의 오류라기보다는 “P2P가 발생할 조건이 만족되지 않은 실행”으로 보는 것이 맞음.

### 2.9 ALFP decisions 연동: ✓

- **의미**: `stage_mesa()`에 `alfp_decisions`가 넘어갔고, 모델이 이를 사용해 ESS 스케줄·거래 추천·DR을 스텝별로 적용했다는 표시.
- **현실 대비**:  
  실제 시스템에서는 “상위 에너지 관리 시스템(EMS)·에이전트 결정”이 하위 실행기(ESS, P2P 마켓)에 지령을 내리는 구조와 동일. ✓는 “상위 결정이 시뮬레이션 실행에 반영되었다”는 의미로, 현실의 **명령-실행 연동**이 된 상태를 나타냄.

---

## 3. 요약 표 (현실 세계와의 대응)

| 지표 | 시뮬 값 | 현실 세계 대응 |
|------|--------|----------------|
| 시뮬레이션 스텝 | 96 | 15분×96 = 1일, 계통/수요관리 시간 해상도와 동일 |
| 커뮤니티 최대 부하 | 922.3 kW | 단지/블록 규모 피크 부하, 계약 전력·변전소 용량과 비교 대상 |
| 커뮤니티 평균 부하 | 659.0 kW | 일 평균 부하, 부하율·에너지 사용량 해석에 사용 |
| 평균 PV 발전 | 129.5 kW | 24시간 평균이므로 설비용량·일조 시간 고려해 해석 |
| 평균 Net Load | 529.4 kW | 계통/ESS가 채워야 할 순수요(수전), 요금·운영의 기준 |
| 평균 예측 MAPE | 18.78 % | 단기 수요 예측 정확도, 15~25% 구간은 현장에서 흔함 |
| ESS 평균 SoC | 45.7 % | 중간 구간 운영, 수명·안전 관점에서 무리 없는 수준 |
| P2P 거래량 합계 | 0.0 kW | 단일 프로슈머 또는 동질 패턴 시 0이 자연스러움; 이질 수요 혼합 시 증가 가능 |
| ALFP decisions 연동 | ✓ | 상위 에이전트 결정이 하위 실행(ESS/P2P)에 반영된 상태 |

---

## 4. 참고 코드 위치

- Mesa 실행: `run_full_pipeline.py` → `stage_mesa()`
- 모델·스텝·수집: `simulation/model.py` → `ALFPSimulationModel`, `step()`, `run()`, DataCollector
- 프로슈머: `simulation/agents/prosumer.py` → `step()`, 예측·surplus/deficit
- ESS: `simulation/agents/ess.py` → `step()`, ALFP 스케줄 적용·SoC
- P2P 시장: `simulation/agents/market.py` → `step()`, 판매자/구매자 매칭·`matched_kw_this_step`

이 문서는 위 실행 구조와 결과 지표를 “현실 세계의 계통·수요·ESS·P2P”와 어떻게 대응하는지 정리한 것입니다.
