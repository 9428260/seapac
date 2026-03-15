# Skills 사용 및 데이터 수정 가이드

이 문서는 **ALFP 스킬(skills) 사용 방법**과 **학습/테스트 데이터 수정·재생성 방법**을 정리합니다.

---

# Part 1. Skills 사용 방법

## 1.1 스킬이 자동으로 쓰이는 곳

파이프라인(`run_forecast.py` 또는 `alfp.main.run`)을 실행하면 아래 에이전트가 **자동으로** 해당 스킬을 호출합니다.

| 스킬 | 호출하는 에이전트 | 호출 시점 |
|------|-------------------|-----------|
| **EnergyForecastSkill** | ForecastPlannerAgent | LLM 실패 시 `select_model()`로 모델 선택 |
| **EnergyForecastSkill** | LoadForecastAgent, PVForecastAgent | 예측 후 `build_forecast_result()`로 결과 DataFrame 생성 |
| **EnergyForecastSkill** | ValidationAgent | Load/PV/NetLoad 각각 `evaluate_forecast()` 호출 후 metrics에 skill_mape, skill_rmse 추가 |
| **ESSOptimizationSkill** | DecisionAgent | `peak_shaving_schedule()`로 ESS 스케줄 생성, `summarize()`로 요약 |
| **TariffAnalysisSkill** | DecisionAgent | `cost_saving_simulation()`으로 요금 절감 시뮬레이션 후 LLM 프롬프트·decisions에 반영 |

별도 설정 없이 **학습/예측 파이프라인만 돌리면** 위 스킬들이 사용됩니다.

---

## 1.2 스크립트·노트북에서 스킬 직접 호출

에이전트를 거치지 않고 스킬만 쓰고 싶을 때는 아래처럼 import 후 호출하면 됩니다.

### EnergyForecastSkill

```python
from alfp.skills.energy_forecast import EnergyForecastSkill
import numpy as np
import pandas as pd

# 모델 선택 (데이터 수, 프로슈머 타입)
model_name = EnergyForecastSkill.select_model(n_samples=6000, prosumer_type="Residential")
# → "lgbm" (5000 이상이면 lgbm)

# 예측 평가 (실제값, 예측값)
actual = np.array([10.0, 20.0, 30.0])
predicted = np.array([10.5, 19.2, 31.0])
metrics = EnergyForecastSkill.evaluate_forecast(actual, predicted)
# → {"mape": ..., "rmse": ...}

# 예측 결과 DataFrame 생성
ts = pd.Series(pd.date_range("2026-05-01", periods=3, freq="15min"))
forecast_df = EnergyForecastSkill.build_forecast_result(
    ts, pd.Series(actual), predicted,
    target_col="load_kw", pred_col="predicted_load_kw",
)
```

### ESSOptimizationSkill

```python
from alfp.skills.ess_optimization import ESSOptimizationSkill
import pandas as pd

skill = ESSOptimizationSkill(bess_kwh_cap=50.0, bess_kw_cap=25.0, dt_h=0.25)
net_load = pd.Series([30.0, 25.0, 40.0, 15.0])
timestamps = pd.Series(pd.date_range("2026-05-01", periods=4, freq="15min"))

# Peak Shaving 스케줄
schedule_df = skill.peak_shaving_schedule(net_load, timestamps, peak_limit_kw=28.0)

# TOU 요금 기반 스케줄 (가격 시리즈 필요)
# schedule_df = skill.tou_schedule(net_load, timestamps, price_series)

summary = skill.summarize(schedule_df)
# → {"charge_steps", "discharge_steps", "idle_steps", "total_charged_kwh", ...}
```

### TariffAnalysisSkill

```python
from alfp.skills.tariff_analysis import TariffAnalysisSkill
import pandas as pd

skill = TariffAnalysisSkill()
df = pd.DataFrame({
    "timestamp": pd.date_range("2026-05-01", periods=96, freq="15min"),
    "load_kw": [20.0] * 96,
    "price_buy": [100.0] * 96,
})

# TOU 구간 분류·추정 비용 추가
analyzed = skill.analyze(df)

# 구간별 통계
by_period = skill.summarize_by_period(df)

# ESS 스케줄 적용 시 절감 시뮬레이션 (ess_schedule은 timestamp, action, power_kw 컬럼 필요)
# saving = skill.cost_saving_simulation(df, ess_schedule_df)
```

---

## 1.3 스킬 동작 수정·확장 방법

### 기존 스킬 로직 변경

- **EnergyForecastSkill** (`alfp/skills/energy_forecast.py`)  
  - `select_model()`: 5_000 기준값, 프로슈머 타입별 분기 변경 시 해당 메서드 내 조건 수정.
  - `evaluate_forecast()`: MAPE/RMSE 계산 방식 변경 시 해당 메서드 수정.
  - `build_forecast_result()`: 반환 컬럼을 바꾸려면 이 메서드와 이를 쓰는 Load/PV 에이전트의 기대 컬럼을 맞춰야 함.

