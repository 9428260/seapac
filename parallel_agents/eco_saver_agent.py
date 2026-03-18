"""
Eco Saver Agent (PRD §5.2 — seapac_parallel_agents_prd.md).

Generates personalized energy-saving recommendations for residents without disrupting market operations.
Advisory only: no veto authority.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class EcoSaverOutput:
    """Eco Saver Agent output (PRD §5.2)."""
    recommendations: list[str] = field(default_factory=list)
    estimated_savings_krw: list[float] = field(default_factory=list)
    acceptance_probability: list[float] = field(default_factory=list)
    notification_payload: list[dict] = field(default_factory=list)
    llm_review: dict = field(default_factory=dict)


def run_eco_saver_agent(
    site_state: dict,
    candidate_actions: list[dict],
    load_forecast_kw: float | None = None,
    pricing_forecast_krw: float | None = None,
    peak_threshold_kw: float = 500.0,
) -> EcoSaverOutput:
    """
    Analyze load/peak and candidate actions to produce energy-saving recommendations.
    Example: "Running the washing machine after 21:00 may save approximately 500 KRW."
    """
    out = EcoSaverOutput()
    load_kw = float(site_state.get("load_kw", 0) or 0)
    pv_kw = float(site_state.get("pv_kw", 0) or 0)
    grid_price = float(site_state.get("grid_price", 100) or 100)
    time_str = str(site_state.get("time", ""))

    # Peak demand opportunity
    if load_kw > peak_threshold_kw:
        excess = load_kw - peak_threshold_kw
        # Shift 30% of excess to off-peak → approximate savings (0.25h * excess * price diff)
        price_diff = 20.0  # assume off-peak ~20 KRW/kWh cheaper
        est_save = round(excess * 0.3 * 0.25 * price_diff, 0)
        rec = f"피크 시간대 부하 초과({excess:.0f} kW). 가전 사용을 21:00 이후로 미루면 약 {max(100, int(est_save))} 원 절약 예상."
        out.recommendations.append(rec)
        out.estimated_savings_krw.append(max(100, int(est_save)))
        out.acceptance_probability.append(0.6)
        out.notification_payload.append({
            "type": "peak_shift",
            "message": rec,
            "estimated_savings_krw": max(100, int(est_save)),
            "timestamp": time_str,
        })

    # General behavioral recommendation if load is moderate-high
    if 0.3 * peak_threshold_kw < load_kw <= peak_threshold_kw:
        rec = "전력 사용이 보통 수준입니다. 세탁기·건조기는 야간(21:00 이후) 사용 시 요금 절감에 도움이 됩니다."
        out.recommendations.append(rec)
        out.estimated_savings_krw.append(500)
        out.acceptance_probability.append(0.5)
        out.notification_payload.append({
            "type": "behavioral",
            "message": rec,
            "estimated_savings_krw": 500,
            "timestamp": time_str,
        })

    # PV surplus: suggest self-consumption
    if pv_kw > 10 and load_kw < pv_kw * 0.5:
        rec = "태양광 잉여 전력이 많습니다. 가전 사용을 지금 시간대로 모으면 자가 소비율이 올라 수익성이 좋아집니다."
        out.recommendations.append(rec)
        out.estimated_savings_krw.append(300)
        out.acceptance_probability.append(0.55)
        out.notification_payload.append({
            "type": "self_consumption",
            "message": rec,
            "estimated_savings_krw": 300,
            "timestamp": time_str,
        })

    # Prevent notification fatigue: cap at 3 recommendations per step
    max_rec = 3
    if len(out.recommendations) > max_rec:
        out.recommendations = out.recommendations[:max_rec]
        out.estimated_savings_krw = out.estimated_savings_krw[:max_rec]
        out.acceptance_probability = out.acceptance_probability[:max_rec]
        out.notification_payload = out.notification_payload[:max_rec]

    try:
        from langchain_core.messages import HumanMessage, SystemMessage
        from alfp.llm import is_llm_enabled, get_llm

        if is_llm_enabled("parallel_eco"):
            system = """당신은 Eco Saver 병렬 심사 보조 분석기입니다.
현재 상태와 생성된 권고를 보고 주민 안내용 톤과 핵심 메시지를 한국어로 짧게 정리하세요.
JSON only:
{"summary": string, "user_message": string, "engagement_note": string}"""
            user = (
                f"site_state={json.dumps(site_state, ensure_ascii=False)}\n"
                f"candidate_actions={json.dumps(candidate_actions, ensure_ascii=False)}\n"
                f"recommendations={json.dumps(out.recommendations, ensure_ascii=False)}\n"
                f"notification_payload={json.dumps(out.notification_payload, ensure_ascii=False)}\n"
                "Output JSON only."
            )
            llm = get_llm(temperature=0.2, stage="parallel_eco")
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            text = resp.content if hasattr(resp, "content") else str(resp)
            out.llm_review = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except Exception:
        out.llm_review = {}

    return out
