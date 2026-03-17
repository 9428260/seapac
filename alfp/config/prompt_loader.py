"""
LLM 프롬프트 외부 파일 로더.
alfp/config/prompts/prompts.txt 한 파일에서 [에이전트.system] / [에이전트.user] 섹션을 읽습니다.
파일이 없거나 섹션이 없으면 내장 기본값을 반환합니다.
"""

import re
from pathlib import Path

_PROMPTS_FILE = Path(__file__).resolve().parent / "prompts" / "prompts.txt"
_SECTION_PATTERN = re.compile(r"\n\[(\w+)\.(system|user)\]\n", re.MULTILINE)

# 에이전트별 기본 프롬프트 (파일 없을 때 또는 해당 섹션 없을 때 사용)
_DEFAULTS = {
    "forecast_planner": {
        "system": """당신은 에너지 수요 예측 전문 AI 플래너입니다.
주어진 프로슈머 데이터 통계와 현재 날씨(선택)를 분석하여 단계형 planning을 수행합니다.
반드시 아래 순서로 사고한 뒤 JSON으로만 응답하세요.
1. 데이터 특성 해석
2. 후보 전략 생성
3. 후보별 리스크 비교
4. 실패 원인 가설 생성
5. 재실험 계획 수립
6. 가장 설명 가능한 계획 채택

반드시 아래 JSON 형식으로만 응답하세요 (코드블록 없이 순수 JSON):
{
  "data_characteristics": ["<특성 1>", "<특성 2>", "<특성 3>"],
  "candidate_strategies": [
    {
      "candidate_id": "<고유 ID>",
      "model": "lgbm" 또는 "xgboost",
      "variant": "balanced | robust | peak_sensitive",
      "model_config": {
        "num_leaves": <정수, lgbm일 때>,
        "max_depth": <정수, xgboost일 때>,
        "n_estimators": <정수>,
        "learning_rate": <실수>
      },
      "forecast_horizon": <예측 스텝 수>,
      "rationale": "<후보 전략 설명>",
      "strengths": ["<강점 1>", "<강점 2>"],
      "risk_score": <0.0~1.0>,
      "risk_reasons": ["<리스크 1>", "<리스크 2>"],
      "explainability_score": <0.0~1.0>
    }
  ],
  "candidate_risk_comparison": [
    {
      "candidate_id": "<ID>",
      "risk_score": <0.0~1.0>,
      "explainability_score": <0.0~1.0>,
      "summary": "<비교 요약>"
    }
  ],
  "failure_hypotheses": ["<실패 가설 1>", "<실패 가설 2>"],
  "reexperiment_plan": ["<재실험 단계 1>", "<재실험 단계 2>", "<재실험 단계 3>"],
  "selected_candidate_id": "<최종 채택 후보 ID>",
  "selected_model": "lgbm" 또는 "xgboost",
  "model_config": {
    "num_leaves": <정수, lgbm일 때>,
    "max_depth": <정수, xgboost일 때>,
    "n_estimators": <정수>,
    "learning_rate": <실수>
  },
  "forecast_horizon": <예측 스텝 수 (15분 단위 정수)>,
  "reasoning": "<최종 채택 이유를 한국어 2~4문장으로 설명>",
  "data_insights": "<핵심 데이터 인사이트를 한국어 1~3문장으로 설명>",
  "risk_factors": ["<주의 사항 1>", "<주의 사항 2>"],
  "explainability_notes": ["<설명 가능성 근거 1>", "<설명 가능성 근거 2>"]
}""",
        "user": """다음 프로슈머의 데이터를 분석하고 단계형 예측 전략을 수립해 주세요.

[프로슈머 정보]
- ID: {prosumer_id}
- 타입: {prosumer_type}
- 데이터 기간: {data_range_days}일
- 학습 레코드 수: {n_records}건 (15분 해상도)

[부하(Load) 통계]
- 평균: {load_mean:.2f} kW
- 표준편차: {load_std:.2f} kW
- 최소: {load_min:.2f} kW / 최대: {load_max:.2f} kW
- 변동계수(CV): {load_cv:.2f}%

[PV 발전량 통계]
- 평균: {pv_mean:.2f} kW (0 포함)
- 최대: {pv_max:.2f} kW
- PV 발전 비율(>0): {pv_ratio:.1f}%

[요금 정보]
- 평균 구매가: {price_buy_mean:.2f}
- 평균 판매가: {price_sell_mean:.2f}

[요청 예측 Horizon]: {requested_horizon} 스텝 ({horizon_hours:.1f}시간)
{weather_block}

요구사항:
- 반드시 3개 이상의 후보 전략을 제시하세요.
- 후보별 risk_score와 explainability_score를 함께 제시하세요.
- 최종 선택은 "가장 설명 가능한 계획" 기준으로 채택하세요.
- 재계획 문맥이 있으면 실패 원인 가설과 재실험 계획에 반영하세요.""",
    },
    "decision": {
        "system": """당신은 공동주택 에너지 커뮤니티의 운영 전략 AI 어드바이저입니다.
전력 예측 결과를 바탕으로 ESS 운영, 에너지 거래, 수요반응(DR) 전략을 수립합니다.

반드시 아래 JSON 형식으로만 응답하세요 (코드블록 없이 순수 JSON):
{
  "ess_strategy": "<ESS 운영 전략을 한국어 3~4문장으로 구체적으로 설명>",
  "trading_strategy": "<에너지 거래 전략을 한국어 2~3문장으로 설명>",
  "dr_strategy": "<수요반응(DR) 전략을 한국어 2~3문장으로 설명>",
  "overall_recommendation": "<종합 에너지 관리 전략을 한국어 3~5문장으로 설명>",
  "priority_actions": [
    "<즉시 실행 권고 사항 1>",
    "<즉시 실행 권고 사항 2>",
    "<즉시 실행 권고 사항 3>"
  ],
  "expected_savings": "<예상 절감 효과를 정성적으로 한국어 1~2문장으로 설명>",
  "alert_level": "정상" 또는 "주의" 또는 "경고"
}""",
        "user": """다음 전력 예측 및 운영 데이터를 바탕으로 운영 전략을 수립해 주세요.

[프로슈머 정보]
- 타입: {prosumer_type}
- ID: {prosumer_id}

[Net Load 현황]
- 평균 Net Load: {nl_mean:.2f} kW
- 최대 Net Load: {nl_max:.2f} kW
- 최소 Net Load: {nl_min:.2f} kW
- 피크 임계값(85th percentile): {peak_threshold:.2f} kW

[ESS 운영 계획]
- 총 스텝: {total_steps}건 (15분 단위)
- 충전 스텝: {charge_steps}건
- 방전 스텝: {discharge_steps}건
- 대기 스텝: {idle_steps}건
- ESS 용량: {bess_kwh_cap} kWh

[에너지 거래 현황]
- 잉여 전력 발생 건수: {surplus_events}건
- 총 잉여 전력량: {total_surplus:.2f} kW

[수요반응(DR) 이벤트]
- DR 이벤트 횟수: {dr_count}건
- 피크 임계값: {peak_threshold:.2f} kW

[예측 성능 참고]
- 부하 예측 MAPE: {load_mape:.2f}%
- Net Load 예측 MAPE: {nl_mape:.2f}%

[TOU 요금 절감 시뮬레이션]
- 기준 비용: {base_cost_krw:,.0f} 원
- ESS 적용 후 비용: {adjusted_cost_krw:,.0f} 원
- 절감액: {saving_krw:,.0f} 원 ({saving_pct:.2f}%)""",
    },
    "validation": {
        "system": """당신은 전력 수요 예측 모델의 성능을 평가하는 AI 전문가입니다.
주어진 예측 성능 지표를 분석하여 종합 평가와 개선 방향을 제시합니다.

반드시 아래 JSON 형식으로만 응답하세요 (코드블록 없이 순수 JSON):
{
  "overall_assessment": "<전반적인 예측 성능 평가를 한국어 2~3문장으로>",
  "load_analysis": "<부하 예측 성능 분석을 한국어 1~2문장으로>",
  "pv_analysis": "<PV 예측 성능 분석을 한국어 1~2문장으로>",
  "net_load_analysis": "<Net Load 예측 성능 분석을 한국어 1~2문장으로>",
  "improvement_suggestions": [
    "<개선 제안 1>",
    "<개선 제안 2>",
    "<개선 제안 3>"
  ],
  "operational_impact": "<예측 정확도가 ESS/에너지 거래 운영에 미치는 영향을 한국어 2문장으로>",
  "confidence_level": "높음" 또는 "보통" 또는 "낮음"
}""",
        "user": """다음 전력 예측 모델의 성능 지표를 분석해 주세요.

[KPI 목표]
- MAPE 목표: 10% 미만
- 피크 예측 정확도 목표: 90% 이상

[부하(Load) 예측 성능]
- MAE: {load_mae} kW | RMSE: {load_rmse} kW | MAPE: {load_mape}%
- 실제 피크: {load_true_peak} kW | 예측 피크: {load_pred_peak} kW | 피크 오차: {load_peak_err}%
- KPI 달성: MAPE {load_mape_kpi} / 피크 {load_peak_kpi}

[PV 발전량 예측 성능]
- MAE: {pv_mae} kW | RMSE: {pv_rmse} kW | MAPE: {pv_mape}%
- 실제 피크: {pv_true_peak} kW | 예측 피크: {pv_pred_peak} kW | 피크 오차: {pv_peak_err}%

[Net Load 예측 성능]
- MAE: {nl_mae} kW | RMSE: {nl_rmse} kW | MAPE: {nl_mape}%
- 실제 피크: {nl_true_peak} kW | 예측 피크: {nl_pred_peak} kW | 피크 오차: {nl_peak_err}%

[프로슈머 정보]
- 타입: {prosumer_type}
- 사용 모델: {selected_model}
- 검증 샘플 수: {n_samples}건""",
    },
}


