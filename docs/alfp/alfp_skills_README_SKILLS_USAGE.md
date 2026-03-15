# ALFP Skills 사용 현황 설명

- **Skills 사용 방법·데이터 수정 방법**: [SKILLS_AND_DATA_GUIDE.md](../guides/SKILLS_AND_DATA_GUIDE.md)
- **Skills 동작을 현실 세계(전력·요금·ESS)와 비교**: [alfp_skills_README_SKILLS_REAL_WORLD.md](alfp_skills_README_SKILLS_REAL_WORLD.md)
- **스킬·에이전트 파라미터를 소스 수정 없이 변경**: `alfp/config/skills_config.json` 수정 → [설정 가이드](alfp_config_README_SKILLS_CONFIG.md)

---

## 1. 요약


| 스킬                       | 정의 위치                 | **현재 사용처**                                                                                                                                                                  |
| ------------------------ | --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **EnergyForecastSkill**  | `energy_forecast.py`  | ForecastPlannerAgent(fallback 시 `select_model`), LoadForecastAgent(`build_forecast_result`), PVForecastAgent(`build_forecast_result`), ValidationAgent(`evaluate_forecast`) |
| **TariffAnalysisSkill**  | `tariff_analysis.py`  | DecisionAgent(`cost_saving_simulation` → LLM 프롬프트·decisions에 반영)                                                                                                            |
| **ESSOptimizationSkill** | `ess_optimization.py` | DecisionAgent(`peak_shaving_schedule`, `summarize` → ESS 스케줄·요약)                                                                                                            |


**결론**: 세 스킬 모두 해당 에이전트에서 **import 및 메서드 호출**로 사용 중입니다.

---

## 2. 각 스킬의 역할과 "쓰일 수 있는" 부분

### 2.1 EnergyForecastSkill (`energy_forecast.py`)

**제공 기능**

- `select_model(n_samples, prosumer_type)` → `"lgbm"` | `"xgboost"`  
  - 데이터 크기·프로슈머 타입 기준 모델 선택.
- `evaluate_forecast(actual, predicted)` → `{ "mape", "rmse" }`  
  - 예측값 vs 실제값 MAPE/RMSE.
- `build_forecast_result(timestamps, actual, predicted, target_col, pred_col)` → 예측 결과용 DataFrame 생성.

**현재 파이프라인에서의 사용**

- **모델 선택**: `ForecastPlannerAgent`의 `_fallback_plan()`에서 LLM 실패 시 `EnergyForecastSkill.select_model(n_records, prosumer_type)` 사용.
- **예측 평가**: `ValidationAgent`가 `_compute_metrics()`와 함께 `EnergyForecastSkill.evaluate_forecast(actual, predicted)`를 호출해 `skill_mape`, `skill_rmse`를 metrics에 추가.
- **예측 결과 DataFrame**: `LoadForecastAgent`·`PVForecastAgent`가 검증 예측 후 `EnergyForecastSkill.build_forecast_result(timestamps, actual, predicted, target_col, pred_col)`로 `forecast_df` 생성.

---

### 2.2 TariffAnalysisSkill (`tariff_analysis.py`)

**제공 기능**

- `classify_period(hour)` → TOU 구간(`off_peak` / `mid_peak` / `on_peak`).
- `analyze(df)` → `timestamp`, `price_buy`, `load_kw` 기준 TOU 구간·배율·추정 비용 추가.
- `cost_saving_simulation(df, ess_schedule)` → ESS 적용 전/후 비용·절감액·절감률.
- `summarize_by_period(df)` → TOU 구간별 부하·요금 통계.

**현재 파이프라인에서의 사용**

- **DecisionAgent**가 `feature_df`의 `timestamp`, `load_kw`, `price_buy`와 ESS 스케줄 DataFrame으로 `TariffAnalysisSkill().cost_saving_simulation(tariff_df, ess_schedule_df)`를 호출.  
결과(`base_cost_krw`, `adjusted_cost_krw`, `saving_krw`, `saving_pct`)를 LLM 프롬프트 `[TOU 요금 절감 시뮬레이션]` 블록과 `decisions["tariff_saving"]`에 반영.

---

### 2.3 ESSOptimizationSkill (`ess_optimization.py`)

**제공 기능**

- `peak_shaving_schedule(net_load, timestamps, peak_limit_kw)` → 피크 제한 기반 충방전 스케줄 DataFrame.
- `tou_schedule(net_load, timestamps, price_series)` → 요금 높을 때 방전, 낮을 때 충전하는 스케줄.
- `summarize(schedule_df)` → 충전/방전/대기 스텝 수, 충/방전량, SOC min/max 등 요약.

**현재 파이프라인에서의 사용**

- **DecisionAgent**가 `ESSOptimizationSkill(bess_kwh_cap=50, bess_kw_cap=25, dt_h=0.25)`로 인스턴스를 만들고,  
`peak_shaving_schedule(net_load_series, timestamps, peak_limit_kw=peak_threshold)`로 ESS 스케줄 DataFrame을 생성.  
`summarize(ess_schedule_df)`로 `charge_steps`, `discharge_steps`, `idle_steps` 등을 구해 LLM 프롬프트·`ess_summary`에 사용.  
스케줄은 기존 형식에 맞게 list of dict로 변환해 `decisions["ess_schedule"]`에 저장.

---

## 3. PRD와의 관계

[prd/deepagents_energy_forecast_prd.md](../prd/deepagents_energy_forecast_prd.md) 8장 Skills Design에 맞춰, 아래처럼 연동되어 있습니다.

- **EnergyForecastSkill**: ForecastPlanner fallback 모델 선택, Load/PV 예측 결과 생성, Validation 예측 평가  
- **ESSOptimizationSkill**: DecisionAgent ESS peak shaving 스케줄 및 요약  
- **TariffAnalysisSkill**: DecisionAgent TOU 요금 절감 시뮬레이션 및 LLM 프롬프트 반영

---

## 4. 연동 요약 (구현된 내용)

- **ForecastPlannerAgent**: `_fallback_plan()`에서 `EnergyForecastSkill.select_model(n_records, prosumer_type)` 사용.
- **LoadForecastAgent** / **PVForecastAgent**: 검증 예측 후 `EnergyForecastSkill.build_forecast_result(...)`로 `forecast_df` 생성.
- **ValidationAgent**: Load/PV/NetLoad 각각에 `EnergyForecastSkill.evaluate_forecast()` 호출 후 `skill_mape`, `skill_rmse`를 metrics에 추가.
- **DecisionAgent**:  
  - `ESSOptimizationSkill`로 `peak_shaving_schedule()`·`summarize()` 호출 → ESS 스케줄·요약.  
  - `TariffAnalysisSkill.cost_saving_simulation(tariff_df, ess_schedule_df)` 호출 → 절감 결과를 LLM 프롬프트와 `decisions["tariff_saving"]`에 반영.
