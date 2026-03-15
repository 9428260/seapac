# ALFP → Simulation → Decision 결과 확인 방법

이 문서는 **ALFP 파이프라인**이 생성한 **의사결정(decision)** 결과를 확인하고, **시뮬레이션**에서 어떻게 적용·검증하는지 정리합니다.

---

## 1. 흐름 요약

```
ALFP 파이프라인:
  data_loader → data_quality → feature_engineering → forecast_planner
    → load_forecast → pv_forecast → net_load_forecast → validation
      → (KPI OK) decision → save_memory → END
      → (KPI 미달) replan → forecast_planner (재시도)
```

- **Decision**은 `validation` 통과 후 실행되며, ESS 스케줄·거래 권고·DR 이벤트·LLM 운영 전략을 생성합니다.
- **Simulation**은 이 `decisions`를 받아 Phase 3(ESS)/Phase 4(거래)에서 스텝별로 적용합니다.

---

## 2. 방법 A: ALFP만 실행해 Decision 결과 확인 (콘솔)

ALFP 파이프라인만 돌리고 **운영 의사결정(DecisionAgent)** 결과를 터미널에서 바로 볼 수 있습니다.

### 실행

```bash
# 프로젝트 루트에서
python -m alfp.main --prosumer bus_48_Commercial --data data/train_2026_seoul.pkl --horizon 96
```

### 출력되는 Decision 섹션

실행 후 **「운영 의사결정 (DecisionAgent)」** 섹션에서 다음이 출력됩니다.

| 항목 | 설명 |
|------|------|
| ESS 스케줄 | 충전/방전/대기 스텝 수 |
| 에너지 거래 | 잉여 이벤트 건수, 총 잉여 kW |
| DR 이벤트 | 건수, 피크 임계값(kW) |
| LLM 경보 수준 | alert_level |
| LLM ESS/거래/DR 전략 | ess_strategy, trading_strategy, dr_strategy |
| LLM 종합 추천 | overall_recommendation |
| 즉시 실행 권고 | priority_actions |
| 예상 절감 효과 | expected_savings |

### Python에서 반환값으로 확인

`alfp.main.run()` 또는 `alfp.pipeline.graph.run_pipeline()`을 호출하면 **dict**로 전체 결과를 받을 수 있습니다. Decision은 `result["decisions"]`에 들어 있습니다.

```python
from alfp.pipeline.graph import run_pipeline

result = run_pipeline(
    prosumer_id="bus_48_Commercial",
    data_path="data/train_2026_seoul.pkl",
    forecast_horizon=96,
)

# Decision 결과 확인
decisions = result.get("decisions", {})
print(decisions.get("ess_summary"))      # ESS 충/방전/대기 스텝
print(decisions.get("trading_summary"))  # 거래 권고 요약
print(decisions.get("dr_summary"))       # DR 이벤트 요약
print(decisions.get("tariff_saving"))    # TOU 절감 시뮬레이션 (base_cost_krw, saving_krw 등)
print(decisions.get("llm_strategy"))     # LLM 운영 전략 (alert_level, ess_strategy 등)
print(decisions.get("ess_schedule"))     # 스텝별 ESS 스케줄 리스트
```

---

## 3. 방법 B: Simulation + ALFP로 Decision 적용 결과 확인

ALFP를 먼저 실행해 **decisions**를 만든 뒤, Mesa 시뮬레이션에 넘겨 **Phase 3(ESS)·Phase 4(거래)** 에서 어떻게 반영되는지 확인합니다.

### 실행

```bash
# Phase 3 (ESS 연동) + ALFP decisions 사용
python simulation/run_simulation.py --phase 3 --use-alfp --data data/train_2026_seoul.pkl --steps 96

# Phase 4 (에너지 거래 연동) + ALFP decisions 사용
python simulation/run_simulation.py --phase 4 --use-alfp --steps 96

# 특정 프로슈머만
python simulation/run_simulation.py --phase 3 --use-alfp --prosumers bus_48_Commercial
```

### 시뮬레이션 출력에서 확인하는 것

- **Phase 3**:  
  - ESS 운영 (총 충전량/방전량, 피크 억제 횟수, ESS 활용률, 최종 SoC, ESS 절감액)
- **Phase 4**:  
  - 에너지 거래 (총 거래 건수, 총 거래량, 판매자 수익, 구매자 절감, 커뮤니티 총 절감, 마켓 수수료)

이 수치는 ALFP의 **ess_schedule**, **trading_recommendations**, **demand_response_events**가 시뮬레이션 스텝별로 적용된 결과입니다.

### 4단계 비교 실행 (Phase 1~4 한 번에)

```bash
python simulation/run_simulation.py --all-phases --use-alfp --steps 96
```

Phase별 KPI 비교 테이블에서 **P3/P4** 열로 ALFP decision 반영 효과를 볼 수 있습니다.

---

## 4. Decision 객체 구조 (참고)

`result["decisions"]` 에 들어 있는 주요 키:

| 키 | 타입 | 설명 |
|----|------|------|
| `ess_schedule` | list[dict] | 스텝별 ESS 스케줄 (timestamp, action, power_kw, soc_kwh, net_load_kw) |
| `ess_summary` | dict | charge_steps, discharge_steps, idle_steps |
| `trading_recommendations` | list[dict] | 잉여 전력 권고 (timestamp, surplus_kw, action 등) |
| `trading_summary` | dict | total_surplus_events, total_surplus_kw |
| `demand_response_events` | list[dict] | DR 이벤트 (timestamp, net_load_kw, recommended_reduction_kw 등) |
| `dr_summary` | dict | peak_threshold_kw, dr_event_count |
| `tariff_saving` | dict | base_cost_krw, adjusted_cost_krw, saving_krw, saving_pct (TOU 절감 시뮬) |
| `llm_strategy` | dict | alert_level, ess_strategy, trading_strategy, dr_strategy, overall_recommendation, priority_actions, expected_savings |

---

## 5. 영구 메모리에서 마지막 Decision 요약 확인

파이프라인 종료 시 **save_memory** 노드에서 현재 런 요약이 저장됩니다.  
마지막 decision 요약은 메모리의 `last_decisions_summary`에 들어 있습니다.

- 저장 위치/형식: `alfp/memory/` 및 `alfp/memory/README.md` 참고.
- 여기에는 `ess_summary`, `tariff_saving`, `dr_summary` 수준의 요약만 저장됩니다.

---

## 요약

| 목적 | 방법 |
|------|------|
| **Decision 내용만 빠르게 보기** | `python -m alfp.main --prosumer bus_48_Commercial` 실행 후 콘솔의 「운영 의사결정」 섹션 확인 |
| **Decision을 코드에서 활용** | `run_pipeline()` 반환값의 `result["decisions"]` 사용 |
| **Simulation에서 Decision 적용 결과 확인** | `python simulation/run_simulation.py --phase 3 또는 4 --use-alfp` 실행 후 Phase별 요약 및 KPI 확인 |
| **Phase 1~4 비교** | `python simulation/run_simulation.py --all-phases --use-alfp` |

데이터 경로·프로슈머·스텝 수는 `--data`, `--prosumer`, `--steps` 등으로 변경하면 됩니다.