def _load_prompts_from_file() -> dict:
    """prompts.txt 를 파싱해 { agent: { system: str, user: str } } 형태로 반환."""
    if not _PROMPTS_FILE.exists():
        return {}
    try:
        text = _PROMPTS_FILE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    # \n[agent.type]\n 로 구분. 앞에 \n 을 붙여 첫 섹션도 동일하게 처리
    parts = _SECTION_PATTERN.split("\n" + text)
    out = {}
    # parts[0] = 앞쪽 주석 등, parts[1],2,3 = agent, type, content, parts[4],5,6 = ...
    for i in range(1, len(parts) - 2, 3):
        agent, ptype, content = parts[i], parts[i + 1], parts[i + 2]
        if agent not in out:
            out[agent] = {}
        out[agent][ptype] = content.strip()
    return out


_cached_prompts: dict | None = None


def _get_prompts() -> dict:
    """통합 파일에서 로드한 프롬프트와 기본값을 병합해 반환."""
    global _cached_prompts
    if _cached_prompts is not None:
        return _cached_prompts
    from_file = _load_prompts_from_file()
    merged = {}
    for agent, default_block in _DEFAULTS.items():
        merged[agent] = {**default_block, **(from_file.get(agent) or {})}
    for agent, block in from_file.items():
        if agent not in merged:
            merged[agent] = dict(block)
    _cached_prompts = merged
    return _cached_prompts


def get_prompt(agent_name: str, prompt_type: str) -> str:
    """
    에이전트별 프롬프트 문자열을 반환합니다.

    Args:
        agent_name: "forecast_planner" | "decision" | "validation"
        prompt_type: "system" | "user"

    Returns:
        프롬프트 문자열. prompts.txt 에 해당 섹션이 있으면 그 내용, 없으면 내장 기본값.
    """
    prompts = _get_prompts()
    block = prompts.get(agent_name, {})
    return block.get(prompt_type, "")


def get_system_prompt(agent_name: str) -> str:
    """에이전트의 시스템 프롬프트를 반환합니다."""
    return get_prompt(agent_name, "system")


def get_user_prompt_template(agent_name: str) -> str:
    """에이전트의 유저 프롬프트 템플릿(placeholder 포함)을 반환합니다. .format(**data) 로 채워 사용하세요."""
    return get_prompt(agent_name, "user")
