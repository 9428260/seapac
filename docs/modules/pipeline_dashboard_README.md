# Pipeline Dashboard — 아키텍처 단계별 결과 UI

파이프라인 실행 결과를 SQLite에 저장하고, 웹 UI로 단계별 결과를 확인할 수 있습니다.

## 아키텍처 단계 (저장·표시 순서)

1. **ALFP decision** — LangGraph 부하 예측 및 운영 의사결정  
2. **MESA Simulation Engine** — 커뮤니티 멀티 에이전트 시뮬레이션  
3. **Step2 State Translator** — Mesa 상태 → LLM용 JSON  
4. **Step3 AgentScope Multi-Agent Decision** — 다중 에이전트 결정  
5. **Step4 Action Execution Engine** — 정책 검증 및 Mesa 반영 실행  
6. **Step5 Evaluation Engine** — KPI·등급 평가  
7. **(MESA next)** — Step4 내부에서의 Mesa 재실행 (병렬 모드 시 별도 스테이지로 표시)  
8. **Parallel Agents** — Policy / EcoSaver / Storage 병렬 검증 (`--use-parallel` 시)

## 데이터베이스

- **위치**: `output/pipeline_runs.db` (기본). `run_full_pipeline.py --output-dir <dir>` 사용 시 `<dir>/pipeline_runs.db`
- **테이블**:
  - `pipeline_run`: 실행별 메타 (시작/종료 시각, 상태, 소요 시간, 인자)
  - `pipeline_stage`: 단계별 결과 (이름, 성공 여부, 소요 시간, 요약 JSON)

파이프라인을 실행하면 자동으로 위 DB에 기록됩니다.

## UI 실행 방법

```bash
# 가상환경 활성화 후
cd /path/to/ai_master_2603_ai

# DB가 저장된 디렉터리 지정 (선택, 기본: output)
export PIPELINE_DB_DIR=output

# Flask 앱 실행
python -m pipeline_dashboard.app
```

브라우저에서 **http://127.0.0.1:5000** 접속.

- **새 파이프라인 실행**: 상단 폼에서 `테스트 범위`를 선택합니다.
- `전력 사용량 예측 테스트`: ALFP의 예측/검증까지만 실행하며, LLM은 `forecast` 모드로 예측 계획 단계에서만 사용됩니다.
- `전체 아키텍처 실행`: 데이터(`test_2026may_seoul.pkl` 등) 선택, 프로슈머·스텝(1일=96) 기준으로 ALFP → MESA → Step2~5 → Parallel Agents 를 실행합니다.
- **실행 이력**: 최근 파이프라인 실행 목록 (ID, 실행 시각, 상태, 소요 시간)
- **상세**: Run ID 클릭 시 해당 실행의 아키텍처 단계별 결과 (요약·소요 시간·에러)

## 의존성

- `flask` — `requirements.txt`에 포함. 설치: `pip install -r requirements.txt`
