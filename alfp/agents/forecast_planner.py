"""
ForecastPlannerAgent - 예측 작업 계획 수립, 모델 선택, horizon 결정
LLM이 데이터 특성·현재 날씨(OpenWeather)를 분석하여 최적 모델·전략을 추론합니다.
"""

import json
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from alfp.agents.state import ALFPState
from alfp.config import get_skills_config, get_system_prompt, get_user_prompt_template
from alfp.llm import get_llm
from alfp.tools.openweather import get_current_weather, get_current_weather_tool
from alfp.skills.energy_forecast import EnergyForecastSkill


def _build_stats(df: pd.DataFrame, prosumer_id: str, requested_horizon: int) -> dict:
    """LLM 프롬프트용 통계 데이터를 구성합니다."""
    prosumer_type = df["prosumer_type"].mode()[0] if "prosumer_type" in df.columns else "Unknown"
    ts = df["timestamp"]
    data_range_days = (ts.max() - ts.min()).days + 1

    load = df["load_kw"]
    pv = df["pv_kw"]
    load_cv = (load.std() / load.mean() * 100) if load.mean() > 0 else 0

    return {
        "prosumer_id": prosumer_id,
        "prosumer_type": prosumer_type,
        "data_range_days": data_range_days,
        "n_records": len(df),
        "load_mean": float(load.mean()),
        "load_std": float(load.std()),
        "load_min": float(load.min()),
        "load_max": float(load.max()),
        "load_cv": float(load_cv),
        "pv_mean": float(pv.mean()),
        "pv_max": float(pv.max()),
        "pv_ratio": float((pv > 0).mean() * 100),
        "price_buy_mean": float(df["price_buy"].mean()) if "price_buy" in df.columns else 0,
        "price_sell_mean": float(df["price_sell"].mean()) if "price_sell" in df.columns else 0,
        "requested_horizon": requested_horizon,
        "horizon_hours": requested_horizon / 4,
    }


def _fallback_plan(
    stats: dict,
    prev_plan: dict | None = None,
    persistent: dict | None = None,
) -> dict:
    """
    LLM 호출 실패 시 규칙 기반 fallback.
    재계획 시(prev_plan 또는 persistent의 last_plan 있음) 이전 모델과 반대 모델을 시도.
    """
    n = stats["n_records"]
    prosumer_type = stats["prosumer_type"]
    last_model = None
    if prev_plan and isinstance(prev_plan, dict):
        last_model = prev_plan.get("selected_model")
    if not last_model and persistent and isinstance(persistent.get("last_plan"), dict):
        last_model = (persistent["last_plan"] or {}).get("selected_model")

    if last_model in ("lgbm", "xgboost"):
        # 재계획: 이전과 다른 모델 선택
        model = "xgboost" if last_model == "lgbm" else "lgbm"
        reasoning = f"재계획: 이전 모델 {last_model} → {model} 전환 (규칙 fallback)"
    else:
        model = EnergyForecastSkill.select_model(n, prosumer_type)
        reasoning = "LLM 미사용 - 규칙 기반 fallback 적용"

    fp_cfg = get_skills_config().get("forecast_planner", {}).get("fallback", {})
    lgbm_cfg = fp_cfg.get("lgbm", {})
    xgb_cfg = fp_cfg.get("xgboost", {})
    num_leaves = lgbm_cfg.get("num_leaves_energy_hub", 127) if prosumer_type == "EnergyHub" else lgbm_cfg.get("num_leaves_default", 63)
    config = {
        "num_leaves": num_leaves,
        "n_estimators": lgbm_cfg.get("n_estimators", 500),
        "learning_rate": lgbm_cfg.get("learning_rate", 0.05),
    } if model == "lgbm" else {
        "max_depth": xgb_cfg.get("max_depth", 6),
        "n_estimators": xgb_cfg.get("n_estimators", 300),
        "learning_rate": xgb_cfg.get("learning_rate", 0.05),
    }
    return {
        "selected_model": model,
        "model_config": config,
        "forecast_horizon": stats["requested_horizon"],
        "reasoning": reasoning,
        "data_insights": f"데이터 {n}건, 타입 {prosumer_type}",
        "risk_factors": [],
    }


