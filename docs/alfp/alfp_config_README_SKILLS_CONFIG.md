# 설정 가이드

## 1. Skills 파라미터 (skills_config.json)

스킬·에이전트에서 사용하는 **변경 가능한 값**을 `alfp/config/skills_config.json` 에 두면, **소스 수정 없이** 파일만 수정해 동작을 바꿀 수 있습니다.

- 설정 파일 경로: `alfp/config/skills_config.json`
- 파일이 없거나 키가 없으면 코드에 있는 기본값이 사용됩니다.
- JSON 형식이므로 따옴표·쉼표 문법에 유의하세요.

---

## 1. energy_forecast (EnergyForecastSkill)

| 키 | 설명 | 기본값 |
|----|------|--------|
| `energy_forecast.model_selection.lgbm_min_samples` | 이 레코드 수 이상이면 LGBM 선택 | `5000` |
| `energy_forecast.model_selection.default_model` | 그 미만일 때 선택할 모델 | `"xgboost"` |
| `energy_forecast.evaluate_forecast.mape_min_actual_kw` | MAPE 계산 시 이 값보다 큰 실제값만 사용 (kW) | `1.0` |

**사용처**: ForecastPlannerAgent fallback 모델 선택, ValidationAgent 예측 평가

---

## 2. tariff_analysis (TariffAnalysisSkill)

| 키 | 설명 | 기본값 |
|----|------|--------|
| `tariff_analysis.interval_hours` | 구간 길이(시간). 15분 = 0.25 | `0.25` |
| `tariff_analysis.tou_periods` | TOU 구간 정의. 각 구간별 `hours`(시 목록), `multiplier`(요금 배율) | 한국 산업용 TOU 간략화 |

**tou_periods 예시**  
- `off_peak`: 심야·저부하 (배율 0.7)  
- `mid_peak`: 중간 (배율 1.0)  
- `on_peak`: 피크 (배율 1.5)  

**사용처**: DecisionAgent TOU 요금 절감 시뮬레이션

---

## 3. ess_optimization (ESSOptimizationSkill)

| 키 | 설명 | 기본값 |
|----|------|--------|
| `ess_optimization.defaults.bess_kwh_cap` | ESS 용량 (kWh) | `50.0` |
| `ess_optimization.defaults.bess_kw_cap` | ESS 출력 (kW) | `25.0` |
| `ess_optimization.defaults.min_soc_pct` | 최소 SOC 비율 (0~1) | `0.20` |
| `ess_optimization.defaults.max_soc_pct` | 최대 SOC 비율 (0~1) | `0.90` |
| `ess_optimization.defaults.initial_soc_pct` | 초기 SOC 비율 (0~1) | `0.50` |
| `ess_optimization.defaults.dt_h` | 스텝 길이(시간). 15분 = 0.25 | `0.25` |
| `ess_optimization.peak_shaving.default_peak_quantile` | 피크 한도 미지정 시 사용할 net_load 분위수 | `0.75` |
| `ess_optimization.peak_shaving.charge_threshold_ratio` | 피크 한도의 이 비율 미만이면 충전 구간 | `0.5` |
| `ess_optimization.tou_schedule.price_high_quantile` | 고요금 판단 분위수 | `0.70` |
| `ess_optimization.tou_schedule.price_low_quantile` | 저요금 판단 분위수 | `0.30` |

**사용처**: DecisionAgent ESS peak shaving 스케줄, 요약

---

## 4. decision_agent (DecisionAgent)

| 키 | 설명 | 기본값 |
|----|------|--------|
| `decision_agent.ess.bess_kwh_cap` | ESS 용량 (kWh) | `50.0` |
| `decision_agent.ess.bess_kw_cap` | ESS 출력 (kW) | `25.0` |
| `decision_agent.ess.dt_h` | 스텝 길이(시간) | `0.25` |
| `decision_agent.peak_threshold_quantile` | 피크 임계값용 net_load 분위수 (예: 0.85 = 85%ile) | `0.85` |
| `decision_agent.trading.surplus_kw_min` | 잉여 전력 권고 최소 kW | `0.5` |
| `decision_agent.trading.max_recommendations` | 거래 권고 최대 건수 | `10` |
| `decision_agent.demand_response.reduction_factor` | DR 권고 감량 계수 (초과분 × 이 값) | `0.3` |
| `decision_agent.tariff_fallback.default_price_buy_krw` | 요금 정보 없을 때 기본 구매가 (원) | `100.0` |
| `decision_agent.llm_temperature` | LLM 운영 전략 생성 시 temperature | `0.2` |

**사용처**: DecisionAgent 전체 (ESS, 거래, DR, TOU, LLM)

---

## 5. forecast_planner (ForecastPlannerAgent fallback)

LLM 실패 시 사용하는 규칙 기반 fallback 모델 설정입니다.

| 키 | 설명 | 기본값 |
|----|------|--------|
| `forecast_planner.fallback.lgbm.num_leaves_energy_hub` | prosumer_type이 EnergyHub일 때 num_leaves | `127` |
| `forecast_planner.fallback.lgbm.num_leaves_default` | 그 외 prosumer_type일 때 num_leaves | `63` |
| `forecast_planner.fallback.lgbm.n_estimators` | LGBM n_estimators | `500` |
| `forecast_planner.fallback.lgbm.learning_rate` | LGBM learning_rate | `0.05` |
| `forecast_planner.fallback.xgboost.max_depth` | XGBoost max_depth | `6` |
| `forecast_planner.fallback.xgboost.n_estimators` | XGBoost n_estimators | `300` |
| `forecast_planner.fallback.xgboost.learning_rate` | XGBoost learning_rate | `0.05` |

**사용처**: ForecastPlannerAgent LLM 오류 시 fallback

---

## 6. validation (ValidationAgent KPI)

| 키 | 설명 | 기본값 |
|----|------|--------|
| `validation.kpi.mape_target_pct` | MAPE 목표치 (%) | `10.0` |
| `validation.kpi.peak_acc_target_pct` | 피크 정확도 목표치 (%) | `90.0` |

**사용처**: ValidationAgent KPI 통과 여부 (MAPE_pass, peak_acc_pass)

---

## 수정 후 반영

- 설정은 **프로세스 시작 시** 한 번 로드되며, 실행 중에 파일을 바꿔도 **같은 프로세스에서는** 이전 값이 유지될 수 있습니다.
- 변경 사항을 확실히 반영하려면 **파이프라인(앱)을 다시 실행**하세요.
- 테스트에서 설정을 다시 읽고 싶다면 `alfp.config.loader.reload_skills_config()` 를 호출할 수 있습니다.

---

## 2. LLM 프롬프트 (prompts/prompts.txt)

LLM에 넘기는 **시스템 프롬프트**와 **유저 프롬프트**는 **한 파일** `alfp/config/prompts/prompts.txt` 에서 관리합니다.  
소스 수정 없이 문구만 바꾸려면 이 파일에서 해당 섹션(`[에이전트.system]` / `[에이전트.user]`)을 수정하면 됩니다.

- **형식**: 섹션 구분자 `[forecast_planner.system]`, `[forecast_planner.user]`, `[decision.system]`, `[decision.user]`, `[validation.system]`, `[validation.user]`
- **자세한 설명**: [alfp_config_prompts_README.md](alfp_config_prompts_README.md)
