"""
ForecastPlannerAgent - 예측 작업 계획 수립, 모델 선택, horizon 결정
LLM 또는 규칙 기반 단계형 planning으로 데이터 특성 해석부터 최종 계획 채택까지 수행합니다.
"""

import pandas as pd

from alfp.agents.state import ALFPState
from alfp.config import get_skills_config, get_system_prompt, get_user_prompt_template
from alfp.deepagents import invoke_deepagents_forecast_planner
from alfp.llm import is_llm_enabled
from alfp.memory import retrieve_best_practices, retrieve_similar_cases, retrieve_similar_failures
from alfp.tools.openweather import get_current_weather_tool
from alfp.skills.energy_forecast import EnergyForecastSkill


def _build_stats(df: pd.DataFrame, prosumer_id: str, requested_horizon: int) -> dict:
    """LLM 프롬프트용 통계 데이터를 구성합니다."""
    if "prosumer_type" not in df.columns:
        prosumer_type = "Unknown"
    else:
        mode_series = df["prosumer_type"].dropna().mode()
        prosumer_type = mode_series.iloc[0] if len(mode_series) > 0 else "Unknown"
    ts = df["timestamp"]
    data_range_days = (ts.max() - ts.min()).days + 1

    load = df["load_kw"]
    pv = df["pv_kw"]
    load_cv = (load.std() / load.mean() * 100) if load.mean() > 0 else 0

    return {
        "prosumer_id": prosumer_id,
        "prosumer_type": prosumer_type,
        "season": _infer_season(ts.iloc[-1].month if len(ts) > 0 else 1),
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
        "forecast_horizon_bucket": _horizon_bucket(requested_horizon),
        "load_cv_bucket": _value_bucket(load_cv, [15, 30, 45], ["stable", "moderate", "volatile", "extreme"]),
        "pv_ratio_bucket": _value_bucket((pv > 0).mean() * 100, [10, 35, 60], ["low", "medium", "high", "very_high"]),
        "tariff_profile": _infer_tariff_profile(df),
    }


def _infer_season(month: int) -> str:
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def _horizon_bucket(requested_horizon: int) -> str:
    if requested_horizon <= 24:
        return "intraday"
    if requested_horizon <= 96:
        return "day_ahead"
    return "multi_day"


def _value_bucket(value: float, thresholds: list[float], labels: list[str]) -> str:
    for idx, threshold in enumerate(thresholds):
        if value < threshold:
            return labels[idx]
    return labels[-1]


def _infer_tariff_profile(df: pd.DataFrame) -> str:
    if "price_buy" not in df.columns:
        return "unknown"
    price_buy = df["price_buy"].astype(float)
    spread = float(price_buy.max() - price_buy.min()) if len(price_buy) else 0.0
    if spread >= 80:
        return "high_tou_spread"
    if spread >= 30:
        return "moderate_tou_spread"
    return "flat_like"


def _classify_weather(weather_text: str) -> str:
    if not weather_text:
        return "unknown"
    lowered = weather_text.lower()
    if "rain" in lowered or "drizzle" in lowered or "shower" in lowered:
        return "rainy"
    if "snow" in lowered or "sleet" in lowered:
        return "snowy"
    if "cloud" in lowered or "overcast" in lowered:
        return "cloudy"
    if "clear" in lowered or "sun" in lowered:
        return "clear"
    return "mixed"


def _build_memory_context(stats: dict, state: ALFPState) -> dict:
    return {
        "operating_mode": state.get("operating_mode", "day_ahead"),
        "forecast_horizon_bucket": stats.get("forecast_horizon_bucket"),
        "stats": {
            "prosumer_type": stats.get("prosumer_type"),
            "season": stats.get("season"),
            "weather_label": stats.get("weather_label"),
            "tariff_profile": stats.get("tariff_profile"),
            "load_cv_bucket": stats.get("load_cv_bucket"),
            "pv_ratio_bucket": stats.get("pv_ratio_bucket"),
        },
        "tags": {
            "prosumer_type": stats.get("prosumer_type"),
            "season": stats.get("season"),
            "weather": stats.get("weather_label"),
            "tariff": stats.get("tariff_profile"),
            "operating_mode": state.get("operating_mode", "day_ahead"),
            "forecast_horizon_bucket": stats.get("forecast_horizon_bucket"),
        },
        "validation_kpi": (state.get("validation_metrics") or {}).get("kpi") or {},
        "plan": {
            "selected_model": (state.get("forecast_plan") or {}).get("selected_model"),
            "forecast_horizon_steps": (state.get("forecast_plan") or {}).get("forecast_horizon_steps"),
            "prosumer_type": stats.get("prosumer_type"),
        },
    }


