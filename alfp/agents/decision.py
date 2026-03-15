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


def _trading_recommendation(
    nl_df: pd.DataFrame,
    load_df: pd.DataFrame | None = None,
    pv_df: pd.DataFrame | None = None,
    log: list | None = None,
) -> list:
    """
    예측된 net_load(부하 - PV)에서 잉여(surplus = -net_load > 0)가 발생하는 스텝을
    P2P 판매 추천으로 생성합니다.

    net_load_forecast를 우선 사용합니다. 이 값은 ALFP ML 모델이 직접 예측한
    값으로 load_forecast - pv_forecast보다 정확하며, PV 과소평가 문제가 없습니다.
    primary 경로에서 권고가 0건이면 load_df/pv_df 기반 surplus로 fallback합니다.
    """
    cfg = get_skills_config().get("decision_agent", {}).get("trading", {})
    surplus_min = cfg.get("surplus_kw_min", 0.5)
    max_recs = cfg.get("max_recommendations", 96)

    def _append_stats(msg_list: list | None, df_work: pd.DataFrame, label: str) -> None:
        if msg_list is None or df_work.empty:
            return
        s = df_work["surplus_kw"]
        msg_list.append(
            f"  [거래권고] {label}: surplus_kw min={s.min():.3f} max={s.max():.3f} "
            f"count_gt_min={int((s > surplus_min).sum())} (임계값={surplus_min})"
        )

    df_work = None
    source = "none"
    if nl_df is not None and "predicted_net_load_kw" in nl_df.columns:
        surplus = (-nl_df["predicted_net_load_kw"]).clip(lower=0)
        df_work = nl_df[["timestamp"]].copy()
        df_work["surplus_kw"] = surplus
        source = "net_load"
    if (df_work is None or df_work.empty) and load_df is not None and pv_df is not None:
        merged = pd.merge(
            load_df[["timestamp", "predicted_load_kw"]],
            pv_df[["timestamp", "predicted_pv_kw"]], on="timestamp")
        merged["surplus_kw"] = (merged["predicted_pv_kw"] - merged["predicted_load_kw"]).clip(lower=0)
        df_work = merged[["timestamp", "surplus_kw"]]
        source = "load_pv_fallback" if source == "none" else "load_pv_fallback(primary_0)"

    if df_work is None or df_work.empty:
        if log:
            log.append("  [거래권고] net_load/load·pv 데이터 없음 → 0건")
        return []

    _append_stats(log, df_work, source)
    recs = []
    for _, row in df_work[df_work["surplus_kw"] > surplus_min].iterrows():
        recs.append({"timestamp": str(row["timestamp"]),
                     "surplus_kw": round(float(row["surplus_kw"]), 2),
                     "action": "sell_p2p"})
    recs = recs[:max_recs]

    # primary(net_load)에서 0건이면 load/pv 잉여로 한 번 더 시도 (동일 타임스탬프 기준)
    if len(recs) == 0 and source == "net_load" and load_df is not None and pv_df is not None:
        merged = pd.merge(
            load_df[["timestamp", "predicted_load_kw"]],
            pv_df[["timestamp", "predicted_pv_kw"]], on="timestamp")
        merged["surplus_kw"] = (merged["predicted_pv_kw"] - merged["predicted_load_kw"]).clip(lower=0)
        _append_stats(log, merged[["timestamp", "surplus_kw"]], "load_pv_fallback")
        for _, row in merged[merged["surplus_kw"] > surplus_min].iterrows():
            recs.append({"timestamp": str(row["timestamp"]),
                         "surplus_kw": round(float(row["surplus_kw"]), 2),
                         "action": "sell_p2p"})
        recs = recs[:max_recs]
        if log and recs:
            log.append(f"  [거래권고] net_load 경로 0건 → load/pv fallback으로 {len(recs)}건 생성")

    # 잉여가 있으나 모두 surplus_min 이하인 경우: 임계값 0으로 잉여 있는 스텝만 상위 N건 권고 (거래권고 0건 방지)
    if len(recs) == 0 and df_work is not None and not df_work.empty:
        positive = df_work[df_work["surplus_kw"] > 0]
        if not positive.empty:
            for _, row in positive.iterrows():
                recs.append({"timestamp": str(row["timestamp"]),
                             "surplus_kw": round(float(row["surplus_kw"]), 2),
                             "action": "sell_p2p"})
            recs = sorted(recs, key=lambda x: x["surplus_kw"], reverse=True)[:max_recs]
            if log and recs:
                log.append(
                    f"  [거래권고] surplus 모두 임계값({surplus_min} kW) 이하 → 잉여>0 스텝만 사용하여 {len(recs)}건 생성 (max surplus={positive['surplus_kw'].max():.3f} kW)"
                )

    # 예측 잉여가 전혀 없을 때: 실제(actual) load/pv 기반 잉여로 권고 (검증 구간 실제 데이터 fallback)
    if len(recs) == 0 and load_df is not None and pv_df is not None and "load_kw" in load_df.columns and "pv_kw" in pv_df.columns:
        actual_merged = pd.merge(
            load_df[["timestamp", "load_kw"]],
            pv_df[["timestamp", "pv_kw"]], on="timestamp"
        )
        actual_merged["surplus_kw"] = (actual_merged["pv_kw"] - actual_merged["load_kw"]).clip(lower=0)
        above = actual_merged[actual_merged["surplus_kw"] > surplus_min]
        if not above.empty:
            for _, row in above.iterrows():
                recs.append({"timestamp": str(row["timestamp"]),
                             "surplus_kw": round(float(row["surplus_kw"]), 2),
                             "action": "sell_p2p"})
            recs = recs[:max_recs]
            if log and recs:
                log.append(f"  [거래권고] 예측 잉여 0건 → 실제(actual) 잉여로 {len(recs)}건 생성")

    return recs


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

    ess_schedule = []
    trading_recs = []
    dr_events = []
    cost_saving = {"base_cost_krw": 0, "adjusted_cost_krw": 0, "saving_krw": 0, "saving_pct": 0.0}

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

        trading_recs = _trading_recommendation(nl_df, load_df, pv_df, log=log)
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
