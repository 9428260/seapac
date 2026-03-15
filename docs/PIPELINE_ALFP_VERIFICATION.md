# [ALFP decision] → MESA → Step2~5 → MESA next — 구현 여부 및 실행 방법

요청하신 흐름:

```
[ALFP decision]
        ↓
[MESA Simulation Engine]
        ↓
Step2 State Translator
        ↓
Step3 AgentScope Multi-Agent Decision
        ↓
Step4 Action Execution Engine
        ↓
Step5 Evaluation Engine
        ↓
[MESA next simulation step]
```

---

## 1. 구현 여부 요약

| 단계 | 구현 | 비고 |
|------|------|------|
| **[ALFP decision]** | ✅ | `alfp.pipeline.graph.run_pipeline()` → `result["decisions"]` |
| **MESA Simulation Engine** | ✅ | `simulation/model.py` — `ALFPSimulationModel` |
| **Step2 State Translator** | ✅ | `seapac_agents/state_translator.py` |
| **Step3 AgentScope Multi-Agent Decision** | ✅ | `seapac_agents/decision.py` — `run_agentscope_decision_series()` |
| **Step4 Action Execution Engine** | ✅ | `seapac_agents/execution.py` — `run_execution()` |
| **Step5 Evaluation Engine** | ✅ | `seapac_agents/evaluation.py` — `evaluate_from_execution_result()` |
| **MESA next simulation step** | ✅ | Step4 내부에서 `ALFPSimulationModel(alfp_decisions=...).run()` |

**단, 위 순서대로 한 번에 도는 “단일 진입점”은 없습니다.**

- **run_agentic_pipeline.py**: **ALFP를 사용하지 않음**. Mesa(무결정) → Step2 → Step3 → Step4 → Step5 만 수행.
- **simulation/run_execution.py --use-alfp**: **[ALFP decision] → Step4(MESA 실행)** 만 수행. Step2·Step3는 거치지 않고, Step5는 별도 실행 가능.

따라서 **“[ALFP decision] → MESA → Step2 → Step3 → Step4 → Step5 → MESA next” 전체가 한 스크립트로 연결된 상태는 아직 없고**,  
아래 두 가지 실행 방식으로 나눠서 검증·실행할 수 있습니다.

---

## 2. 실행 방법

### 방법 A: ALFP decision → Step4(MESA) → Step5 (현재 구현된 경로)

**[ALFP decision]** 을 넣고 **MESA(Step4)** 를 돌린 뒤 **Step5 Evaluation** 까지 수행하는 경로입니다.  
Step2·Step3는 포함되지 않습니다.

```bash
# 1) ALFP decisions 생성 후 Step4 실행 (MESA에 decisions 반영) + 결과 저장
python simulation/run_execution.py --use-alfp \
  --data data/train_2026_seoul.pkl \
  --steps 96 \
  --phase 4 \
  --output-dir output \
  --save-csv
```

이렇게 하면:

- **[ALFP decision]**: 내부에서 `run_pipeline()` 호출로 decisions 생성
- **[MESA Simulation Engine] / [MESA next step]**: Step4 `run_execution()` 안에서 `ALFPSimulationModel(alfp_decisions=decisions).run()` 로 한 번 실행
- **Step5** 입력용 파일 생성: `output/execution_summary.json`, `output/execution_timeseries.csv`

Step5 평가까지 하려면 같은 출력을 사용해 Python에서 한 번 더 호출합니다:

```bash
# 2) Step5 Evaluation (방금 만든 실행 결과로)
python -c "
from seapac_agents.evaluation import run_evaluation, EvaluationConfig
import json
with open('output/execution_summary.json') as f:
    summary = json.load(f)
import pandas as pd
df = pd.read_csv('output/execution_timeseries.csv')
report = run_evaluation(execution_summary=summary, execution_df=df, config=EvaluationConfig())
report.print_report()
"
```