def _build_memory_retrieval(prosumer_id: str, stats: dict, state: ALFPState) -> dict:
    current_context = _build_memory_context(stats, state)
    return {
        "current_context": current_context,
        "similar_cases": retrieve_similar_cases(prosumer_id, current_context, top_k=3),
        "best_practices": retrieve_best_practices(prosumer_id, current_context, top_k=3),
        "similar_failures": retrieve_similar_failures(prosumer_id, current_context, top_k=3),
    }


def _candidate_model_config(model: str, prosumer_type: str, stats: dict, variant: str) -> dict:
    fp_cfg = get_skills_config().get("forecast_planner", {}).get("fallback", {})
    lgbm_cfg = fp_cfg.get("lgbm", {})
    xgb_cfg = fp_cfg.get("xgboost", {})

    if model == "lgbm":
        base_num_leaves = (
            lgbm_cfg.get("num_leaves_energy_hub", 127)
            if prosumer_type == "EnergyHub"
            else lgbm_cfg.get("num_leaves_default", 63)
        )
        num_leaves = base_num_leaves
        learning_rate = lgbm_cfg.get("learning_rate", 0.05)
        if variant == "robust":
            num_leaves = max(31, int(base_num_leaves * 0.75))
            learning_rate = min(0.08, learning_rate + 0.01)
        elif variant == "peak_sensitive":
            num_leaves = min(255, int(base_num_leaves * 1.25))
        return {
            "num_leaves": num_leaves,
            "n_estimators": lgbm_cfg.get("n_estimators", 500),
            "learning_rate": learning_rate,
        }

    max_depth = xgb_cfg.get("max_depth", 6)
    learning_rate = xgb_cfg.get("learning_rate", 0.05)
    n_estimators = xgb_cfg.get("n_estimators", 300)
    if variant == "robust":
        max_depth = max(4, max_depth - 1)
        learning_rate = min(0.08, learning_rate + 0.01)
    elif variant == "peak_sensitive":
        max_depth = min(10, max_depth + 1)
        n_estimators = int(n_estimators * 1.1)
    return {
        "max_depth": max_depth,
        "n_estimators": n_estimators,
        "learning_rate": learning_rate,
    }


def _derive_data_characteristics(stats: dict, state: ALFPState) -> list[str]:
    persistent = state.get("persistent_memory") or {}
    prev_metrics = state.get("validation_metrics") or {}
    memory_retrieval = state.get("memory_retrieval") or {}
    characteristics: list[str] = []

    if stats["n_records"] >= 5000:
        characteristics.append("학습 데이터가 충분해 복잡한 트리 계열 모델을 안정적으로 학습할 수 있습니다.")
    else:
        characteristics.append("학습 데이터가 상대적으로 적어 과적합을 억제하는 보수적 설정이 유리합니다.")

    if stats["load_cv"] >= 35:
        characteristics.append("부하 변동성이 높아 피크 시점 오차와 분산 확대 리스크가 큽니다.")
    elif stats["load_cv"] >= 20:
        characteristics.append("부하 변동성이 중간 수준이라 일반 성능과 피크 대응을 함께 봐야 합니다.")
    else:
        characteristics.append("부하 패턴이 비교적 안정적이라 해석 가능성과 일반화 성능을 우선할 수 있습니다.")

    if stats["pv_ratio"] >= 35:
        characteristics.append("PV 기여도가 높아 기상 변화가 Net Load 안정성에 직접 영향을 줍니다.")
    elif stats["pv_ratio"] <= 10:
        characteristics.append("PV 영향이 낮아 Load 중심 전략이 더 중요합니다.")

    if prev_metrics:
        kpi = prev_metrics.get("kpi") or {}
        if kpi.get("MAPE_pass") is False:
            characteristics.append("직전 검증에서 MAPE KPI 미달이 발생해 모델 구조 또는 피처 전략 변경이 필요합니다.")
        if kpi.get("peak_acc_pass") is False:
            characteristics.append("직전 검증에서 피크 정확도 미달이 발생해 피크 민감 전략이 필요합니다.")

    last_plan = persistent.get("last_plan") or {}
    if last_plan.get("selected_model"):
        characteristics.append(f"이전 런에서 {last_plan['selected_model']} 모델이 사용되어 재계획 시 비교 기준으로 활용할 수 있습니다.")

    similar_cases = memory_retrieval.get("similar_cases") or []
    if similar_cases:
        top_case = similar_cases[0]
        matched = ", ".join(top_case.get("matched_features") or []) or "공통 조건 다수"
        characteristics.append(f"유사 과거 사례가 검색되었으며 주요 일치 조건은 {matched} 입니다.")

    return characteristics


