"""
LangGraph 파이프라인에서 공유되는 State 타입 정의
"""

from typing import Any, Optional
from typing_extensions import TypedDict


class ALFPState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────
    raw_data: Any                  # 로드된 pkl dict
    prosumer_id: str               # 예측 대상 프로슈머
    forecast_horizon: int          # 예측 스텝 수 (15분 단위)
    data_path: str                 # pkl 파일 경로

    # ── Multi-Step Reasoning / 재계획 ────────────────────
    plan_retry_count: int          # 재계획 횟수 (검증 실패 시 재진입)
    max_plan_retries: int          # 최대 재계획 횟수 (기본 2)
    persistent_memory: dict        # 런 간 영구 메모리 (이전 런 요약)

    # ── DataQualityAgent ──────────────────────────────────
    clean_data: Any                # 정제된 pd.DataFrame
    quality_report: dict           # 품질 검증 결과

    # ── FeatureAgent ──────────────────────────────────────
    feature_df: Any                # feature 포함 pd.DataFrame
    feature_names: list            # 사용할 feature 컬럼명 목록

    # ── ForecastPlannerAgent ──────────────────────────────
    selected_model: str            # "lgbm" | "xgboost"
    model_config: dict             # 모델 하이퍼파라미터
    forecast_plan: dict            # 계획 요약

    # ── LoadForecastAgent ─────────────────────────────────
    load_forecast: Any             # 예측 결과 pd.DataFrame
    load_model: Any                # 학습된 모델 객체

    # ── PVForecastAgent ───────────────────────────────────
    pv_forecast: Any               # PV 예측 pd.DataFrame
    pv_model: Any                  # 학습된 PV 모델

    # ── NetLoadForecastAgent ──────────────────────────────
    net_load_forecast: Any         # Net Load 예측 pd.DataFrame

    # ── ValidationAgent ───────────────────────────────────
    validation_metrics: dict       # MAE, RMSE, MAPE, PeakError

    # ── DecisionAgent ─────────────────────────────────────
    decisions: dict                # ESS / 거래 / DR 추천

    # ── 공통 로그 ─────────────────────────────────────────
    messages: list                 # 에이전트별 실행 로그
    errors: list                   # 오류 로그

    # ── Dashboard Agent 단계 로깅 (run_pipeline에서만 설정) ──
    _logging_ctx: dict             # { run_id, stage_order, db_path }
    _agent_step_order: int         # 다음 노드의 step_order
