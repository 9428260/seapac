# Step 4 — Action Execution Engine (실행 단계)

PRD: [seapac_agentic_prd.md](../prd/seapac_agentic_prd.md) Step 4

**실행 스크립트와 실행 엔진은 `seapac_agents`로 이전되었습니다.**  
자세한 사용법은 **[seapac_agents_README_RUN_EXECUTION.md](../modules/seapac_agents_README_RUN_EXECUTION.md)** 를 참고하세요.

## 구성 (seapac_agents)

| 구성요소 | 파일 | 설명 |
|----------|------|------|
| 액션 타입·검증·실행 | `seapac_agents/execution.py` | ESSAction, TradeAction, DemandResponseAction, 정책 검증, `run_execution()` |
| 실행 단계 CLI | `simulation/run_execution.py` | decisions 로드 후 실행 → 결과 출력·저장 |

## 실행 예시

```bash
# 프로젝트 루트에서 실행
# ALFP에서 decisions 생성 후 실행
python simulation/run_execution.py --use-alfp

# 저장된 decisions JSON으로 실행
python simulation/run_execution.py --decisions-file output/decisions.json

# 결과 저장 (Step 5 입력용)
python simulation/run_execution.py --use-alfp --output-dir output --save-csv
```

## 코드에서 사용

```python
from seapac_agents.execution import run_execution, ExecutionResult

decisions = { "ess_schedule": [...], "trading_recommendations": [...], "demand_response_events": [...] }
result: ExecutionResult = run_execution(decisions, data_path="...", n_steps=96, phase=4)
print(result.summary)
# result.dataframe → Step 5 KPI 계산
```