또는 `evaluate_from_execution_result(result, ...)` 를 쓰려면, Step4를 Python에서 호출해 `ExecutionResult` 를 받은 뒤 그대로 Step5에 넘기면 됩니다.

---

### 방법 B: MESA → Step2 → Step3 → Step4 → Step5 (ALFP 없이 전체 파이프라인)

**[ALFP decision]** 은 쓰지 않고, **MESA → Step2 → Step3 → Step4 → Step5** 와 **MESA next**(Step4 내 재실행)까지 한 번에 돌리는 방법입니다.

```bash
python -m seapac_agents.run_agentic_pipeline \
  --data-path data/train_2026_seoul.pkl \
  --steps 96 \
  --phase 4 \
  --output-dir output \
  --save-json \
  --verbose
```

흐름:

1. **MESA Simulation Engine**: Mesa를 decisions 없이 1회 실행 → DataFrame
2. **Step2 State Translator**: `translate_dataframe(df)` → state JSON 리스트
3. **Step3 AgentScope Multi-Agent Decision**: `run_agentscope_decision_series()` → decisions
4. **Step4 Action Execution Engine**: `run_execution(decisions, ...)` → MESA를 decisions와 함께 재실행 (**MESA next**)
5. **Step5 Evaluation Engine**: `evaluate_from_execution_result(result, ...)` → KPI·등급 출력

---

### 방법 C: ALFP decision을 JSON으로 저장한 뒤 Step4·Step5만 실행

ALFP는 따로 돌려서 decisions만 저장하고, 그 다음 Step4·Step5만 반복 실행하고 싶을 때 사용합니다.

```bash
# 1) ALFP만 실행해 decisions 저장 (예: 스크립트나 alfp.main.run() 사용)
python -m alfp.main --prosumer bus_48_Commercial --data data/train_2026_seoul.pkl --horizon 96
# → 수동으로 output/decisions.json 에 decisions 부분 저장하거나,
#    run_execution --use-alfp 한 번 실행 후 output에서 decisions 복사

# 2) 저장된 decisions로 Step4 실행
python simulation/run_execution.py --decisions-file output/decisions.json \
  --data data/train_2026_seoul.pkl --steps 96 --phase 4 \
  --output-dir output --save-csv
```

이후 Step5는 방법 A와 동일하게 `output/execution_summary.json` 과 `output/execution_timeseries.csv` 로 실행하면 됩니다.

---

## 3. 요약 표

| 목적 | 실행 방법 |
|------|------------|
| **[ALFP decision] → MESA(Step4) → (저장) → Step5** | `python simulation/run_execution.py --use-alfp --output-dir output --save-csv` 후, 출력으로 Step5 호출 |
| **MESA → Step2 → Step3 → Step4 → Step5 (ALFP 없음)** | `python -m seapac_agents.run_agentic_pipeline --output-dir output --save-json` |
| **저장된 ALFP decisions로 Step4·Step5** | `python simulation/run_execution.py --decisions-file output/decisions.json ...` 후 Step5 |

---

## 4. “[ALFP decision] → MESA → Step2 → Step3 → Step4 → Step5” 를 한 번에 돌리려면

현재 구조만으로는 **한 스크립트**에서 위 순서를 모두 수행하는 진입점은 없습니다.  
원하시면 **run_agentic_pipeline.py** 에 `--use-alfp` 옵션을 추가해 다음처럼 만들 수 있습니다:

1. ALFP 실행 → decisions 수집  
2. Mesa를 **ALFP decisions** 로 1회 실행 → DataFrame  
3. `translate_dataframe(df)` → Step2  
4. `run_agentscope_decision_series(state_list)` → Step3 (선택: 사용할 최종 decisions를 ALFP vs Step3 중 선택)  
5. `run_execution(선택한 decisions)` → Step4 (MESA next)  
6. `evaluate_from_execution_result(result)` → Step5  

이렇게 하면 “[ALFP decision] → MESA → Step2 → Step3 → Step4 → Step5 → MESA next” 전체를 한 번에 실행할 수 있습니다.