def _estimate_candidate_risk(stats: dict, state: ALFPState, candidate: dict) -> tuple[float, list[str]]:
    prev_metrics = state.get("validation_metrics") or {}
    kpi = prev_metrics.get("kpi") or {}
    risk = 0.18
    reasons: list[str] = []

    if stats["n_records"] < 5000 and candidate["model"] == "lgbm":
        risk += 0.12
        reasons.append("표본 수가 충분하지 않아 고복잡도 트리 구성의 안정성이 낮을 수 있습니다.")
    if stats["load_cv"] >= 35 and candidate["variant"] != "peak_sensitive":
        risk += 0.10
        reasons.append("변동성이 높은 데이터인데 피크 민감 전략이 아니어서 피크 추종력이 떨어질 수 있습니다.")
    if stats["pv_ratio"] >= 35 and candidate["forecast_horizon"] > stats["requested_horizon"]:
        risk += 0.08
        reasons.append("PV 영향이 큰데 horizon이 길어 기상 불확실성이 커질 수 있습니다.")
    if kpi.get("MAPE_pass") is False and candidate["model"] == state.get("forecast_plan", {}).get("selected_model"):
        risk += 0.14
        reasons.append("직전 실패와 동일 모델이라 재실험 효과가 제한될 수 있습니다.")
    if kpi.get("peak_acc_pass") is False and candidate["variant"] == "robust":
        risk += 0.08
        reasons.append("보수적 설정은 피크 구간 민감도를 더 낮출 수 있습니다.")

    if not reasons:
        reasons.append("현재 통계 기준으로 구조적 고위험 신호는 제한적입니다.")

    return round(min(risk, 0.95), 2), reasons