- **ESSOptimizationSkill** (`alfp/skills/ess_optimization.py`)  
  - `__init__`: `bess_kwh_cap`, `min_soc_pct`, `max_soc_pct`, `dt_h` 등 기본값 변경.
  - `peak_shaving_schedule()`: 피크 제한·충방전 조건 변경.
  - `tou_schedule()`: 요금 구간(quantile) 또는 충방전 규칙 변경.

- **TariffAnalysisSkill** (`alfp/skills/tariff_analysis.py`)  
  - `TOU_PERIODS`: 시간대·배율 수정 시 해당 딕셔너리 변경.
  - `analyze()`, `cost_saving_simulation()`: 비용 식(0.25 = 15분 단위) 등 수정.

### 새 스킬 추가

1. `alfp/skills/` 아래에 새 파일 추가 (예: `my_skill.py`).
2. 클래스 또는 함수로 인터페이스 정의.
3. 사용할 에이전트에서 `from alfp.skills.my_skill import ...` 후 호출.
4. 필요하면 `alfp/skills/README_SKILLS_USAGE.md`에 새 스킬와 사용처를 정리.

---

# Part 2. 데이터 수정 방법

## 2.1 데이터 흐름 개요

```
ods132.csv (원시)
    → prepare_elia_raw.py → elia_raw.csv
                                ↓
case141.m ──→ build_ieee141_from_matpower.py → IEEE141_grid.pkl
                                ↓
    build_train_test_datasets.py (elia_raw + grid pkl 사용)
        → train_2026_seoul.pkl, test_2026may_seoul.pkl
```

- **학습/테스트 pkl**은 `elia_raw.csv`의 **기간 필터**와 **IEEE141_grid.pkl**, **converter 내 PROSUMER_TABLE·TYPE_SPECS**에 의해 결정됩니다.
- **데이터 수정**은 (1) 기간/시간대, (2) 프로슈머 목록·타입·용량, (3) 원시 ELIA/그리드 소스를 바꾼 뒤 **스크립트 재실행**으로 반영합니다.

---

## 2.2 학습·테스트 기간·시간대 변경

**파일**: `data/build_train_test_datasets.py`

- **학습 기간**: 현재는 2026년 전체. 다른 연도/기간으로 바꾸려면 `elia_2026` 필터를 수정합니다.

  ```python
  # 예: 2025년 전체를 학습으로
  elia_train = elia_full[(elia_full["timestamp"].dt.year == 2025)].copy()
  ```

- **테스트 기간**: 현재는 2026년 5월 1개월. 다른 월/연도로 바꾸려면 `elia_2026may` 필터와 출력 파일명을 수정합니다.

  ```python
  # 예: 2026년 6월을 테스트로
  elia_test = elia_full[
      (elia_full["timestamp"].dt.year == 2026) &
      (elia_full["timestamp"].dt.month == 6)
  ].copy()
  # TEST_OUTPUT = DATA_DIR / "test_2026jun_seoul.pkl" 등으로 변경
  ```

- **시간대**: `TIMEZONE = "Asia/Seoul"` 를 바꾸면 학습/테스트 pkl의 타임스탬프가 해당 시간대로 생성됩니다 (converter가 `target_tz`로 변환).

**실행** (data 디렉터리 기준):

```bash
cd data
python3 build_train_test_datasets.py
```

---

## 2.3 프로슈머 목록·타입·용량 수정

**파일**: `data/elia_ieee141_reproduction_converter.py`

- **프로슈머 목록 (버스 ID·타입)**  
  - `PROSUMER_TABLE` 리스트를 수정합니다.  
  - 한 행: `(버스ID, "타입명", has_cdg, has_wt, has_pv, has_bess, has_cl)`  
  - 타입명은 `TYPE_SPECS`에 정의된 키와 같아야 합니다 (Residential, Commercial, Industrial, Rural, EnergyHub).

  ```python
  # 예: 버스 99를 EnergyHub로 추가
  PROSUMER_TABLE = [
      # ... 기존 20개 ...
      (99, "EnergyHub", 1, 1, 1, 1, 1),
  ]
  ```

- **타입별 용량·부하 스케일**  
  - `TYPE_SPECS` 딕셔너리를 수정합니다.  
  - 키: 타입명, 값: `pv_kw_cap`, `wt_kw_cap`, `bess_kwh_cap`, `bess_kw_cap`, `cl_kw_cap`, `cdg_kw_cap`, `load_scale`.

  ```python
  TYPE_SPECS = {
      "Residential": {"pv_kw_cap": 6.0, "wt_kw_cap": 0.0, ...},
      # load_scale만 0.6 → 0.7로 변경 등
  }
  ```

수정 후 **학습/테스트 pkl을 다시 만들어야** 반영됩니다.  
converter를 직접 돌리는 경우:

