"""
ValidationAgent - 예측 결과 정확도 검증 + LLM 해석
Metrics: MAE, RMSE, MAPE, Peak Error
LLM이 지표를 해석하고 개선 방향을 제시합니다.
"""

import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.output_parsers import JsonOutputParser

from alfp.agents.state import ALFPState
from alfp.config import get_skills_config, get_system_prompt, get_user_prompt_template
from alfp.llm import get_llm, is_llm_enabled
from alfp.skills.energy_forecast import EnergyForecastSkill

# ── 통계 계산 함수 ─────────────────────────────────────────────────
def _mae(y_true, y_pred): return float(np.mean(np.abs(y_true - y_pred)))
def _rmse(y_true, y_pred): return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
def _mape(y_true, y_pred, eps=1.0):
    mask = y_true > eps
    return float(np.mean(np.abs((y_true[mask] - y_pred[mask]) / y_true[mask])) * 100) if mask.sum() > 0 else float("nan")

def _peak_error(y_true, y_pred):
    tp, pp = float(y_true.max()), float(y_pred.max())
    return {"true_peak_kw": round(tp, 3), "pred_peak_kw": round(pp, 3),
            "peak_error_pct": round(abs(tp - pp) / (tp + 1e-6) * 100, 2)}

def _compute_metrics(actual, predicted, label):
    return {
        "label": label,
        "MAE": round(_mae(actual, predicted), 4),
        "RMSE": round(_rmse(actual, predicted), 4),
        "MAPE": round(_mape(actual, predicted), 2),
        "peak": _peak_error(actual, predicted),
        "n_samples": len(actual),
    }


def validation_agent(state: ALFPState) -> ALFPState:
    """
    ValidationAgent 노드 함수.
    수치 지표 계산 후 LLM이 해석·개선 방향 제시.
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[ValidationAgent] 예측 성능 검증 시작")

    metrics = {}

    try:
        # ── 수치 지표 계산 (_compute_metrics + EnergyForecastSkill.evaluate_forecast) ─
        load_df = state["load_forecast"]
        load_m = _compute_metrics(load_df["load_kw"].values, load_df["predicted_load_kw"].values, "Load")
        load_skill = EnergyForecastSkill.evaluate_forecast(load_df["load_kw"].values, load_df["predicted_load_kw"].values)
        load_m["skill_mape"] = load_skill["mape"]
        load_m["skill_rmse"] = load_skill["rmse"]
        metrics["load"] = load_m

        pv_df = state["pv_forecast"]
        pv_m = _compute_metrics(pv_df["pv_kw"].values, pv_df["predicted_pv_kw"].values, "PV")
        pv_skill = EnergyForecastSkill.evaluate_forecast(pv_df["pv_kw"].values, pv_df["predicted_pv_kw"].values)
        pv_m["skill_mape"] = pv_skill["mape"]
        pv_m["skill_rmse"] = pv_skill["rmse"]
        metrics["pv"] = pv_m

        nl_df = state["net_load_forecast"]
        nl_m = _compute_metrics(nl_df["actual_net_load_kw"].values, nl_df["predicted_net_load_kw"].values, "NetLoad")
        nl_skill = EnergyForecastSkill.evaluate_forecast(nl_df["actual_net_load_kw"].values, nl_df["predicted_net_load_kw"].values)
        nl_m["skill_mape"] = nl_skill["mape"]
        nl_m["skill_rmse"] = nl_skill["rmse"]
        metrics["net_load"] = nl_m

        # KPI (설정에서 목표값 로드)
        kpi_cfg = get_skills_config().get("validation", {}).get("kpi", {})
        mape_target = kpi_cfg.get("mape_target_pct", 10.0)
        peak_acc_target = kpi_cfg.get("peak_acc_target_pct", 90.0)
        load_peak_acc = 100 - load_m["peak"]["peak_error_pct"]
        kpi = {
            "MAPE_target": mape_target, "MAPE_achieved": load_m["MAPE"], "MAPE_pass": load_m["MAPE"] < mape_target,
            "peak_acc_target": peak_acc_target, "peak_acc_achieved": round(load_peak_acc, 2), "peak_acc_pass": load_peak_acc >= peak_acc_target,
        }
        metrics["kpi"] = kpi

        for key in ["load", "pv", "net_load"]:
            m = metrics[key]
            log.append(f"  [{m['label']}] MAE={m['MAE']:.3f} kW, RMSE={m['RMSE']:.3f} kW, "
                       f"MAPE={m['MAPE']:.2f}%, PeakErr={m['peak']['peak_error_pct']:.2f}%")
        log.append(f"  KPI: MAPE {'✓' if kpi['MAPE_pass'] else '✗'} ({kpi['MAPE_achieved']:.2f}%) / "
                   f"피크 {'✓' if kpi['peak_acc_pass'] else '✗'} ({kpi['peak_acc_achieved']:.2f}%)")

        # ── LLM 해석 ──────────────────────────────────────────────
        plan = state.get("forecast_plan", {})
        prompt_data = {
            "load_mae": load_m["MAE"], "load_rmse": load_m["RMSE"], "load_mape": load_m["MAPE"],
            "load_true_peak": load_m["peak"]["true_peak_kw"], "load_pred_peak": load_m["peak"]["pred_peak_kw"],
            "load_peak_err": load_m["peak"]["peak_error_pct"],
            "load_mape_kpi": "달성" if load_m["MAPE"] < 10 else "미달",
            "load_peak_kpi": "달성" if load_peak_acc >= 90 else "미달",
            "pv_mae": pv_m["MAE"], "pv_rmse": pv_m["RMSE"], "pv_mape": pv_m["MAPE"],
            "pv_true_peak": pv_m["peak"]["true_peak_kw"], "pv_pred_peak": pv_m["peak"]["pred_peak_kw"],
            "pv_peak_err": pv_m["peak"]["peak_error_pct"],
            "nl_mae": nl_m["MAE"], "nl_rmse": nl_m["RMSE"], "nl_mape": nl_m["MAPE"],
            "nl_true_peak": nl_m["peak"]["true_peak_kw"], "nl_pred_peak": nl_m["peak"]["pred_peak_kw"],
            "nl_peak_err": nl_m["peak"]["peak_error_pct"],
            "prosumer_type": plan.get("prosumer_type", "Unknown"),
            "selected_model": plan.get("selected_model", "Unknown"),
            "n_samples": load_m["n_samples"],
        }

        if is_llm_enabled("alfp_validation"):
            llm = get_llm(temperature=0.0, stage="alfp_validation")
            log.append("  GPT-4o 지표 해석 중...")
            system_prompt = get_system_prompt("validation")
            user_template = get_user_prompt_template("validation")
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_template.format(**prompt_data)),
            ])
            llm_analysis = JsonOutputParser().invoke(response.content)
            metrics["llm_analysis"] = llm_analysis
            log.append(f"  LLM 신뢰도 평가: {llm_analysis.get('confidence_level', 'N/A')}")
            log.append(f"  LLM 종합 평가: {llm_analysis.get('overall_assessment', '')}")
        else:
            log.append("  LLM 비활성화 상태 - 규칙 기반 수치 검증만 수행")

    except Exception as e:
        errors.append(f"[ValidationAgent] LLM 오류: {e}")

    log.append("[ValidationAgent] 완료")

    return {**state, "validation_metrics": metrics, "messages": log, "errors": errors}
