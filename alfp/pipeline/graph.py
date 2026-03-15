"""
LangGraph 기반 멀티 에이전트 파이프라인 정의

- 조건 분기: Validation 후 KPI 미달 시 재계획(forecast_planner) 또는 의사결정(decision)으로 라우팅
- 추론 루프: 재계획 시 load → pv → net_load → validation 반복 (max_plan_retries 제한)
- 영구 메모리: 파이프라인 시작 시 이전 런 로드, 종료 시 현재 런 요약 저장
"""

from datetime import datetime
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


def build_pipeline() -> StateGraph:
    """ALFP LangGraph 파이프라인을 빌드합니다."""

    graph = StateGraph(ALFPState)

    # 노드 등록
    graph.add_node("data_loader",          data_loader_node)
    graph.add_node("data_quality",         data_quality_agent)
    graph.add_node("feature_engineering",  feature_engineering_agent)
    graph.add_node("forecast_planner",     forecast_planner_agent)
    graph.add_node("load_forecast",        load_forecast_agent)
    graph.add_node("pv_forecast",          pv_forecast_agent)
    graph.add_node("net_load_forecast",    net_load_forecast_agent)
    graph.add_node("validation",           validation_agent)
    graph.add_node("replan",               replan_node)
    graph.add_node("decision",             decision_agent)
    graph.add_node("save_memory",          save_memory_node)

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


def compile_pipeline():
    """파이프라인 컴파일."""
    graph = build_pipeline()
    return graph.compile()


def run_pipeline(
    prosumer_id: str,
    data_path: str = "data/train_2026_seoul.pkl",
    forecast_horizon: int = 96,
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
    app = compile_pipeline()

    initial_state: ALFPState = {
        "prosumer_id": prosumer_id,
        "data_path": data_path,
        "forecast_horizon": forecast_horizon,
        "messages": [],
        "errors": [],
        "plan_retry_count": 0,
        "max_plan_retries": 2,
    }

    result = app.invoke(initial_state)
    return result
