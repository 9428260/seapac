"""
DecisionAgent - 예측 결과 기반 운영 의사결정 + LLM 상세 추천
deepagents + MCP skills 또는 규칙 기반 로직으로 ESS/거래/DR 운영 전략을 생성합니다.
"""

import json
import pandas as pd

from alfp.agents.state import ALFPState
from alfp.config import get_skills_config, get_system_prompt
from alfp.deepagents import invoke_deepagents_decision_agent
from alfp.llm import is_llm_enabled
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


def _build_decision_context(
    state: ALFPState,
    nl_df: pd.DataFrame,
    load_df: pd.DataFrame,
    pv_df: pd.DataFrame,
    feature_df: pd.DataFrame | None,
    peak_threshold: float,
) -> dict:
    context = {
        "prosumer_id": (state.get("forecast_plan") or {}).get("prosumer_id", state.get("prosumer_id", "Unknown")),
        "prosumer_type": (state.get("forecast_plan") or {}).get("prosumer_type", "Unknown"),
        "operating_mode": state.get("operating_mode", "day_ahead"),
        "execution_mode": state.get("execution_mode", "full"),
        "forecast_plan": state.get("forecast_plan") or {},
        "validation_metrics": state.get("validation_metrics") or {},
        "peak_threshold": peak_threshold,
        "net_load_forecast": nl_df.to_dict(orient="records"),
        "load_forecast": load_df.to_dict(orient="records"),
        "pv_forecast": pv_df.to_dict(orient="records"),
        "feature_df": [],
    }
    if feature_df is not None and not feature_df.empty:
        cols = [col for col in ["timestamp", "price_buy", "price_sell"] if col in feature_df.columns]
        if cols:
            context["feature_df"] = feature_df[cols].drop_duplicates("timestamp").to_dict(orient="records")
    return context


def _deepagent_prompt(context: dict, peak_threshold: float) -> str:
    prosumer_type = context.get("prosumer_type", "Unknown")
    mode = context.get("operating_mode", "day_ahead")
    metrics = context.get("validation_metrics") or {}
    kpi = metrics.get("kpi") or {}
    return f"""
다음 전력 운영 맥락에 대해 MCP-backed decision skills를 사용해 의사결정을 수행하세요.

[프로슈머]
- ID: {context.get('prosumer_id', 'Unknown')}
- 타입: {prosumer_type}

[운영 모드]
- operating_mode: {mode}
- execution_mode: {context.get('execution_mode', 'full')}

[검증 KPI]
- MAPE_pass: {kpi.get('MAPE_pass')}
- peak_acc_pass: {kpi.get('peak_acc_pass')}
- peak_threshold_kw: {peak_threshold:.2f}

필수 작업:
1. 여러 ESS/거래/DR 조합안을 동시에 생성
2. 수익, 리스크, 정책 위반 가능성, 배터리 열화 비용 비교
3. short horizon, day ahead, 이상상황 대응 모드 구분
4. 프로슈머 타입별 전략 차별화
5. 가장 설명 가능한 최종 전략 채택

반드시 MCP skills를 사용해서 후보를 생성/비교하고, 최종 응답에는 선택 후보와 운영 가이드를 포함하세요.

[JSON Context]
{json.dumps(context, ensure_ascii=False)}
""".strip()


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

        if is_llm_enabled("alfp_decision"):
            context = _build_decision_context(state, nl_df, load_df, pv_df, feature_df, peak_threshold)
            system_prompt = get_system_prompt("decision")
            user_prompt = _deepagent_prompt(context, peak_threshold)
            log.append("  deepagents + MCP skills 기반 운영 전략 수립 중...")
            deep_plan = invoke_deepagents_decision_agent(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage="alfp_decision",
            )
            selected = deep_plan.get("selected_candidate") or {}
            if selected:
                ess_schedule = selected.get("ess_schedule") or []
                ess_summary = selected.get("ess_summary") or {}
                trading_recs = selected.get("trading_recommendations") or []
                dr_events = selected.get("demand_response_events") or []
                cost_saving = selected.get("tariff_saving") or cost_saving
                decisions = {
                    "ess_schedule": ess_schedule,
                    "ess_summary": ess_summary,
                    "trading_recommendations": trading_recs,
                    "trading_summary": selected.get("trading_summary") or {
                        "total_surplus_events": len(trading_recs),
                        "total_surplus_kw": round(sum(r["surplus_kw"] for r in trading_recs), 2) if trading_recs else 0.0,
                    },
                    "demand_response_events": dr_events,
                    "dr_summary": selected.get("dr_summary") or {
                        "peak_threshold_kw": round(peak_threshold, 2),
                        "dr_event_count": len(dr_events),
                    },
                    "tariff_saving": cost_saving,
                    "llm_strategy": {
                        "ess_strategy": deep_plan.get("ess_strategy", ""),
                        "trading_strategy": deep_plan.get("trading_strategy", ""),
                        "dr_strategy": deep_plan.get("dr_strategy", ""),
                        "overall_recommendation": deep_plan.get("overall_recommendation", ""),
                        "priority_actions": deep_plan.get("priority_actions", []),
                        "expected_savings": deep_plan.get("expected_savings", ""),
                        "alert_level": deep_plan.get("alert_level", "정상"),
                    },
                    "candidate_comparisons": deep_plan.get("candidate_comparisons", []),
                    "strategy_candidates": deep_plan.get("candidate_portfolios", []),
                    "selected_candidate_id": deep_plan.get("selected_candidate_id"),
                    "scenario_mode": deep_plan.get("scenario_mode"),
                    "mode_guidance": deep_plan.get("mode_guidance", []),
                    "selected_candidate": selected,
                }
                log.append(f"  deepagents 선택 후보: {deep_plan.get('selected_candidate_id')}")
                log.append(f"  경보 수준: {deep_plan.get('alert_level', '정상')}")
                log.append(f"  종합 추천: {deep_plan.get('overall_recommendation', '')[:80]}...")
                log.append("[DecisionAgent] 완료")
                return {**state, "decisions": decisions, "messages": log, "errors": errors}
            llm_strategy = {}
        else:
            log.append("  LLM 비활성화 상태 - 규칙 기반 운영 의사결정만 생성")
            llm_strategy = {}

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
        "strategy_candidates": [],
    }

    log.append("[DecisionAgent] 완료")
    return {**state, "decisions": decisions, "messages": log, "errors": errors}
