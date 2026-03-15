# 아키텍처 구현 여부 확인

요청하신 레이어별 아키텍처가 코드베이스에 어떻게 구현되어 있는지 확인한 결과입니다.

---

## 1. [ External / Field Data ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **스마트미터** | ✅ | 시계열 데이터 `load_kw` (부하)로 사용. `alfp/data/loader.py`, `timeseries.load_kw`, Mesa ProsumerAgent의 `current_load_kw` |
| **태양광 발전량** | ✅ | `timeseries.pv_kw`, ProsumerAgent `current_pv_kw`, DataCollector `community_pv_kw` |
| **ESS 상태 (SOC)** | ✅ | `simulation/agents/ess.py` ESSAgent `soc`, `soc_pct` / state_translator `ess_state.soc` / execution `soc_kwh` |
| **ESS 상태 (SOH)** | ❌ | State of Health(배터리 열화도) 필드·계산 없음 |
| **날씨** | ✅ | ALFP ForecastPlanner에서 OpenWeather API 사용 (`alfp/tools/openweather.py`, `forecast_planner.py`의 `get_current_weather_tool`) |
| **TOU 요금** | ✅ | `alfp/skills/tariff_analysis.py`, `skills_config.json`의 `tou_periods`, DecisionAgent·StorageMaster TOU 기반 결정 |
| **계통 가격** | ✅ | `elia_internal` 가격 데이터, `price_buy` (feature/요금 분석), state_translator `market_state.grid_price` |

---

## 2. [ Mesa Simulation Layer ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **ApartmentCommunityModel** | ⚠️ 다른 이름 | **ALFPSimulationModel** (`simulation/model.py`) — 단지/커뮤니티 시뮬레이션 모델로 동일 역할 |
| **HouseholdAgent** | ⚠️ 다른 이름 | **ProsumerAgent** (`simulation/agents/prosumer.py`) — 세대/프로슈머 단위 에이전트 |
| **ProsumerAgent** | ✅ | `simulation/agents/prosumer.py` — 부하·PV·예측·거래/DR 결정 반영 |
| **ESSAssetAgent** | ⚠️ 다른 이름 | **ESSAgent** (`simulation/agents/ess.py`) — ESS 자산 에이전트, TOU·피크·SoC 기반 충방전 |
| **MarketEnvironment** | ⚠️ 다른 이름 | **EnergyMarketAgent** (`simulation/agents/market.py`) — P2P 매칭·거래 환경 |
| **DataCollector** | ✅ | `simulation/model.py` — model_reporters / agent_reporters로 step별 수집 |
| **시간단위/15분단위 step** | ✅ | 1 tick = 15분 (`model.step()` 주석, `dt_h=0.25`, `--steps 96` = 24시간) |
| **시나리오별 결과 생성** | ✅ | Phase 1~4 (`run_simulation.py --phase 1|2|3|4`, `--all-phases`), `model.run(n_steps)` → DataFrame |

---

## 3. [ State Translator / Feature Store ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **단지 총부하** | ✅ | `state_translator.translate_model_state()` → `community_state.total_load` |
| **세대별 잉여/부족** | ✅ | community: `surplus_energy` / `deficit_energy`; 세대별: DataCollector `agent_reporters`의 `surplus_kw`, `load_kw`, `pv_kw` |
| **ESS SOC / 제약조건** | ✅ | `ess_state.soc`, `capacity`, `available_discharge`; PolicyAgent·ESSAgent의 `soc_min`/`soc_max` |
| **예상 피크시간** | ⚠️ 부분 | `peak_risk` (LOW/MEDIUM/HIGH) 존재. “예상 피크**시간**”(시각) 필드는 없음 |
| **거래가능량** | ✅ | `surplus_energy`, `trading_recommendations`, Market 매칭량 |
| **가격 band** | ✅ | `market_state.community_trade_price_range`, `grid_price` |
| **KPI 요약** | ✅ | Step 5 Evaluation (`seapac_agents/evaluation.py`) — 비용, 수익, 피크감소율, ESS 마모, DR 수락율 등 |

**Feature Store**라는 별도 저장소/서비스는 없음. State Translator 출력(JSON)과 DataCollector DataFrame이 “요약·특성” 역할을 함.

