"""
DecisionAgent - 예측 결과 기반 운영 의사결정 + LLM 상세 추천
LLM이 ESS 전략·에너지 거래·DR 이벤트에 대한 운영 가이드를 생성합니다.
"""

import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from alfp.agents.state import ALFPState
from alfp.config import get_skills_config, get_system_prompt, get_user_prompt_template
from alfp.llm import get_llm
from alfp.skills.ess_optimization import ESSOptimizationSkill
from alfp.skills.tariff_analysis import TariffAnalysisSkill


def _trading_recommendation(load_df: pd.DataFrame, pv_df: pd.DataFrame) -> list:
    cfg = get_skills_config().get("decision_agent", {}).get("trading", {})
    surplus_min = cfg.get("surplus_kw_min", 0.5)
    max_recs = cfg.get("max_recommendations", 10)
    merged = pd.merge(
        load_df[["timestamp", "predicted_load_kw"]],
        pv_df[["timestamp", "predicted_pv_kw"]], on="timestamp")
    merged["surplus_kw"] = (merged["predicted_pv_kw"] - merged["predicted_load_kw"]).clip(lower=0)
    recs = []
    for _, row in merged[merged["surplus_kw"] > surplus_min].iterrows():
        recs.append({"timestamp": str(row["timestamp"]), "surplus_kw": round(row["surplus_kw"], 2),
                     "action": "sell_p2p"})
    return recs[:max_recs]


def _demand_response(net_load: pd.Series, timestamps: pd.Series, peak_threshold: float) -> list:
    cfg = get_skills_config().get("decision_agent", {}).get("demand_response", {})
    reduction_factor = cfg.get("reduction_factor", 0.3)
    return [
        {"timestamp": str(ts), "net_load_kw": round(float(nl), 2),
         "recommended_reduction_kw": round((nl - peak_threshold) * reduction_factor, 2), "action": "demand_response"}
        for ts, nl in zip(timestamps, net_load) if nl > peak_threshold
    ]


