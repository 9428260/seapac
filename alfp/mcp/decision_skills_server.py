"""
MCP server exposing decision skills for ALFP.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alfp.config import get_skills_config
from alfp.skills.ess_optimization import ESSOptimizationSkill
from alfp.skills.tariff_analysis import TariffAnalysisSkill

mcp = FastMCP("alfp-decision-skills")


def _df_from_records(records: list[dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df


def _scenario_mode(context: dict[str, Any]) -> str:
    mode = context.get("operating_mode", "day_ahead")
    metrics = context.get("validation_metrics") or {}
    kpi = metrics.get("kpi") or {}
    peak_ratio = float(context.get("peak_ratio", 1.0) or 1.0)
    if context.get("anomaly_mode"):
        return "anomaly_response"
    if kpi.get("MAPE_pass") is False or kpi.get("peak_acc_pass") is False or peak_ratio >= 1.25:
        return "anomaly_response"
    if mode == "short_horizon":
        return "short_horizon"
    return "day_ahead"


def _trading_recommendation_variant(
    nl_df: pd.DataFrame,
    variant: str,
    prosumer_type: str,
) -> list[dict[str, Any]]:
    cfg = get_skills_config().get("decision_agent", {}).get("trading", {})
    base_threshold = float(cfg.get("surplus_kw_min", 0.1))
    max_recommendations = int(cfg.get("max_recommendations", 10))
    if prosumer_type in {"EnergyHub", "Commercial"}:
        max_recommendations = max(max_recommendations, 12)

    multiplier = {"conservative": 1.5, "balanced": 1.0, "aggressive": 0.5}.get(variant, 1.0)
    threshold = max(0.01, base_threshold * multiplier)
    rec_limit = {
        "conservative": max(4, max_recommendations // 2),
        "balanced": max_recommendations,
        "aggressive": max_recommendations * 2,
    }.get(variant, max_recommendations)

    surplus = (-nl_df["predicted_net_load_kw"]).clip(lower=0)
    df = nl_df[["timestamp"]].copy()
    df["surplus_kw"] = surplus
    df = df[df["surplus_kw"] > threshold].sort_values("surplus_kw", ascending=False).head(rec_limit)

    price_sell = float(nl_df["price_sell"].mean()) if "price_sell" in nl_df.columns else 80.0
    return [
        {
            "timestamp": str(row["timestamp"]),
            "surplus_kw": round(float(row["surplus_kw"]), 2),
            "expected_revenue_krw": round(float(row["surplus_kw"]) * price_sell * 0.25, 1),
            "action": "sell_p2p",
        }
        for _, row in df.iterrows()
    ]


def _demand_response_variant(
    nl_df: pd.DataFrame,
    variant: str,
    scenario_mode: str,
) -> list[dict[str, Any]]:
    base_quantile = {"low": 0.92, "balanced": 0.85, "high": 0.78}.get(variant, 0.85)
    if scenario_mode == "anomaly_response":
        base_quantile = max(0.65, base_quantile - 0.08)
    reduction_factor = {"low": 0.2, "balanced": 0.3, "high": 0.45}.get(variant, 0.3)

    net_load = nl_df["predicted_net_load_kw"]
    threshold = float(net_load.quantile(base_quantile))
    rows = []
    for ts, value in zip(nl_df["timestamp"], net_load):
        if float(value) > threshold:
            rows.append(
                {
                    "timestamp": str(ts),
                    "net_load_kw": round(float(value), 2),
                    "recommended_reduction_kw": round((float(value) - threshold) * reduction_factor, 2),
                    "action": "demand_response",
                }
            )
    return rows


def _build_ess_schedule(
    nl_df: pd.DataFrame,
    feature_df: pd.DataFrame,
    ess_mode: str,
    scenario_mode: str,
    prosumer_type: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    ess_cfg = get_skills_config().get("decision_agent", {}).get("ess", {})
    skill = ESSOptimizationSkill(
        bess_kwh_cap=ess_cfg.get("bess_kwh_cap", 50.0),
        bess_kw_cap=ess_cfg.get("bess_kw_cap", 25.0),
        dt_h=ess_cfg.get("dt_h", 0.25),
    )
    net_load = nl_df["predicted_net_load_kw"]
    timestamps = nl_df["timestamp"]
    if feature_df is not None and "price_buy" in feature_df.columns:
        price_series = (
            feature_df[["timestamp", "price_buy"]]
            .drop_duplicates("timestamp")
            .set_index("timestamp")
            .reindex(timestamps)
            .ffill()
            .bfill()
            ["price_buy"]
            .reset_index(drop=True)
        )
    else:
        price_series = pd.Series([100.0] * len(nl_df))

    if ess_mode == "tou_optimized":
        schedule_df = skill.tou_schedule(net_load, timestamps, price_series)
    else:
        quantile = {"peak_shaving": 0.85, "conservative": 0.92, "resilience": 0.72}.get(ess_mode, 0.85)
        if scenario_mode == "anomaly_response":
            quantile = max(0.65, quantile - 0.08)
        peak_limit = float(net_load.quantile(quantile))
        schedule_df = skill.peak_shaving_schedule(net_load, timestamps, peak_limit_kw=peak_limit)

    summary = skill.summarize(schedule_df)
    if prosumer_type == "Residential":
        summary["prosumer_bias"] = "comfort_first"
    elif prosumer_type in {"Commercial", "EnergyHub"}:
        summary["prosumer_bias"] = "value_capture"
    else:
        summary["prosumer_bias"] = "balanced"
    return schedule_df, summary


def _portfolio_risk(candidate: dict[str, Any], context: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    risk = 0.15
    ess_summary = candidate["ess_summary"]
    scenario_mode = candidate["scenario_mode"]
    prosumer_type = context.get("prosumer_type", "Unknown")

    if candidate["trading_variant"] == "aggressive":
        risk += 0.14
        reasons.append("공격적 거래 전략은 정책 위반 및 체결 불확실성을 높입니다.")
    if candidate["dr_variant"] == "high":
        risk += 0.10
        reasons.append("강한 DR 전략은 사용자 수용성과 실행 편차 리스크가 큽니다.")
    if ess_summary.get("discharge_steps", 0) + ess_summary.get("charge_steps", 0) > 40:
        risk += 0.12
        reasons.append("배터리 사이클 수가 많아 열화 비용과 운영 리스크가 커집니다.")
    if scenario_mode == "anomaly_response" and candidate["ess_mode"] == "conservative":
        risk += 0.08
        reasons.append("이상상황 대응에는 보수적 ESS 전략이 피크 대응에 부족할 수 있습니다.")
    if prosumer_type == "Residential" and candidate["dr_variant"] == "high":
        risk += 0.07
        reasons.append("주거형 프로슈머에는 강한 DR 전략의 체감 불편이 큽니다.")
    if not reasons:
        reasons.append("현재 조합은 구조적으로 과도한 위험 신호가 제한적입니다.")
    return round(min(risk, 0.95), 2), reasons


def _policy_violation_probability(candidate: dict[str, Any], context: dict[str, Any]) -> float:
    probability = 0.05
    if candidate["trading_variant"] == "aggressive":
        probability += 0.12
    if candidate["dr_variant"] == "high":
        probability += 0.08
    if candidate["scenario_mode"] == "anomaly_response":
        probability += 0.06
    if context.get("prosumer_type") == "Residential":
        probability += 0.03 if candidate["dr_variant"] != "low" else 0.0
    return round(min(probability, 0.9), 2)


def _battery_degradation_cost(schedule_df: pd.DataFrame, dt_h: float) -> float:
    throughput = float(schedule_df[schedule_df["action"].isin(["charge", "discharge"])]["power_kw"].sum()) * dt_h
    return round(throughput * 8.0, 1)


def _candidate_to_records(schedule_df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": str(row["timestamp"]),
            "action": row["action"],
            "power_kw": float(row["power_kw"]),
            "soc_kwh": float(row.get("soc_kwh", 0.0)),
            "net_load_kw": float(row.get("net_load_kw", 0.0)),
        }
        for _, row in schedule_df.iterrows()
    ]


@mcp.tool(description="Generate multiple ESS/trading/DR portfolio candidates for the given operating context.", structured_output=True)
def generate_strategy_candidates(context_json: str) -> dict[str, Any]:
    context = json.loads(context_json)
    nl_df = _df_from_records(context["net_load_forecast"])
    feature_df = _df_from_records(context.get("feature_df") or [])
    load_df = _df_from_records(context.get("load_forecast") or [])
    prosumer_type = context.get("prosumer_type", "Unknown")
    operating_mode = context.get("operating_mode", "day_ahead")
    scenario_mode = _scenario_mode(context)
    context["scenario_mode"] = scenario_mode

    combos = [
        ("peak_shaving", "balanced", "balanced"),
        ("tou_optimized", "aggressive" if prosumer_type in {"Commercial", "EnergyHub"} else "balanced", "low"),
        ("conservative", "conservative", "low"),
        ("resilience", "balanced", "high" if scenario_mode == "anomaly_response" else "balanced"),
    ]
    if operating_mode == "short_horizon":
        combos[0] = ("resilience", "balanced", "high")
        combos[2] = ("conservative", "conservative", "balanced")

    tariff_skill = TariffAnalysisSkill()
    candidates: list[dict[str, Any]] = []
    peak_ratio = float(nl_df["predicted_net_load_kw"].max() / max(nl_df["predicted_net_load_kw"].mean(), 1e-6))
    context["peak_ratio"] = peak_ratio
    dt_h = float(get_skills_config().get("decision_agent", {}).get("ess", {}).get("dt_h", 0.25))

    tariff_df = None
    if not load_df.empty:
        if feature_df is not None and not feature_df.empty and "price_buy" in feature_df.columns:
            default_price = get_skills_config().get("decision_agent", {}).get("tariff_fallback", {}).get("default_price_buy_krw", 100.0)
            tariff_df = load_df[["timestamp", "load_kw"]].merge(
                feature_df[["timestamp", "price_buy"]].drop_duplicates("timestamp"),
                on="timestamp",
                how="left",
            ).fillna({"price_buy": default_price})

    for idx, (ess_mode, trading_variant, dr_variant) in enumerate(combos, start=1):
        schedule_df, ess_summary = _build_ess_schedule(nl_df, feature_df, ess_mode, scenario_mode, prosumer_type)
        trading = _trading_recommendation_variant(nl_df, trading_variant, prosumer_type)
        dr_events = _demand_response_variant(nl_df, dr_variant, scenario_mode)
        tariff_saving = (
            tariff_skill.cost_saving_simulation(tariff_df, schedule_df)
            if tariff_df is not None and not tariff_df.empty
            else {"base_cost_krw": 0, "adjusted_cost_krw": 0, "saving_krw": 0, "saving_pct": 0.0}
        )
        trading_revenue = round(sum(item["expected_revenue_krw"] for item in trading), 1)
        degradation_cost = _battery_degradation_cost(schedule_df, dt_h)
        risk_score, risk_reasons = _portfolio_risk(
            {
                "ess_mode": ess_mode,
                "trading_variant": trading_variant,
                "dr_variant": dr_variant,
                "ess_summary": ess_summary,
                "scenario_mode": scenario_mode,
            },
            context,
        )
        policy_probability = _policy_violation_probability(
            {
                "trading_variant": trading_variant,
                "dr_variant": dr_variant,
                "scenario_mode": scenario_mode,
            },
            context,
        )
        expected_profit = round(float(tariff_saving["saving_krw"]) + trading_revenue - degradation_cost, 1)
        candidate_id = f"portfolio_{idx}_{ess_mode}_{trading_variant}_{dr_variant}"
        candidates.append(
            {
                "candidate_id": candidate_id,
                "operating_mode": operating_mode,
                "scenario_mode": scenario_mode,
                "prosumer_type": prosumer_type,
                "ess_mode": ess_mode,
                "trading_variant": trading_variant,
                "dr_variant": dr_variant,
                "ess_schedule": _candidate_to_records(schedule_df),
                "ess_summary": ess_summary,
                "trading_recommendations": trading,
                "trading_summary": {
                    "total_surplus_events": len(trading),
                    "total_surplus_kw": round(sum(item["surplus_kw"] for item in trading), 2),
                    "expected_revenue_krw": trading_revenue,
                },
                "demand_response_events": dr_events,
                "dr_summary": {
                    "dr_event_count": len(dr_events),
                    "peak_threshold_kw": round(float(nl_df["predicted_net_load_kw"].quantile(0.85)), 2),
                },
                "tariff_saving": tariff_saving,
                "risk_score": risk_score,
                "risk_reasons": risk_reasons,
                "policy_violation_probability": policy_probability,
                "battery_degradation_cost_krw": degradation_cost,
                "expected_profit_krw": expected_profit,
            }
        )

    return {
        "scenario_mode": scenario_mode,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


@mcp.tool(description="Compare portfolio candidates on profit, risk, policy compliance probability, and battery degradation cost.", structured_output=True)
def compare_strategy_candidates(context_json: str, candidates_json: str) -> dict[str, Any]:
    context = json.loads(context_json)
    payload = json.loads(candidates_json)
    candidates = payload["candidates"] if isinstance(payload, dict) else payload

    comparisons: list[dict[str, Any]] = []
    for candidate in candidates:
        profit = float(candidate.get("expected_profit_krw", 0.0))
        risk = float(candidate.get("risk_score", 0.5))
        policy = float(candidate.get("policy_violation_probability", 0.0))
        degradation = float(candidate.get("battery_degradation_cost_krw", 0.0))
        explainability = 0.9
        if candidate.get("trading_variant") == "aggressive":
            explainability -= 0.08
        if candidate.get("dr_variant") == "high":
            explainability -= 0.05
        if candidate.get("ess_mode") == "tou_optimized":
            explainability -= 0.03
        if context.get("prosumer_type") == "Residential":
            explainability += 0.03 if candidate.get("dr_variant") == "low" else -0.02
        overall = round(profit * 0.0015 + (1 - risk) * 0.35 + (1 - policy) * 0.2 + explainability * 0.15 - degradation * 0.0008, 4)
        comparisons.append(
            {
                "candidate_id": candidate["candidate_id"],
                "expected_profit_krw": round(profit, 1),
                "risk_score": risk,
                "policy_violation_probability": policy,
                "battery_degradation_cost_krw": round(degradation, 1),
                "explainability_score": round(explainability, 2),
                "overall_score": overall,
                "summary": (
                    f"profit={profit:.1f}, risk={risk:.2f}, policy={policy:.2f}, "
                    f"degradation={degradation:.1f}, mode={candidate.get('scenario_mode')}"
                ),
            }
        )

    ranked = sorted(comparisons, key=lambda item: item["overall_score"], reverse=True)
    return {
        "recommended_candidate_id": ranked[0]["candidate_id"] if ranked else "",
        "comparisons": ranked,
    }


@mcp.tool(description="Recommend mode-specific planning guidance for short horizon, day ahead, anomaly response, and prosumer type.", structured_output=True)
def recommend_mode_profile(context_json: str) -> dict[str, Any]:
    context = json.loads(context_json)
    prosumer_type = context.get("prosumer_type", "Unknown")
    scenario_mode = _scenario_mode(context)

    if scenario_mode == "short_horizon":
        guidance = [
            "최신 피크와 단기 변동성 대응을 우선합니다.",
            "DR 반응 속도와 ESS 순간 대응력을 높입니다.",
        ]
    elif scenario_mode == "anomaly_response":
        guidance = [
            "정상 수익보다 안정성, 정책 준수, 피크 완화를 우선합니다.",
            "과도한 거래 전략보다 resilience 중심 조합을 우선 검토합니다.",
        ]
    else:
        guidance = [
            "day-ahead 수익성, 요금 절감, 피크 대응의 균형을 맞춥니다.",
            "ESS와 거래 조합의 경제성을 함께 비교합니다.",
        ]

    if prosumer_type == "Residential":
        guidance.append("주거형은 사용자 체감 불편이 큰 DR 고강도 전략을 피합니다.")
    elif prosumer_type in {"Commercial", "EnergyHub"}:
        guidance.append("상업형/허브형은 거래 수익과 ESS 활용률을 상대적으로 더 공격적으로 검토합니다.")

    return {
        "scenario_mode": scenario_mode,
        "prosumer_type": prosumer_type,
        "guidance": guidance,
    }


if __name__ == "__main__":
    mcp.run("stdio")