---

## 4. [ AgentScope Multi-Agent Layer ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **SmartSeller-Agent** | ✅ | `seapac_agents/decision.py` **SmartSellerAgentAS** — 잉여 전력 bid_price/bid_quantity·판매 결정 |
| **StorageMaster-Agent** | ✅ | **StorageMasterAgentAS** — ESS 충방전 제어, TOU·피크·SoC 기반 |
| **EcoSaver-Agent** | ✅ | **EcoSaverAgentAS** — DR 권고(절감 추천) 생성 |
| **MarketCoordinator-Agent** | ✅ | **MarketCoordinatorAgentAS** — 제안 수렴·충돌 해결·최종 decisions 생성 |
| **Policy/Trust Agent** | ✅ (Policy) | **PolicyAgentAS** — ESS/거래/DR 제약 검증·클램핑 |
| **Trust Agent (이상거래 감시)** | ❌ | PRD의 “Trust Agent” 역할(이상거래 감시·감사)은 별도 구현 없음. PolicyAgent가 제약 검증만 수행 |

**참고**: PRD에는 “AgentScope” 기반으로 명시되어 있으나, 실제 코드는 **AgentScope 라이브러리 미사용** — 동일 5개 에이전트를 **독립 Python 클래스**(AgentBase 상속)로 구현. `docs/PRD_IMPLEMENTATION_STATUS.md` 참고.

---

## 5. [ Execution & Control Layer ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **bid 제출** | ✅ | `trading_recommendations` → TradeAction 빌드 → `execution.build_actions_from_decisions()` |
| **ESS schedule 생성** | ✅ | `ess_schedule` → ESSAction → `ALFPSimulationModel(alfp_decisions=decisions)`로 스텝별 적용 |
| **절약 recommendation 생성** | ✅ | `demand_response_events` → DemandResponseAction, EcoSaver 제안 반영 |
| **거래 승인/거절** | ✅ | `execution.validate_all_actions()` + `approve_actions()`; Mesa 단에서 EnergyMarketAgent가 매칭·거래 수행 |
| **Mesa 다음 step 반영** | ✅ | `run_execution()` 내부에서 `ALFPSimulationModel(alfp_decisions=...)` 생성 후 `model.run()` 호출, step() 시 `_ess_schedule_by_step`, `_trading_by_step`, `_dr_by_step` 사용 |

---

## 6. [ Evaluation / Runtime ]

| 항목 | 구현 여부 | 비고 |
|------|-----------|------|
| **KPI 계산** | ✅ | `seapac_agents/evaluation.py` — Energy Cost, Trading Profit, Peak Reduction, ESS Degradation Cost, User Acceptance, 종합 등급 |
| **로그/추적/관측** | ✅ | DataCollector 시계열, `execution_summary.json`, `execution_timeseries.csv`, `--output-dir` / `--save-csv` |
| **AgentScope Runtime 배포** | ❌ | AgentScope 프레임워크 미도입. CLI·Python API로만 실행 가능 (예: `run_agentic_pipeline.py`, `simulation/run_execution.py`) |

---

## 요약

| 레이어 | 전반적 구현 | 미구현/다른 이름 |
|--------|-------------|------------------|
| External / Field Data | ✅ 대부분 | SOH 미구현 |
| Mesa Simulation | ✅ | ApartmentCommunity→ALFPSimulation, Household→Prosumer, ESSAsset→ESS, MarketEnvironment→EnergyMarket |
| State Translator / Feature Store | ✅ | Feature Store 별도 시스템 없음, 예상 피크**시간** 미구현 |
| AgentScope Multi-Agent | ✅ (동일 역할) | AgentScope 라이브러리 미사용(독립 클래스), Trust Agent 미구현 |
| Execution & Control | ✅ | — |
| Evaluation / Runtime | ✅ KPI·로그 | AgentScope Runtime 배포 없음 |

원하시면 “예상 피크시간”, “SOH”, “Trust Agent”, “AgentScope Runtime 배포” 중 우선 반영할 항목을 정해 주시면, 해당 부분 설계·구현 방향을 구체적으로 제안하겠습니다.
