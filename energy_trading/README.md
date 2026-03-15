## Energy Trading Simulator (Mesa 3.5)

이 프로젝트는 Mesa 3.5를 사용한 간단한 **에너지 거래 시뮬레이터** 예제입니다.

### 도메인 개요

- 에이전트: 가구 또는 prosumer (소비 + 생산)
  - 속성: 보유 에너지(`energy`), 목표 수준(`target_energy`), 현금(`cash`), 개별 호가 가격(`bid_price`, `ask_price`)
- 공간: 2D 격자 (이웃 간에만 에너지 거래)
- 메커니즘(라운드당):
  1. 각 에이전트가 자신의 에너지 잉여/부족을 계산
  2. 이웃 중 **서로 가격이 겹치는 매수자–매도자**를 찾아 1단위씩 거래
  3. 거래 가격은 단순히 `(buyer.bid_price + seller.ask_price) / 2`
  4. `DataCollector`로 총 거래량, 평균 가격, 에너지 불균형 등을 추적

### 파일 구조

- `energy_trading/`
  - `agents.py` — 에너지 트레이더 에이전트 정의
  - `model.py` — `EnergyTradingModel` 정의, 거래 로직 & DataCollector
  - `app.py` — Solara + Mesa `SolaraViz` 대시보드 (격자 + 시계열 플롯)
  - `README.md` — 프로젝트 설명 (현재 문서)

### 실행 방법

```bash
cd /Users/a09206/work/ai_master_26/mesa
source .venv/bin/activate

# 텍스트 모드(백엔드만)로 몇 스텝 실행
python -m energy_trading.model

# Solara 대시보드 실행
solara run energy_trading/app.py
```

브라우저에서 `http://localhost:8765`에 접속하면:

- 2D 격자에 에이전트가 색깔로 표시됨 (에너지 잉여/부족에 따라 색 변경)
- 하단 플롯에서 시간에 따른:
  - 평균 거래 가격
  - 거래량
  - 전체 에너지 불균형(목표에서 얼마나 벗어났는지)
을 확인할 수 있습니다.