def _build_candidate_strategies(stats: dict, state: ALFPState) -> list[dict]:
    default_model = EnergyForecastSkill.select_model(stats["n_records"], stats["prosumer_type"])
    other_model = "xgboost" if default_model == "lgbm" else "lgbm"

    candidates = [
        {
            "candidate_id": f"{default_model}_balanced",
            "model": default_model,
            "variant": "balanced",
            "forecast_horizon": stats["requested_horizon"],
            "rationale": "기본 모델 선택 규칙과 현재 데이터 규모를 가장 직접적으로 반영한 균형 전략입니다.",
        },
        {
            "candidate_id": f"{other_model}_robust",
            "model": other_model,
            "variant": "robust",
            "forecast_horizon": stats["requested_horizon"],
            "rationale": "대체 모델을 사용해 과적합 또는 구조적 편향 가능성을 점검하는 비교 전략입니다.",
        },
        {
            "candidate_id": f"{default_model}_peak_sensitive",
            "model": default_model,
            "variant": "peak_sensitive",
            "forecast_horizon": stats["requested_horizon"],
            "rationale": "피크 부하 구간 대응력을 우선해 KPI 중 peak accuracy를 보완하는 전략입니다.",
        },
    ]

    built: list[dict] = []
    memory_retrieval = state.get("memory_retrieval") or {}
    similar_cases = memory_retrieval.get("similar_cases") or []
    best_practices = memory_retrieval.get("best_practices") or {}
    for candidate in candidates:
        config = _candidate_model_config(candidate["model"], stats["prosumer_type"], stats, candidate["variant"])
        risk_score, risk_reasons = _estimate_candidate_risk(stats, state, candidate)
        explainability_score = 0.85 if candidate["model"] == "lgbm" else 0.75
        if candidate["variant"] == "robust":
            explainability_score += 0.05
        strengths = [
            "데이터 특성과 모델 선택 규칙의 정합성이 높습니다." if candidate["variant"] == "balanced" else
            "실패 시 비교 실험의 기준점으로 사용하기 좋습니다." if candidate["variant"] == "robust" else
            "피크 구간 민감도를 높여 운영 리스크를 줄일 수 있습니다."
        ]
        for case in similar_cases:
            case_plan = ((case.get("entry") or {}).get("context") or {}).get("plan") or {}
            if case_plan.get("selected_model") == candidate["model"] and float((case.get("entry") or {}).get("performance_score", 0.0)) >= 0.7:
                risk_score = max(0.05, risk_score - 0.05)
                strengths.append("유사 사례에서 같은 모델 계열이 성공해 재사용 근거가 있습니다.")
                break
        if best_practices.get("season") and candidate["variant"] == "balanced":
            strengths.append("동일 계절 best practice를 기준선 후보에 반영했습니다.")
        if best_practices.get("weather") and candidate["variant"] == "peak_sensitive":
            strengths.append("동일 날씨 조건 best practice를 반영해 기상 민감도를 높였습니다.")
        built.append({
            **candidate,
            "model_config": config,
            "strengths": strengths,
            "risk_score": round(min(risk_score, 0.95), 2),
            "risk_reasons": risk_reasons,
            "explainability_score": round(min(explainability_score, 0.95), 2),
        })
    return built


def _build_failure_hypotheses(stats: dict, state: ALFPState, candidates: list[dict]) -> list[str]:
    hypotheses: list[str] = []
    prev_metrics = state.get("validation_metrics") or {}
    kpi = prev_metrics.get("kpi") or {}
    similar_failures = (state.get("memory_retrieval") or {}).get("similar_failures") or []

    if kpi.get("MAPE_pass") is False:
        hypotheses.append("직전 MAPE 미달은 모델 구조보다 feature 구성 또는 horizon 설정 부조화에서 발생했을 가능성이 있습니다.")
    if kpi.get("peak_acc_pass") is False:
        hypotheses.append("피크 정확도 미달은 피크 민감 설정 부족 또는 고변동 구간 학습 부족 때문일 수 있습니다.")
    if stats["load_cv"] >= 35:
        hypotheses.append("부하 변동성이 높아 동일 모델이라도 시간대별 오차 편차가 커졌을 가능성이 있습니다.")
    if stats["pv_ratio"] >= 35:
        hypotheses.append("PV 비중이 높아 날씨 반영 지연 또는 기상 불확실성이 Net Load 오차를 키웠을 수 있습니다.")
    if similar_failures:
        top_failure = similar_failures[0]
        failure_pattern = top_failure.get("failure_pattern") or {}
        hypotheses.append(
            "과거 실패 패턴과 유사합니다. "
            f"MAPE_pass={failure_pattern.get('mape_pass')}, peak_acc_pass={failure_pattern.get('peak_acc_pass')} "
            "조합이 반복되어 동일 모델/설정 재사용을 피해야 합니다."
        )
    if not hypotheses:
        lowest_risk = min(candidates, key=lambda item: item["risk_score"])
        hypotheses.append(f"현재 주요 실패 가설은 제한적이며, {lowest_risk['candidate_id']}를 기준 전략으로 검증하는 것이 합리적입니다.")

    return hypotheses