def _build_replan_context(state: ALFPState) -> str:
    """재계획 시 이전 런/검증 결과를 LLM·fallback에 전달할 문맥 문자열."""
    parts = []
    persistent = state.get("persistent_memory") or {}
    prev_metrics = state.get("validation_metrics")
    retry = state.get("plan_retry_count", 0)

    if retry > 0:
        parts.append(f"[재계획] 이번 런 재시도 횟수: {retry}회")
    if prev_metrics:
        kpi = (prev_metrics.get("kpi") or {})
        parts.append(
            f"[이전 검증 결과] MAPE 달성: {kpi.get('MAPE_pass', 'N/A')} (achieved: {kpi.get('MAPE_achieved')}), "
            f"피크 정확도 달성: {kpi.get('peak_acc_pass', 'N/A')} (achieved: {kpi.get('peak_acc_achieved')})"
        )
        plan = state.get("forecast_plan") or {}
        if plan.get("selected_model"):
            parts.append(f"이전 선택 모델: {plan.get('selected_model')}. KPI 미달 시 다른 모델 또는 설정을 권장합니다.")
    if persistent.get("last_plan"):
        lp = persistent["last_plan"]
        parts.append(f"[이전 런 요약] 모델: {lp.get('selected_model')}, horizon: {lp.get('forecast_horizon_steps')} 스텝")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


def forecast_planner_agent(state: ALFPState) -> ALFPState:
    """
    ForecastPlannerAgent 노드 함수.
    GPT-4o가 데이터 특성·이전 검증 결과(재계획 시)·영구 메모리를 참고해 모델·horizon·전략을 결정합니다.
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    is_replan = (state.get("plan_retry_count", 0) or 0) > 0
    if is_replan:
        log.append("[ForecastPlannerAgent] 재계획 수립 (이전 검증 KPI 미달)")
    else:
        log.append("[ForecastPlannerAgent] LLM 기반 예측 계획 수립 시작")

    df = state["feature_df"]
    prosumer_id = state.get("prosumer_id", "unknown")
    requested_horizon = state.get("forecast_horizon", 96)

    try:
        stats = _build_stats(df, prosumer_id, requested_horizon)
        # OpenWeather 현재 날씨를 프롬프트에 포함 (LLM이 예측 전략 수립 시 참고)
        try:
            weather_text = get_current_weather_tool(city="Seoul")
            weather_block = f"\n[현재 날씨 (OpenWeather)]\n{weather_text}"
        except Exception:
            weather_block = ""
        stats["weather_block"] = weather_block

        # 재계획/영구 메모리 문맥 추가
        replan_block = _build_replan_context(state)
        stats["weather_block"] = (replan_block + stats["weather_block"]) if replan_block else stats["weather_block"]

        llm = get_llm(temperature=0.0)
        parser = JsonOutputParser()

        system_prompt = get_system_prompt("forecast_planner")
        user_template = get_user_prompt_template("forecast_planner")
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_template.format(**stats)),
        ]

        log.append("  GPT-4o 호출 중... (날씨 정보 포함)")
        response = llm.invoke(messages)
        plan = parser.invoke(response.content)
        log.append("  GPT-4o 응답 수신 완료")

    except Exception as e:
        errors.append(f"[ForecastPlannerAgent] LLM 오류 → fallback 적용: {e}")
        plan = _fallback_plan(
            _build_stats(df, prosumer_id, requested_horizon),
            prev_plan=state.get("forecast_plan"),
            persistent=state.get("persistent_memory"),
        )

    # 결과 정리
    selected_model = plan.get("selected_model", "lgbm")
    model_config = plan.get("model_config", {})
    horizon = int(plan.get("forecast_horizon", requested_horizon))

    forecast_plan = {
        "prosumer_id": prosumer_id,
        "prosumer_type": stats["prosumer_type"],
        "data_range_days": stats["data_range_days"],
        "n_train_records": stats["n_records"],
        "selected_model": selected_model,
        "forecast_horizon_steps": horizon,
        "forecast_horizon_hours": horizon / 4,
        "model_config": model_config,
        "llm_reasoning": plan.get("reasoning", ""),
        "llm_data_insights": plan.get("data_insights", ""),
        "llm_risk_factors": plan.get("risk_factors", []),
    }

    log.append(f"  선택 모델: {selected_model.upper()}")
    log.append(f"  예측 Horizon: {horizon} 스텝 ({horizon/4:.1f}시간)")
    log.append(f"  LLM 판단 근거: {plan.get('reasoning', '')}")
    log.append("[ForecastPlannerAgent] 완료")

    return {
        **state,
        "selected_model": selected_model,
        "model_config": model_config,
        "forecast_horizon": horizon,
        "forecast_plan": forecast_plan,
        "messages": log,
        "errors": errors,
    }
