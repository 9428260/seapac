# 전체 파이프라인 구현 여부 및 실행 방법

요청하신 흐름:  
**MESA Simulation Engine → Step2 State Translator → Step3 AgentScope Multi-Agent Decision → Step4 Action Execution → Step5 Evaluation → MESA next simulation step**

---

## 1. 구현 여부 요약

| 단계 | 구현 | 파일/함수 | 비고 |
|------|------|-----------|------|
| **MESA Simulation Engine** | ✅ | `simulation/model.py` — `ALFPSimulationModel`, `step()`, `run()` | 15분 단위 step, DataCollector 수집 |
| **Step 2 State Translator** | ✅ | `seapac_agents/state_translator.py` — `translate_model_state()`, `translate_dataframe()` | Mesa 상태 → LLM용 JSON |
| **Step 3 AgentScope Multi-Agent Decision** | ✅ | `seapac_agents/decision.py` — `run_agentscope_decision_series()` | 5개 에이전트, decisions 생성 |
| **Step 4 Action Execution Engine** | ✅ | `seapac_agents/execution.py` — `run_execution()` | 검증 → Mesa에 decisions 반영 실행 |
| **Step 5 Evaluation Engine** | ✅ | `seapac_agents/evaluation.py` — `evaluate_from_execution_result()`, `EvaluationReport` | KPI·등급·보고서 |
| **MESA next simulation step** | ✅ (배치 방식) | Step 4 내부에서 `ALFPSimulationModel(alfp_decisions=decisions).run()` | decisions를 반영한 Mesa **전체 재실행** |

**참고**: "MESA next simulation step"은 **스텝마다 한 번씩 도는 루프**가 아니라,  
Step 4에서 **decisions를 넣고 Mesa를 처음부터 끝까지 한 번 더 실행**하는 방식으로 구현되어 있습니다.  
즉, Step 1 Mesa(무결정) → Step 2~3 → Step 4에서 Mesa(결정 반영) 재실행 → Step 5 평가 순서입니다.

---

## 2. 한 번에 전체 파이프라인 실행 (권장)

프로젝트 루트에서 아래 한 줄로 **MESA → Step2 → Step3 → Step4 → Step5** 를 모두 실행할 수 있습니다.

```bash
python -m seapac_agents.run_agentic_pipeline
```

### 옵션 예시

```bash
python -m seapac_agents.run_agentic_pipeline \
  --data-path data/train_2026_seoul.pkl \
  --steps 96 \
  --phase 4 \
  --peak-threshold 500 \
  --ess-capacity 200 \
  --grid-price 100 \
  --seed 42 \
  --verbose
```

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--data-path` | Mesa/실행용 데이터 pkl | `data/train_2026_seoul.pkl` |
| `--steps` | 시뮬레이션 스텝 수 (15분 단위) | 96 |
| `--phase` | Mesa phase (1~4) | 4 |
| `--peak-threshold` | 피크 임계값 (kW) | 500 |
| `--ess-capacity` | ESS 용량 (kWh) | 200 |
| `--grid-price` | 계통 단가 (원/kWh) | 100 |
| `--output-dir` | 결과 저장 디렉터리 | (미지정 시 저장 안 함) |
| `--save-json` | state/decisions/evaluation_report JSON 저장 | False |
| `--verbose` | 상세 로그 출력 | False |

### 실행 시 콘솔 흐름

1. **[Step 1] Mesa 시뮬레이션 초기 실행** → `df_initial` 생성  
2. **[Step 2] State Translator** → `state_json_list` 생성  
3. **[Step 3] Multi-Agent Decision Engine** → `decisions` (ESS/거래/DR)  
4. **[Step 4] Action Execution Engine** → 검증 후 **Mesa를 decisions와 함께 재실행** → `ExecutionResult`  
5. **[Step 5] Evaluation Engine** → KPI·등급 출력 (`report.print_report()`)

### 결과 저장

```bash
python -m seapac_agents.run_agentic_pipeline --output-dir output --save-json
```

생성 파일 예:

- `output/state_translations.json` — Step 2 출력
- `output/multi_agent_decisions.json` — Step 3 출력
- `output/execution_timeseries.csv` — Step 4 Mesa 시계열
- `output/evaluation_report.json` — Step 5 평가 보고서

---

## 3. 단계별로 따로 실행하는 방법

### Step 1: Mesa만 실행

```bash
python simulation/run_simulation.py --phase 4 --steps 96 --data data/train_2026_seoul.pkl
```

### Step 2~5만 실행 (이미 Mesa 결과가 있을 때)

Python에서 순서대로 호출:

```python
from simulation.model import ALFPSimulationModel
from seapac_agents.state_translator import translate_dataframe
from seapac_agents.decision import run_agentscope_decision_series
from seapac_agents.execution import run_execution
from seapac_agents.evaluation import evaluate_from_execution_result, EvaluationConfig

# Step 1: Mesa
model = ALFPSimulationModel(phase=4, data_path="data/train_2026_seoul.pkl", n_steps=96, seed=42)
df = model.run()

# Step 2: State Translator
state_list = translate_dataframe(df, peak_threshold_kw=500.0, ess_capacity_kwh=200.0)

# Step 3: Multi-Agent Decision
decisions = run_agentscope_decision_series(state_list, peak_threshold_kw=500.0, max_charge_kw=50.0, max_discharge_kw=50.0)

# Step 4: Action Execution (내부에서 Mesa를 decisions와 함께 재실행)
result = run_execution(decisions, data_path="data/train_2026_seoul.pkl", n_steps=96, phase=4)

# Step 5: Evaluation
report = evaluate_from_execution_result(result, decisions=decisions)
report.print_report()
```

### Step 4 + Step 5만 (decisions가 이미 있을 때)

```bash
# Step 4: decisions 파일로 실행
python simulation/run_execution.py --decisions-file output/multi_agent_decisions.json \
  --data data/train_2026_seoul.pkl --steps 96 --phase 4 \
  --output-dir output --save-csv
```

이후 Step 5는 `output/execution_summary.json`과 `output/execution_timeseries.csv`를 사용해 `evaluate_from_execution_result()` 또는 `run_evaluation()`으로 호출하면 됩니다.

---

## 4. 요약

- **전체 파이프라인**: `python -m seapac_agents.run_agentic_pipeline` 한 번으로 MESA → Step2 → Step3 → Step4 → Step5 까지 모두 실행 가능합니다.
- **MESA next simulation step**: Step 4 `run_execution()` 안에서 **decisions를 반영한 Mesa 전체 실행**이 이루어지므로, “의사결정이 반영된 다음 시뮬레이션”이 구현되어 있습니다.  
  스텝 단위로 매 step마다 Mesa → Step2 → … → Step5 를 도는 **반복 루프**는 현재 없고, **배치 방식(1회 Mesa 무결정 → 1회 Mesa 결정 반영)** 으로 동작합니다.