```bash
cd data
python3 elia_ieee141_reproduction_converter.py
```

학습/테스트만 다시 만들 경우:

```bash
cd data
python3 build_train_test_datasets.py
```

(`build_train_test_datasets.py`는 converter의 `build_dataset_from_elia_df`·`build_prosumer_table()`을 사용하므로, PROSUMER_TABLE·TYPE_SPECS 수정이 그대로 반영됩니다.)

---

## 2.4 ELIA 원시 데이터 교체

- **ods132.csv**를 새 파일로 교체한 뒤:

  ```bash
  cd data
  python3 prepare_elia_raw.py
  ```

  → `elia_raw.csv`가 새 컬럼명·구분자로 다시 생성됩니다.  
  - 컬럼명 매핑을 바꾸려면 `prepare_elia_raw.py`의 `RENAME` 딕셔너리를 수정합니다.

- **elia_raw.csv**를 다른 기간/출처 파일로 **완전히 교체**해도 됩니다.  
  - CSV 형식: `datetime`, `resolutioncode`, `afrrbeup`, `mfrrbesaup`, `mfrrbedaup`, `afrrbedown`, `mfrrbesadown`, `mfrrbedadown` 등 converter가 기대하는 컬럼이 있으면 됩니다.  
  - 이후 `build_train_test_datasets.py`에서 사용하는 기간 필터만 새 데이터에 맞게 조정합니다.

---

## 2.5 그리드(IEEE141) 변경

- **다른 case 파일 사용**  
  - `data/build_ieee141_from_matpower.py`에서 `CASE141_PATH` 또는 파싱 대상을 다른 MATPOWER case로 바꾸고, 컬럼 매핑이 맞는지 확인한 뒤:

  ```bash
  cd data
  python3 build_ieee141_from_matpower.py
  ```

  → `IEEE141_grid.pkl`이 새 계통으로 갱신됩니다.

- **최소 20버스 그리드**  
  - `data/create_ieee141_grid_pkl.py`는 PROSUMER_TABLE의 버스만 포함한 최소 pkl을 만듭니다.  
  - 이 스크립트로 생성한 pkl을 쓰면, converter/build_train_test는 동일한 PROSUMER_TABLE과 TYPE_SPECS를 사용합니다.

---

## 2.6 학습/테스트 pkl 재생성 체크리스트

| 목적 | 수정 위치 | 재실행 |
|------|-----------|--------|
| 학습·테스트 **기간** 변경 | `build_train_test_datasets.py` (기간 필터, 출력 파일명) | `python3 build_train_test_datasets.py` |
| **시간대** 변경 | `build_train_test_datasets.py` (`TIMEZONE`) | `python3 build_train_test_datasets.py` |
| **프로슈머 수·버스·타입** 변경 | `elia_ieee141_reproduction_converter.py` (`PROSUMER_TABLE`) | `python3 build_train_test_datasets.py` (또는 converter 직접 실행) |
| **타입별 용량·load_scale** 변경 | `elia_ieee141_reproduction_converter.py` (`TYPE_SPECS`) | `python3 build_train_test_datasets.py` (또는 converter 직접 실행) |
| **ELIA 원시** 교체 | `prepare_elia_raw.py` 입력 파일 또는 `RENAME` | `prepare_elia_raw.py` → `build_train_test_datasets.py` |
| **그리드** 교체 | `build_ieee141_from_matpower.py` 또는 `create_ieee141_grid_pkl.py` | 해당 스크립트 → `build_train_test_datasets.py` |

---

## 2.7 ALFP에서 사용하는 데이터 경로

- **run_forecast.py** 기본값:  
  - `--data data/train_2026_seoul.pkl`  
  - `--prosumer bus_48_Commercial` 등 (pkl 안 `prosumer_id`와 일치해야 함).

- 데이터를 수정해 **새 pkl**을 만들었다면:

  ```bash
  python run_forecast.py --data data/train_2026_seoul.pkl --prosumer bus_62_Residential
  # 또는
  python run_forecast.py --data data/test_2026may_seoul.pkl --prosumer bus_74_EnergyHub
  ```

  처럼 `--data`로 새 파일을 지정하면 됩니다.

---

# 요약

- **Skills**: 파이프라인 실행 시 자동 사용됨. 스크립트/노트북에서는 `from alfp.skills.... import ...` 후 위 예시처럼 호출. 동작 변경·확장은 해당 스킬 파일 수정 또는 새 스킬 추가.
- **데이터 수정**: 기간/시간대는 `build_train_test_datasets.py`, 프로슈머/용량은 `elia_ieee141_reproduction_converter.py`의 PROSUMER_TABLE·TYPE_SPECS, 원시 데이터는 `prepare_elia_raw.py`·elia_raw.csv, 그리드는 `build_ieee141_from_matpower.py` 등으로 수정한 뒤 표에 맞게 스크립트를 재실행하면 됩니다.