def decision_agent(state: ALFPState) -> ALFPState:
    """
    DecisionAgent 노드 함수.
    규칙 기반으로 스케줄/이벤트를 생성하고, LLM이 운영 전략을 작성합니다.
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[DecisionAgent] LLM 기반 운영 의사결정 생성 시작")

    try:
        nl_df = state["net_load_forecast"]
        load_df = state["load_forecast"]
        pv_df = state["pv_forecast"]
        plan = state.get("forecast_plan", {})
        metrics = state.get("validation_metrics", {})

        net_load_series = nl_df["predicted_net_load_kw"]
        timestamps = nl_df["timestamp"]
        da_cfg = get_skills_config().get("decision_agent", {})
        peak_quantile = da_cfg.get("peak_threshold_quantile", 0.85)
        peak_threshold = float(net_load_series.quantile(peak_quantile))

        # ── ESSOptimizationSkill: Peak Shaving 스케줄 ─────────────────
        ess_cfg = da_cfg.get("ess", {})
        bess_kwh_cap = ess_cfg.get("bess_kwh_cap", 50.0)
        bess_kw_cap = ess_cfg.get("bess_kw_cap", 25.0)
        dt_h = ess_cfg.get("dt_h", 0.25)
        ess_skill = ESSOptimizationSkill(bess_kwh_cap=bess_kwh_cap, bess_kw_cap=bess_kw_cap, dt_h=dt_h)
        ess_schedule_df = ess_skill.peak_shaving_schedule(net_load_series, timestamps, peak_limit_kw=peak_threshold)
        ess_summary_skill = ess_skill.summarize(ess_schedule_df)
        ess_schedule = [{"timestamp": str(row["timestamp"]), "action": row["action"], "power_kw": row["power_kw"],
                        "soc_kwh": row["soc_kwh"], "net_load_kw": row["net_load_kw"]}
                       for _, row in ess_schedule_df.iterrows()]

        n_charge = ess_summary_skill["charge_steps"]
        n_discharge = ess_summary_skill["discharge_steps"]
        n_idle = ess_summary_skill["idle_steps"]

        trading_recs = _trading_recommendation(load_df, pv_df)
        dr_events = _demand_response(net_load_series, timestamps, peak_threshold)

        # ── TariffAnalysisSkill: TOU 분석 및 ESS 절감 시뮬레이션 ──────
        cost_saving = {"base_cost_krw": 0, "adjusted_cost_krw": 0, "saving_krw": 0, "saving_pct": 0.0}
        tariff_skill = TariffAnalysisSkill()
        feature_df = state.get("feature_df")
        if feature_df is not None and "price_buy" in feature_df.columns:
            default_price = get_skills_config().get("decision_agent", {}).get("tariff_fallback", {}).get("default_price_buy_krw", 100.0)
            tariff_df = load_df[["timestamp", "load_kw"]].merge(
                feature_df[["timestamp", "price_buy"]].drop_duplicates("timestamp"),
                on="timestamp", how="left"
            ).fillna({"price_buy": default_price})
            cost_saving = tariff_skill.cost_saving_simulation(tariff_df, ess_schedule_df)

        log.append(f"  ESS: 충전 {n_charge}스텝 / 방전 {n_discharge}스텝")
        log.append(f"  에너지 거래 잉여: {len(trading_recs)}건")
        log.append(f"  DR 이벤트: {len(dr_events)}건")

        # ── LLM 전략 수립 ─────────────────────────────────────────
        load_mape = metrics.get("load", {}).get("MAPE", 0)
        nl_mape = metrics.get("net_load", {}).get("MAPE", 0)

        prompt_data = {
            "prosumer_type": plan.get("prosumer_type", "Unknown"),
            "prosumer_id": plan.get("prosumer_id", "Unknown"),
            "nl_mean": float(net_load_series.mean()),
            "nl_max": float(net_load_series.max()),
            "nl_min": float(net_load_series.min()),
            "peak_threshold": peak_threshold,
            "total_steps": len(ess_schedule),
            "charge_steps": n_charge,
            "discharge_steps": n_discharge,
            "idle_steps": n_idle,
            "bess_kwh_cap": bess_kwh_cap,
            "surplus_events": len(trading_recs),
            "total_surplus": sum(r["surplus_kw"] for r in trading_recs),
            "dr_count": len(dr_events),
            "load_mape": load_mape,
            "nl_mape": nl_mape,
            "base_cost_krw": cost_saving["base_cost_krw"],
            "adjusted_cost_krw": cost_saving["adjusted_cost_krw"],
            "saving_krw": cost_saving["saving_krw"],
            "saving_pct": cost_saving["saving_pct"],
        }

        llm_temperature = get_skills_config().get("decision_agent", {}).get("llm_temperature", 0.2)
        llm = get_llm(temperature=llm_temperature)
        log.append("  GPT-4o 운영 전략 수립 중...")
        system_prompt = get_system_prompt("decision")
        user_template = get_user_prompt_template("decision")
        response = llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_template.format(**prompt_data)),
        ])
        llm_strategy = JsonOutputParser().invoke(response.content)
        log.append(f"  LLM 경보 수준: {llm_strategy.get('alert_level', 'N/A')}")
        log.append(f"  LLM 종합 추천: {llm_strategy.get('overall_recommendation', '')[:80]}...")

    except Exception as e:
        errors.append(f"[DecisionAgent] LLM 오류: {e}")
        llm_strategy = {}

    decisions = {
        "ess_schedule": ess_schedule,
        "ess_summary": {"charge_steps": n_charge, "discharge_steps": n_discharge, "idle_steps": n_idle},
        "trading_recommendations": trading_recs,
        "trading_summary": {
            "total_surplus_events": len(trading_recs),
            "total_surplus_kw": round(sum(r["surplus_kw"] for r in trading_recs), 2),
        },
        "demand_response_events": dr_events,
        "dr_summary": {"peak_threshold_kw": round(peak_threshold, 2), "dr_event_count": len(dr_events)},
        "tariff_saving": cost_saving,
        "llm_strategy": llm_strategy,
    }

    log.append("[DecisionAgent] 완료")
    return {**state, "decisions": decisions, "messages": log, "errors": errors}
