# [MESA] 시뮬레이션 실행 화면

## 재설계 (Mesa 라이브러리 참조)

[Mesa](https://github.com/mesa/mesa) 구조에 맞춰 **Modeling · Analysis · Visualization** 세 가지 영역으로 화면을 재구성했습니다.

- **Intro**: "Mesa: Agent-based simulation" 헤더, GitHub 링크, Modeling / Analysis / Visualization 펍
- **Analysis**: Model reporters (DataCollector · get_model_vars_dataframe) — 스텝별 지표 테이블
- **Visualization**: Time series (model metric over time) + P2P Market (agent interaction) — 2열 레이아웃(대형 화면)

---

## 첨부 이미지 대비 검토

## 첨부 이미지 구성

1. **왼쪽 패널**: 10×10 그리드, X/Y 축(0~9), 셀별 색상(빨강/파랑/회색)·크기·투명도(Opacity 0~1), Shape: square  
   → **공간(spatial) MESA**: 에이전트가 2차원 격자 위에 위치하는 시각화

2. **오른쪽 패널**: Step(가로) × 값(0~250), 3개 시계열 — AvgPrice(주황), TotalVolume(초록), TotalImbalance(보라)  
   → **스텝별 집계 지표** 시계열 차트

---

## 현재 파이프라인 MESA 구조

- **모델**: `simulation/model.py` — **시간축(스텝) + 커뮤니티/시장 집계**
  - 에이전트: ProsumerAgent, ESSAgent, EnergyMarketAgent
  - **에이전트의 (x, y) 격자 위치 없음** → 2D 공간 그리드 데이터 없음
- **수집 데이터** (`run_{id}_mesa_trajectory.json`): 스텝별 **모델 레벨** 지표만 저장
  - step, hour, community_load_kw, community_pv_kw, community_net_kw  
  - avg_forecast_mape, ess_soc_pct, ess_power_kw  
  - market_matched_kw, market_trade_count, cumulative_saving_krw

---

## 검토 결과

| 구분 | 첨부 왼쪽 (10×10 그리드) | 첨부 오른쪽 (Step 시계열) |
|------|---------------------------|----------------------------|
| **표시 가능 여부** | **현재 불가** | **가능 (이미 유사 구현)** |
| **이유** | 2D 격자 위 에이전트 위치/속성 데이터가 없음 | 스텝별 지표가 이미 수집·저장되며, 궤적 차트로 표시 중 |
| **조치** | 공간 MESA 모델 확장 시 그리드 시각화 추가 가능 | 범례/축 이름을 AvgPrice·TotalVolume·TotalImbalance 스타일로 맞추거나, 동일 형식의 추가 차트 구성 가능 |

---

## 현재 대시보드에서 제공하는 것

- **그리드 (스텝 × 지표)**: 테이블 — 스텝이 행, 지표가 열 (이미지 왼쪽의 “공간 그리드”와는 다른 형태)
- **궤적 추적 (스텝별 지표)**: Step 기준 라인 차트 — 부하(kW), Net(kW), ESS SoC(%), P2P(kW) 등 (이미지 오른쪽과 **개념적으로 동일**)
- **전력거래(P2P)**: 스텝별 P2P 거래량·누적 절감액 차트

---

## 결론

- **오른쪽 패널(Step 기준 시계열)**  
  → **표시 가능**. 현재 “[MESA] 시뮬레이션 실행” 탭의 “궤적 추적 (스텝별 지표)”가 동일한 형태이며, 필요 시 AvgPrice/TotalVolume/TotalImbalance에 대응하는 지표로 범례·축 이름을 매핑해 동일한 느낌으로 정리할 수 있음.

- **왼쪽 패널(10×10 에이전트 그리드)**  
  → **현재 데이터로는 불가**. 구현하려면 시뮬레이션을 2D 격자 + 에이전트 위치를 수집하는 구조로 확장하고, Dashboard에서 해당 데이터를 받아 캔버스/SVG로 그리드 시각화를 추가해야 함.
