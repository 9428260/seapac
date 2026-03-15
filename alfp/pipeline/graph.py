"""
LangGraph 기반 멀티 에이전트 파이프라인 정의

- 조건 분기: Validation 후 KPI 미달 시 재계획(forecast_planner) 또는 의사결정(decision)으로 라우팅
- 추론 루프: 재계획 시 load → pv → net_load → validation 반복 (max_plan_retries 제한)
- 영구 메모리: 파이프라인 시작 시 이전 런 로드, 종료 시 현재 런 요약 저장
- Dashboard 로깅: run_id/db_path 전달 시 Agent별 Langchain DeepAgent 단계를 DB에 기록
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import StateGraph, END

from alfp.agents.state import ALFPState
from alfp.agents.data_quality import data_quality_agent
from alfp.agents.feature_engineering import feature_engineering_agent
from alfp.agents.forecast_planner import forecast_planner_agent
from alfp.agents.load_forecast import load_forecast_agent
from alfp.agents.pv_forecast import pv_forecast_agent
from alfp.agents.net_load_forecast import net_load_forecast_agent
from alfp.agents.validation import validation_agent
from alfp.agents.decision import decision_agent
from alfp.data.loader import load_dataset
from alfp.memory import load_memory, save_memory


def data_loader_node(state: ALFPState) -> ALFPState:
    """데이터 로드 노드. 영구 메모리 로드 후 데이터 로드."""
    log = state.get("messages", [])
    log.append("[DataLoader] 데이터 로드 시작")

    prosumer_id = state.get("prosumer_id", "unknown")
    persistent = load_memory(prosumer_id)
    if persistent:
        log.append(f"[DataLoader] 이전 런 메모리 로드 (last_run: {persistent.get('last_run_at', 'N/A')})")

    data_path = state.get("data_path", "data/train_2026_seoul.pkl")
    raw_data = load_dataset(data_path)

    log.append(f"[DataLoader] 로드 완료: {data_path}")
    return {
        **state,
        "raw_data": raw_data,
        "persistent_memory": persistent,
        "plan_retry_count": state.get("plan_retry_count", 0),
        "max_plan_retries": state.get("max_plan_retries", 2),
        "messages": log,
    }


def _route_after_validation(state: ALFPState) -> str:
    """
    동적 라우팅: 검증 후 재계획(추론 루프) 또는 의사결정으로 분기.
    KPI 미달이고 재시도 횟수 여유가 있으면 'replan', 아니면 'decision'.
    """
    metrics = state.get("validation_metrics") or {}
    kpi = metrics.get("kpi") or {}
    mape_ok = kpi.get("MAPE_pass", True)
    peak_ok = kpi.get("peak_acc_pass", True)
    retry = state.get("plan_retry_count", 0)
    max_retries = state.get("max_plan_retries", 2)

    if (not mape_ok or not peak_ok) and retry < max_retries:
        return "replan"
    return "decision"


def replan_node(state: ALFPState) -> ALFPState:
    """재계획 진입 노드. 재시도 횟수 증가 후 ForecastPlanner로 이동."""
    log = state.get("messages", [])
    retry = state.get("plan_retry_count", 0) + 1
    log.append(f"[Replan] 검증 KPI 미달 → 재계획 시도 ({retry}/{state.get('max_plan_retries', 2)})")
    return {
        **state,
        "plan_retry_count": retry,
        "messages": log,
    }


def _agent_step_summary(node_name: str, out: ALFPState) -> dict[str, Any]:
    """노드별 출력에서 Langchain DeepAgent 단계 로그용 요약을 추출 (JSON 직렬 가능한 값만)."""
    summary: dict[str, Any] = {}
    if out.get("messages"):
        summary["messages_count"] = len(out["messages"])

    if node_name == "data_loader" and out.get("raw_data") is not None:
        raw = out["raw_data"]
        if hasattr(raw, "shape"):
            summary["raw_data_shape"] = list(raw.shape)
        elif isinstance(raw, dict):
            summary["raw_data_keys"] = list(raw.keys())[:10]

    if node_name == "forecast_planner":
        plan = out.get("forecast_plan") or {}
        if plan:
            summary["model_load"] = str(plan.get("model_load", ""))[:80]
            summary["model_pv"] = str(plan.get("model_pv", ""))[:80]
            if plan.get("llm_reasoning"):
                summary["llm_used"] = True

    if node_name == "load_forecast" and out.get("load_forecast") is not None:
        lf = out["load_forecast"]
        if hasattr(lf, "shape"):
            summary["forecast_len"] = int(getattr(lf, "shape", [0])[0]) if getattr(lf, "shape", None) else None

    if node_name == "validation":
        metrics = out.get("validation_metrics") or {}
        kpi = metrics.get("kpi") or {}
        summary["MAPE_pass"] = kpi.get("MAPE_pass")
        summary["peak_acc_pass"] = kpi.get("peak_acc_pass")

    if node_name == "decision":
        dec = out.get("decisions") or {}
        ess = dec.get("ess_schedule") or []
        summary["ess_schedule_count"] = len(ess)
        summary["trading_count"] = len(dec.get("trading_recommendations") or [])
        summary["dr_events_count"] = len(dec.get("demand_response_events") or [])
        if dec.get("llm_strategy"):
            summary["llm_strategy"] = True

    if node_name == "save_memory":
        summary["saved"] = True

    return summary


def _wrap_node_for_logging(
    node_name: str,
    node_func: Callable[[ALFPState], ALFPState],
    step_logger: Callable[..., None],
) -> Callable[[ALFPState], ALFPState]:
    """노드 실행 전후로 DB에 Agent 단계 로그를 남기는 래퍼 (Langchain DeepAgent 단계)."""

    def wrapped(state: ALFPState) -> ALFPState:
        ctx = state.get("_logging_ctx") or {}
        run_id = ctx.get("run_id")
        stage_order = ctx.get("stage_order", 1)
        db_path = ctx.get("db_path")
        step_order = state.get("_agent_step_order", 0)

        if run_id is None or db_path is None:
            return node_func(state)

        started_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        t0 = datetime.utcnow()
        try:
            out = node_func(state)
            finished_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            elapsed_sec = (datetime.utcnow() - t0).total_seconds()
            summary = _agent_step_summary(node_name, out)
            step_logger(
                run_id=run_id,
                stage_order=stage_order,
                agent_name=node_name,
                step_order=step_order,
                started_at=started_at,
                finished_at=finished_at,
                elapsed_sec=elapsed_sec,
                ok=True,
                summary=summary if summary else None,
                error_text=None,
                db_path=db_path,
            )
            return {**out, "_agent_step_order": step_order + 1}
        except Exception as e:
            finished_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            elapsed_sec = (datetime.utcnow() - t0).total_seconds()
            step_logger(
                run_id=run_id,
                stage_order=stage_order,
                agent_name=node_name,
                step_order=step_order,
                started_at=started_at,
                finished_at=finished_at,
                elapsed_sec=elapsed_sec,
                ok=False,
                summary=None,
                error_text=str(e),
                db_path=db_path,
            )
            raise

    return wrapped


def save_memory_node(state: ALFPState) -> ALFPState:
    """의사결정 후 현재 런 요약을 영구 메모리에 저장."""
    log = state.get("messages", [])
    prosumer_id = state.get("prosumer_id", "unknown")
    plan = state.get("forecast_plan") or {}
    metrics = state.get("validation_metrics") or {}
    decisions = state.get("decisions") or {}

    # JSON 직렬 가능한 요약만 저장
    summary = {
        "last_run_at": datetime.utcnow().isoformat() + "Z",
        "last_plan": {
            "selected_model": plan.get("selected_model"),
            "forecast_horizon_steps": plan.get("forecast_horizon_steps"),
            "llm_reasoning": plan.get("llm_reasoning", "")[:500],
        },
        "last_validation_metrics": {
            "kpi": metrics.get("kpi"),
            "load": {k: v for k, v in (metrics.get("load") or {}).items() if k != "label"},
            "pv": {k: v for k, v in (metrics.get("pv") or {}).items() if k != "label"},
            "net_load": {k: v for k, v in (metrics.get("net_load") or {}).items() if k != "label"},
        },
        "last_decisions_summary": {
            "ess_summary": decisions.get("ess_summary"),
            "tariff_saving": decisions.get("tariff_saving"),
            "dr_summary": decisions.get("dr_summary"),
        },
    }
    save_memory(prosumer_id, summary)
    log.append("[Memory] 런 요약 저장 완료")
    return {**state, "messages": log}


def build_pipeline(
    step_logger: Callable[..., None] | None = None,
) -> StateGraph:
    """ALFP LangGraph 파이프라인을 빌드합니다. step_logger가 있으면 Agent별 단계를 로깅합니다."""

    graph = StateGraph(ALFPState)

    def _node(name: str, func: Callable[[ALFPState], ALFPState]):
        if step_logger is not None:
            return _wrap_node_for_logging(name, func, step_logger)
        return func

    # 노드 등록 (step_logger 있으면 래핑)
    graph.add_node("data_loader",          _node("data_loader", data_loader_node))
    graph.add_node("data_quality",         _node("data_quality", data_quality_agent))
    graph.add_node("feature_engineering",  _node("feature_engineering", feature_engineering_agent))
    graph.add_node("forecast_planner",     _node("forecast_planner", forecast_planner_agent))
    graph.add_node("load_forecast",        _node("load_forecast", load_forecast_agent))
    graph.add_node("pv_forecast",         _node("pv_forecast", pv_forecast_agent))
    graph.add_node("net_load_forecast",    _node("net_load_forecast", net_load_forecast_agent))
    graph.add_node("validation",           _node("validation", validation_agent))
    graph.add_node("replan",               _node("replan", replan_node))
    graph.add_node("decision",            _node("decision", decision_agent))
    graph.add_node("save_memory",          _node("save_memory", save_memory_node))

    # 엣지: 순차 + 조건 분기
    graph.set_entry_point("data_loader")
    graph.add_edge("data_loader",         "data_quality")
    graph.add_edge("data_quality",        "feature_engineering")
    graph.add_edge("feature_engineering", "forecast_planner")
    graph.add_edge("forecast_planner",    "load_forecast")
    graph.add_edge("load_forecast",       "pv_forecast")
    graph.add_edge("pv_forecast",         "net_load_forecast")
    graph.add_edge("net_load_forecast",   "validation")
    graph.add_conditional_edges("validation", _route_after_validation, {"replan": "replan", "decision": "decision"})
    graph.add_edge("replan",              "forecast_planner")
    graph.add_edge("decision",             "save_memory")
    graph.add_edge("save_memory",         END)

    return graph


def compile_pipeline(step_logger: Callable[..., None] | None = None):
    """파이프라인 컴파일. step_logger가 있으면 Agent 단계 로깅용 래퍼 적용."""
    graph = build_pipeline(step_logger=step_logger)
    return graph.compile()


def run_pipeline(
    prosumer_id: str,
    data_path: str = "data/train_2026_seoul.pkl",
    forecast_horizon: int = 96,
    run_id: int | None = None,
    db_path: Any = None,
) -> ALFPState:
    """
    파이프라인 실행 진입점.

    Args:
        prosumer_id: 예측할 프로슈머 ID (예: "bus_48_Commercial")
        data_path: 학습 데이터 pkl 경로
        forecast_horizon: 예측 스텝 수 (15분 단위, 기본 96 = 24시간)

    Returns:
        최종 ALFPState (예측 결과, 검증, 의사결정 포함)
    """
    step_logger = None
    # run_id와 db_path가 모두 유효할 때만 Agent별 단계를 DB(alfp_agent_step)에 기록
    if run_id is not None and db_path and str(db_path).strip():
        from pipeline_dashboard.db import add_agent_step
        _path = Path(db_path) if not isinstance(db_path, Path) else db_path

        def _log_step(
            run_id: int,
            stage_order: int,
            agent_name: str,
            step_order: int,
            started_at: str,
            finished_at: str | None = None,
            elapsed_sec: float | None = None,
            ok: bool = True,
            summary: dict[str, Any] | None = None,
            error_text: str | None = None,
            db_path: Path | None = None,
        ) -> None:
            add_agent_step(
                run_id=run_id,
                stage_order=stage_order,
                agent_name=agent_name,
                step_order=step_order,
                started_at=started_at,
                finished_at=finished_at,
                elapsed_sec=elapsed_sec,
                ok=ok,
                summary=summary,
                error_text=error_text,
                db_path=db_path or _path,
            )

        step_logger = _log_step
        # 진입 시 한 건 기록해 DB 경로·기록 동작 검증 (노드 래퍼와 무관)
        _now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        add_agent_step(
            run_id=run_id,
            stage_order=1,
            agent_name="pipeline_start",
            step_order=0,
            started_at=_now,
            finished_at=_now,
            elapsed_sec=0.0,
            ok=True,
            summary={"event": "run_pipeline invoked with step_logger"},
            error_text=None,
            db_path=_path,
        )

    app = compile_pipeline(step_logger=step_logger)

    initial_state: ALFPState = {
        "prosumer_id": prosumer_id,
        "data_path": data_path,
        "forecast_horizon": forecast_horizon,
        "messages": [],
        "errors": [],
        "plan_retry_count": 0,
        "max_plan_retries": 2,
    }
    if run_id is not None and db_path and str(db_path).strip():
        _path_for_ctx = Path(db_path) if not isinstance(db_path, Path) else Path(db_path)
        initial_state["_logging_ctx"] = {
            "run_id": run_id,
            "stage_order": 1,
            "db_path": _path_for_ctx,
        }
        initial_state["_agent_step_order"] = 1  # 첫 노드(data_loader)가 1, pipeline_start는 0

    result = app.invoke(initial_state)
    return result
