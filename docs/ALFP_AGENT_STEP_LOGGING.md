# ALFP Agent별 Langchain DeepAgent 단계 로깅 설계

[ALFP] 부하 예측 및 운영 의사결정 단계 실행 시, Agent별 Langchain DeepAgent 사용 단계를 DB에 기록하고 Dashboard UI에서 조회할 수 있도록 한 설계입니다.

## 1. 개요

- **대상 단계**: [ALFP] 부하 예측 및 운영 의사결정 (전체 파이프라인 중 Stage 1)
- **기록 대상**: LangGraph 내 각 노드(Agent) 실행 단계
  - data_loader, data_quality, feature_engineering, forecast_planner, load_forecast, pv_forecast, net_load_forecast, validation, replan, decision, save_memory
- **저장소**: SQLite `pipeline_runs.db` 테이블 `alfp_agent_step`
- **조회**: Pipeline Dashboard Run 상세 → 탭 "1. [ALFP] 부하 예측 및 운영 의사결정" → "Langchain DeepAgent 단계" 섹션

## 2. 데이터 흐름

```
[Dashboard UI 실행]
  → POST /api/run → create_run() → run_id 생성
  → subprocess run_full_pipeline (env: PIPELINE_RUN_ID, PIPELINE_DB_DIR)

[run_full_pipeline main]
  → run_id = int(PIPELINE_RUN_ID), db_path = get_db_path(output_dir)
  → stage_alfp(args, run_id=run_id, db_path=db_path)

[alfp.main.run]
  → run_pipeline(..., run_id=run_id, db_path=db_path)

[alfp.pipeline.graph.run_pipeline]
  → run_id/db_path 있으면 step_logger = add_agent_step 래퍼 생성
  → initial_state["_logging_ctx"] = { run_id, stage_order=1, db_path }
  → initial_state["_agent_step_order"] = 0
  → build_pipeline(step_logger) → 각 노드를 _wrap_node_for_logging 으로 래핑

[각 노드 실행 시 _wrap_node_for_logging]
  → 시작 시각 기록
  → node_func(state) 실행
  → 종료 시각, elapsed_sec, summary 수집
  → step_logger(run_id, stage_order=1, agent_name, step_order, started_at, finished_at, elapsed_sec, ok, summary, error_text, db_path)
  → pipeline_dashboard.db.add_agent_step() → INSERT INTO alfp_agent_step

[UI 조회]
  → GET /runs/<run_id> → run_detail()
  → get_alfp_agent_steps(run_id, stage_order=1) → alfp_agent_step 조회
  → run_detail.html 탭1에 "Langchain DeepAgent 단계"로 렌더링
```

## 3. DB 스키마 (alfp_agent_step)

| 컬럼         | 타입    | 설명 |
|-------------|---------|------|
| id          | INTEGER | PK  |
| run_id      | INTEGER | pipeline_run.id (FK) |
| stage_order | INTEGER | ALFP 단계 = 1 |
| agent_name  | TEXT    | 노드명 (예: forecast_planner, decision) |
| step_order  | INTEGER | 실행 순서 (0부터 증가) |
| started_at  | TEXT    | 시작 시각 (UTC) |
| finished_at | TEXT    | 종료 시각 (UTC) |
| elapsed_sec | REAL    | 소요 시간(초) |
| ok          | INTEGER | 1=성공, 0=실패 |
| summary_json| TEXT    | 요약 정보 (JSON) |
| error_text  | TEXT    | 실패 시 오류 메시지 |

## 4. API

- **기록**: `pipeline_dashboard.db.add_agent_step(run_id, stage_order, agent_name, step_order, started_at, finished_at, elapsed_sec, ok, summary, error_text, db_path)`
- **조회**: `pipeline_dashboard.db.get_alfp_agent_steps(run_id, stage_order=1, db_path)` → list[dict]

## 5. UI 표시

- Run 상세 페이지 → **탭 1. [ALFP] 부하 예측 및 운영 의사결정**
- ALFP 스테이지 결과 카드 아래 **"Langchain DeepAgent 단계"** 섹션
- 각 단계: 순서, Agent명, 성공/실패 뱃지, 시작/종료 시각, 소요 시간, 오류 메시지(실패 시), summary 항목

## 6. 참고

- Dashboard에서 실행할 때만 `run_id`/`db_path`가 전달되므로, **UI를 통해 파이프라인을 실행한 Run**에만 Agent 단계 로그가 쌓입니다.
- CLI에서 `python -m run_full_pipeline`만 실행하면 `PIPELINE_RUN_ID`가 없어 Agent 단계는 DB에 기록되지 않습니다.

## 7. 검증 방법

**DB 기록 확인**
- Dashboard에서 파이프라인 실행 후 로그에 `ALFP Agent 단계 로깅 활성화 run_id=... db_path=...` 가 출력되는지 확인.
- `output/pipeline_runs.db` 에서 `SELECT COUNT(*) FROM alfp_agent_step WHERE run_id = ?` 로 해당 run_id의 단계 수 확인 (ALFP 정상 시 10~12개 노드 예상).

**UI 조회 확인**
- Run 상세 페이지 접속 → **탭 1. [ALFP] 부하 예측 및 운영 의사결정** 선택.
- "Langchain DeepAgent 단계" 섹션에 테이블 및 단계별 summary 카드가 표시되는지 확인.
- 단계가 없을 경우 "기록된 Agent 단계가 없습니다. UI를 통해 파이프라인을 실행한 Run에만..." 안내 문구가 보이는지 확인.