def _build_reexperiment_plan(stats: dict, state: ALFPState, candidates: list[dict], failure_hypotheses: list[str]) -> list[str]:
    plan_steps = [
        "우선순위 1 후보를 기준선으로 학습하고 validation KPI를 다시 측정합니다.",
        "피크 정확도 미달 시 peak_sensitive 후보를 별도 재실험해 peak error 개선 여부를 확인합니다.",
        "MAPE 미달 시 robust 후보와 비교해 일반화 오차가 줄어드는지 검증합니다.",
    ]
    if state.get("plan_retry_count", 0) > 0:
        plan_steps.append("이번 재계획은 이전 실패 후보와 동일 모델을 우선 배제하고 비교 실험 결과를 기록합니다.")
    if stats["pv_ratio"] >= 35:
        plan_steps.append("PV 영향이 큰 구간은 날씨 반영 여부를 함께 점검해 재실험 결과를 해석합니다.")
    if failure_hypotheses:
        plan_steps.append(f"가장 가능성이 높은 실패 가설: {failure_hypotheses[0]}")
    similar_cases = (state.get("memory_retrieval") or {}).get("similar_cases") or []
    if similar_cases:
        top_case = similar_cases[0]
        matched = ", ".join(top_case.get("matched_features") or []) or "유사 조건"
        plan_steps.append(f"유사 성공 사례의 전략을 재조합합니다. 기준 조건: {matched}")
    best_practices = (state.get("memory_retrieval") or {}).get("best_practices") or {}
    if best_practices.get("tariff"):
        plan_steps.append("동일 tariff profile의 best practice를 반영해 시간대별 feature 또는 threshold를 재조정합니다.")
    similar_failures = (state.get("memory_retrieval") or {}).get("similar_failures") or []
    if similar_failures:
        plan_steps.append("과거 유사 실패 사례에서 사용한 모델/변형은 우선 배제하고 대체 후보를 먼저 검증합니다.")
    return plan_steps


def _build_candidate_risk_comparison(candidates: list[dict]) -> list[dict]:
    ranked = sorted(candidates, key=lambda item: (item["risk_score"], -item["explainability_score"]))
    return [
        {
            "candidate_id": candidate["candidate_id"],
            "risk_score": candidate["risk_score"],
            "explainability_score": candidate["explainability_score"],
            "summary": candidate["risk_reasons"][0] if candidate["risk_reasons"] else "",
        }
        for candidate in ranked
    ]


def _select_most_explainable_candidate(candidates: list[dict]) -> dict:
    def _score(candidate: dict) -> float:
        return round((1 - candidate["risk_score"]) * 0.6 + candidate["explainability_score"] * 0.4, 4)

    ranked = sorted(candidates, key=_score, reverse=True)
    selected = dict(ranked[0])
    selected["selection_score"] = _score(selected)
    return selected


