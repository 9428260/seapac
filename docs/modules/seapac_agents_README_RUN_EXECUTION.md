# Step 4 Action Execution — 시뮬레이션 결과를 받아서 실행하는 방법

`run_execution.py`는 **decisions**(ALFP 또는 Step 3 에이전트 결정)를 받아 **정책 검증 → Mesa 시뮬레이션**을 수행하고, 그 결과를 Step 5 Evaluation용으로 출력합니다.

---

## 파일 위치

- **실행 스크립트**: `simulation/run_execution.py`  
- **실행 엔진**: `seapac_agents/execution.py` (`run_execution`, `ExecutionResult`)

---

## 1. 실행 방법 (CLI)

프로젝트 루트에서 실행하세요.

### 방법 A: ALFP에서 decisions 생성 후 바로 실행

ALFP 파이프라인을 돌려 `decisions`를 만든 뒤, 같은 과정에서 Mesa 시뮬레이션까지 수행합니다.

```bash
python simulation/run_execution.py --use-alfp
```

옵션 예:

```bash
python simulation/run_execution.py --use-alfp \
  --data data/train_2026_seoul.pkl \
  --steps 96 \
  --phase 4 \
  --prosumers bus_48_Commercial \
  --output-dir output \
  --save-csv
```

- `--data`: 시뮬레이션용 pkl 경로  
- `--steps`: 시뮬레이션 스텝 수 (기본 96)  
- `--phase`: Mesa phase 1~4 (기본 4)  
- `--prosumers`: 프로슈머 ID 목록 (생략 시 전체)  
- `--output-dir`: 결과 저장 디렉터리  
- `--save-csv`: 시계열 CSV 저장 (Step 5 입력용)

### 방법 B: 저장된 decisions JSON으로 실행 (시뮬레이션 결과 재사용)

이미 만들어 둔 decisions(예: ALFP 또는 시뮬레이션 전 단계에서 저장)가 있으면, 그 파일을 넘겨서 실행할 수 있습니다.

```bash
python simulation/run_execution.py --decisions-file output/decisions.json
```

JSON 형식은 다음 중 하나면 됩니다.

- `{"decisions": { ... } }` → `decisions` 키 사용  
- `{ ... }` → 전체 객체를 decisions로 사용  

decisions 안에는 ALFP와 동일한 구조가 있으면 됩니다  
(`ess_schedule`, `trading_recommendations`, `demand_response_events` 등).

---

## 2. 시뮬레이션 결과를 “받아서” 쓰는 흐름

- **입력**: `decisions` (ALFP에서 생성하거나, JSON 파일로 로드)  
- **실행**: `run_execution()`이 정책 검증 후 Mesa 시뮬레이션을 돌림  
- **출력**: **시뮬레이션 결과** = `ExecutionResult`  
  - `result.summary`: Mesa 요약 지표 (부하, ESS 절감, 거래 등)  
  - `result.dataframe`: 스텝별 시계열 DataFrame  
  - `result.approved`, `result.validation_errors`: 검증 결과  

즉, “시뮬레이션 결과를 받아서 실행”은 다음 두 가지로 이해하면 됩니다.

1. **decisions를 받아서** Step 4 실행(검증 + Mesa)을 한 번 수행하고, 그 결과를 사용한다.  
2. **이미 저장된 decisions 파일**을 `--decisions-file`로 넘겨, 같은 decisions로 시뮬레이션을 다시 실행한다.

---

## 3. 시뮬레이션 결과 저장 (Step 5용)

실행 결과(시뮬레이션 요약 + 시계열)를 파일로 남기려면:

```bash
python simulation/run_execution.py --use-alfp --output-dir output --save-csv
```

생성 파일:

- `output/execution_summary.json`: Mesa 요약 지표 (Step 5 `execution_summary` 입력)  
- `output/execution_timeseries.csv`: 스텝별 시계열 (Step 5 `execution_df` 입력)

---

## 4. Python에서 시뮬레이션 결과 받기

CLI 대신 코드에서 직접 호출해 시뮬레이션 결과를 받을 수 있습니다.

```python
from seapac_agents.execution import run_execution

# decisions는 ALFP run_pipeline() 결과 또는 JSON 로드
decisions = {...}  # ess_schedule, trading_recommendations, demand_response_events 등

result = run_execution(
    decisions,
    data_path="data/train_2026_seoul.pkl",
    n_steps=96,
    phase=4,
    prosumer_ids=["bus_48_Commercial"],
    seed=42,
)

# 시뮬레이션 결과
print(result.approved)
print(result.summary)       # Mesa 요약
print(result.dataframe)     # 시계열 DataFrame
print(result.validation_errors)
```

Step 5 Evaluation에는 `result.summary`와 `result.dataframe`을 넘기면 됩니다.

---

## 5. simulation/run_simulation.py와의 차이

| 구분 | simulation/run_simulation.py | simulation/run_execution.py |
|------|------------------------------|---------------------------------|
| 목적 | Phase 1~4 Mesa 시뮬레이션 실행·비교 | Step 4: 정책 검증 + Mesa 실행 (PRD 플로우) |
| 입력 | `--use-alfp` 시 내부에서 ALFP 호출 | `--use-alfp` 또는 `--decisions-file` |
| 출력 | 콘솔 요약, Phase 비교 테이블 | ExecutionResult → summary/json, CSV (Step 5용) |

- **run_simulation.py**: 시뮬레이션만 돌리고 결과를 콘솔/비교 테이블로 확인할 때 사용.  
- **simulation/run_execution.py**: decisions를 받아 검증 후 시뮬레이션을 돌리고, 그 **시뮬레이션 결과**를 Step 5에 넘길 때 사용.

---

## 요약

- **실행**: `python simulation/run_execution.py --use-alfp` 또는 `--decisions-file <JSON>`  
- **시뮬레이션 결과**: `ExecutionResult.summary` + `ExecutionResult.dataframe`  
- **저장**: `--output-dir` + `--save-csv`로 Step 5 입력용 파일 생성  

decisions는 ALFP에서 생성하거나, 이전에 저장한 JSON을 `--decisions-file`로 넘기면 됩니다.