def _fallback_plan(stats: dict, state: ALFPState) -> dict:
    """
    LLM 호출 실패 시 규칙 기반 deep planning.
    """
    data_characteristics = _derive_data_characteristics(stats, state)
    candidates = _build_candidate_strategies(stats, state)
    risk_comparison = _build_candidate_risk_comparison(candidates)
    failure_hypotheses = _build_failure_hypotheses(stats, state, candidates)
    reexperiment_plan = _build_reexperiment_plan(stats, state, candidates, failure_hypotheses)
    selected = _select_most_explainable_candidate(candidates)

    return {
        "data_characteristics": data_characteristics,
        "candidate_strategies": candidates,
        "candidate_risk_comparison": risk_comparison,
        "failure_hypotheses": failure_hypotheses,
        "reexperiment_plan": reexperiment_plan,
        "selected_candidate_id": selected["candidate_id"],
        "selected_model": selected["model"],
        "model_config": selected["model_config"],
        "forecast_horizon": selected["forecast_horizon"],
        "reasoning": (
            f"{selected['candidate_id']} 후보는 risk_score={selected['risk_score']}, "
            f"explainability_score={selected['explainability_score']}로 가장 설명 가능성이 높은 계획입니다."
        ),
        "data_insights": " / ".join(data_characteristics[:3]),
        "risk_factors": selected["risk_reasons"],
        "explainability_notes": [
            "후보별 risk_score와 explainability_score를 함께 비교했습니다.",
            "최종 계획은 가장 낮은 위험과 가장 높은 설명 가능성의 균형으로 선택했습니다.",
        ],
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
    memory_retrieval = state.get("memory_retrieval") or {}
    similar_cases = memory_retrieval.get("similar_cases") or []
    if similar_cases:
        top_case = similar_cases[0]
        parts.append(
            f"[유사 사례] similarity={top_case.get('similarity_score')}, matched={', '.join(top_case.get('matched_features') or [])}"
        )
    similar_failures = memory_retrieval.get("similar_failures") or []
    if similar_failures:
        top_failure = similar_failures[0]
        parts.append(f"[유사 실패] similarity={top_failure.get('similarity_score')} → 동일 실패 패턴 회피 필요")
    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"


def _normalize_plan(plan: dict, stats: dict, state: ALFPState) -> dict:
    fallback = _fallback_plan(stats, state)
    if not isinstance(plan, dict):
        return fallback

    normalized = dict(fallback)
    normalized.update({k: v for k, v in plan.items() if v not in (None, "")})

    if normalized.get("selected_candidate_id"):
        for candidate in normalized.get("candidate_strategies") or []:
            if candidate.get("candidate_id") == normalized["selected_candidate_id"]:
                normalized.setdefault("selected_model", candidate.get("model"))
                normalized.setdefault("model_config", candidate.get("model_config"))
                normalized.setdefault("forecast_horizon", candidate.get("forecast_horizon"))
                break

    normalized["candidate_strategies"] = normalized.get("candidate_strategies") or fallback["candidate_strategies"]
    normalized["candidate_risk_comparison"] = normalized.get("candidate_risk_comparison") or _build_candidate_risk_comparison(normalized["candidate_strategies"])
    normalized["failure_hypotheses"] = normalized.get("failure_hypotheses") or fallback["failure_hypotheses"]
    normalized["reexperiment_plan"] = normalized.get("reexperiment_plan") or fallback["reexperiment_plan"]
    normalized["data_characteristics"] = normalized.get("data_characteristics") or fallback["data_characteristics"]
    normalized["risk_factors"] = normalized.get("risk_factors") or fallback["risk_factors"]
    normalized["explainability_notes"] = normalized.get("explainability_notes") or fallback["explainability_notes"]
    normalized["selected_model"] = normalized.get("selected_model") or fallback["selected_model"]
    normalized["model_config"] = normalized.get("model_config") or fallback["model_config"]
    normalized["forecast_horizon"] = int(normalized.get("forecast_horizon") or fallback["forecast_horizon"])

    return normalized


def forecast_planner_agent(state: ALFPState) -> ALFPState:
    """
    ForecastPlannerAgent 노드 함수.
    데이터 특성 해석 → 후보 전략 생성 → 리스크 비교 → 실패 가설 → 재실험 계획 → 최종 계획 채택 순서로 planning 한다.
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    is_replan = (state.get("plan_retry_count", 0) or 0) > 0
    if is_replan:
        log.append("[ForecastPlannerAgent] 단계형 재계획 수립 시작")
    else:
        log.append("[ForecastPlannerAgent] 단계형 예측 계획 수립 시작")

    df = state["feature_df"]
    prosumer_id = state.get("prosumer_id", "unknown")
    requested_horizon = state.get("forecast_horizon", 96)

    try:
        stats = _build_stats(df, prosumer_id, requested_horizon)
        try:
            weather_text = get_current_weather_tool(city="Seoul")
            weather_block = f"\n[현재 날씨 (OpenWeather)]\n{weather_text}"
        except Exception:
            weather_block = ""
            weather_text = ""
        stats["weather_label"] = _classify_weather(weather_text)
        state = {
            **state,
            "memory_retrieval": _build_memory_retrieval(prosumer_id, stats, state),
        }
        retrieval = state.get("memory_retrieval") or {}
        log.append(
            "  memory retrieval: "
            f"similar_cases={len(retrieval.get('similar_cases') or [])}, "
            f"best_practice_facets={len([k for k, v in (retrieval.get('best_practices') or {}).items() if v])}, "
            f"similar_failures={len(retrieval.get('similar_failures') or [])}"
        )
        stats["weather_block"] = weather_block

        replan_block = _build_replan_context(state)
        stats["weather_block"] = (replan_block + stats["weather_block"]) if replan_block else stats["weather_block"]

        if is_llm_enabled("alfp_forecast_planner"):
            system_prompt = get_system_prompt("forecast_planner")
            user_template = get_user_prompt_template("forecast_planner")
            user_prompt = user_template.format(**stats)

            log.append("  1/6 데이터 특성 해석")
            log.append("  2/6 후보 전략 생성")
            log.append("  3/6 후보별 리스크 비교")
            log.append("  4/6 실패 원인 가설 생성")
            log.append("  5/6 재실험 계획 수립")
            log.append("  6/6 가장 설명 가능한 계획 채택")
            log.append("  deepagents 기반 forecast planner 호출 중...")
            plan = invoke_deepagents_forecast_planner(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage="alfp_forecast_planner",
            )
            log.append("  deepagents 기반 forecast planner 응답 수신 완료")
        else:
            log.append("  LLM 비활성화 상태 - 규칙 기반 단계형 planning 사용")
            plan = _fallback_plan(stats, state)

    except Exception as e:
        errors.append(f"[ForecastPlannerAgent] LLM 오류 → 단계형 fallback 적용: {e}")
        stats = _build_stats(df, prosumer_id, requested_horizon)
        stats["weather_label"] = "unknown"
        state = {
            **state,
            "memory_retrieval": _build_memory_retrieval(prosumer_id, stats, state),
        }
        plan = _fallback_plan(stats, state)

    normalized_plan = _normalize_plan(plan, stats, state)
    selected_model = normalized_plan.get("selected_model", "lgbm")
    model_config = normalized_plan.get("model_config", {})
    horizon = int(normalized_plan.get("forecast_horizon", requested_horizon))

    forecast_plan = {
        "prosumer_id": prosumer_id,
        "prosumer_type": stats["prosumer_type"],
        "data_range_days": stats["data_range_days"],
        "n_train_records": stats["n_records"],
        "llm_used": is_llm_enabled("alfp_forecast_planner"),
        "selected_candidate_id": normalized_plan.get("selected_candidate_id"),
        "selected_model": selected_model,
        "forecast_horizon_steps": horizon,
        "forecast_horizon_hours": horizon / 4,
        "model_config": model_config,
        "llm_reasoning": normalized_plan.get("reasoning", ""),
        "llm_data_insights": normalized_plan.get("data_insights", ""),
        "llm_risk_factors": normalized_plan.get("risk_factors", []),
        "data_characteristics": normalized_plan.get("data_characteristics", []),
        "candidate_strategies": normalized_plan.get("candidate_strategies", []),
        "candidate_risk_comparison": normalized_plan.get("candidate_risk_comparison", []),
        "failure_hypotheses": normalized_plan.get("failure_hypotheses", []),
        "reexperiment_plan": normalized_plan.get("reexperiment_plan", []),
        "explainability_notes": normalized_plan.get("explainability_notes", []),
        "memory_retrieval_summary": {
            "similar_case_count": len((state.get("memory_retrieval") or {}).get("similar_cases") or []),
            "best_practice_facets": [
                facet for facet, items in ((state.get("memory_retrieval") or {}).get("best_practices") or {}).items() if items
            ],
            "similar_failure_count": len((state.get("memory_retrieval") or {}).get("similar_failures") or []),
        },
    }

    log.append(f"  선택 후보: {forecast_plan.get('selected_candidate_id')}")
    log.append(f"  선택 모델: {selected_model.upper()}")
    log.append(f"  예측 Horizon: {horizon} 스텝 ({horizon/4:.1f}시간)")
    log.append(f"  후보 전략 수: {len(forecast_plan['candidate_strategies'])}")
    log.append(f"  최종 채택 근거: {normalized_plan.get('reasoning', '')}")
    log.append("[ForecastPlannerAgent] 완료")

    return {
        **state,
        "selected_model": selected_model,
        "model_config": model_config,
        "forecast_horizon": horizon,
        "forecast_plan": forecast_plan,
        "memory_retrieval": state.get("memory_retrieval"),
        "messages": log,
        "errors": errors,
    }
