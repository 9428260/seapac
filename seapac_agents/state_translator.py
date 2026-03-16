"""
Step 2 — State Translator (PRD: seapac_agentic_prd.md)

Mesa 시뮬레이션 상태를 LLM 에이전트용 구조화 JSON으로 변환합니다.

Responsibilities:
  - Mesa DataCollector 데이터 추출
  - 고차원 데이터 압축
  - LLM 친화적 JSON 생성
  - 사람이 읽을 수 있는 요약 텍스트 생성
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from simulation.model import ALFPSimulationModel


# PRD 예시 출력과 동일한 스키마
# {
#   "time": "18:00",
#   "community_state": { total_load, pv_generation, surplus_energy, deficit_energy, peak_risk },
#   "market_state": { grid_price, community_trade_price_range },
#   "ess_state": { soc, capacity, available_discharge }
# }

_PEAK_RISK_THRESHOLDS = {
    "LOW":    0.70,   # peak_load * 0.70 미만
    "MEDIUM": 0.85,   # peak_load * 0.85 미만
    "HIGH":   1.00,   # 이상
}


def _peak_risk_label(current_load: float, peak_threshold: float) -> str:
    ratio = current_load / peak_threshold if peak_threshold > 0 else 0.0
    if ratio < _PEAK_RISK_THRESHOLDS["LOW"]:
        return "LOW"
    if ratio < _PEAK_RISK_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "HIGH"


# ─────────────────────────────────────────────────────────────────
# 메인 공개 함수
# ─────────────────────────────────────────────────────────────────

def translate_model_state(
    model: "ALFPSimulationModel",
    peak_threshold_kw: float = 500.0,
    p2p_price_range: tuple[float, float] = (80.0, 110.0),
) -> dict:
    """
    현재 Mesa 모델 상태를 LLM 에이전트용 JSON dict로 변환.

    Args:
        model: 현재 실행 중인 ALFPSimulationModel 인스턴스
        peak_threshold_kw: 피크 리스크 판단 기준값 (kW)
        p2p_price_range: 커뮤니티 P2P 거래 가격 범위 [min, max] (원/kWh)

    Returns:
        PRD 스펙 JSON dict
    """
    from simulation.agents.prosumer import ProsumerAgent
    from simulation.agents.ess import ESSAgent
    from simulation.agents.market import EnergyMarketAgent

    step = model.current_step
    hour = model.current_hour
    time_str = f"{hour:02d}:{(step % 4) * 15:02d}"

    # ── Community State ─────────────────────────────────────────
    prosumers = list(model.agents_by_type.get(ProsumerAgent) or [])
    total_load = round(sum(a.current_load_kw for a in prosumers), 1)
    pv_generation = round(sum(a.current_pv_kw for a in prosumers), 1)
    net = total_load - pv_generation
    surplus_energy = round(max(-net, 0.0), 1)
    deficit_energy = round(max(net, 0.0), 1)
    peak_risk = _peak_risk_label(total_load, peak_threshold_kw)

    prosumer_states = []
    for a in prosumers:
        surplus = round(max(a.current_pv_kw - a.current_load_kw, 0.0), 2)
        deficit = round(max(a.current_load_kw - a.current_pv_kw, 0.0), 2)
        prosumer_states.append({
            "prosumer_id": a.prosumer_id,
            "prosumer_type": a.prosumer_type,
            "load_kw": round(a.current_load_kw, 2),
            "pv_kw": round(a.current_pv_kw, 2),
            "surplus_energy": surplus,
            "deficit_energy": deficit,
            "price_buy": round(a.current_price_buy, 2),
            "price_sell": round(a.current_price_sell, 2),
            "price_p2p": round(a.current_price_p2p, 2),
        })

    community_state = {
        "total_load": total_load,
        "pv_generation": pv_generation,
        "surplus_energy": surplus_energy,
        "deficit_energy": deficit_energy,
        "peak_risk": peak_risk,
    }

    # ── Market State ────────────────────────────────────────────
    avg_price = (
        round(sum(a.current_price_buy for a in prosumers) / len(prosumers), 1)
        if prosumers else 100.0
    )
    market_state = {
        "grid_price": avg_price,
        "community_trade_price_range": list(p2p_price_range),
    }

    # ── ESS State ───────────────────────────────────────────────
    ess_agents = list(model.agents_by_type.get(ESSAgent) or [])
    if ess_agents:
        e = ess_agents[0]
        soc_pct = round(e.soc * 100, 1)
        capacity = round(e.capacity_kwh, 1)
        available_discharge = round(
            (e.energy_kwh - e.soc_min * e.capacity_kwh) * e.efficiency, 1
        )
        ess_state = {
            "soc": soc_pct,
            "capacity": capacity,
            "available_discharge": available_discharge,
        }
    else:
        ess_state = {
            "soc": None,
            "capacity": None,
            "available_discharge": None,
        }

    return {
        "time": time_str,
        "step": step,
        "community_state": community_state,
        "market_state": market_state,
        "ess_state": ess_state,
        "prosumer_states": prosumer_states,
    }


def translate_dataframe(
    df: pd.DataFrame,
    peak_threshold_kw: float = 500.0,
    p2p_price_range: tuple[float, float] = (80.0, 110.0),
    ess_capacity_kwh: float = 200.0,
    ess_soc_min: float = 0.10,
) -> list[dict]:
    """
    시뮬레이션 결과 DataFrame(DataCollector 출력)을 스텝별 JSON 리스트로 변환.

    Mesa 모델 없이 사후(post-hoc) 변환이 필요할 때 사용합니다.

    Args:
        df: ALFPSimulationModel.run() 반환 DataFrame
        peak_threshold_kw: 피크 리스크 기준
        p2p_price_range: P2P 가격 범위
        ess_capacity_kwh: ESS 총 용량
        ess_soc_min: ESS 최소 SoC

    Returns:
        스텝별 state JSON 리스트
    """
    results = []
    for _, row in df.iterrows():
        step = int(row.get("step", 0))
        hour = int(row.get("hour", 0))
        time_str = f"{hour:02d}:{(step % 4) * 15:02d}"

        total_load = float(row.get("community_load_kw", 0.0))
        pv_generation = float(row.get("community_pv_kw", 0.0))
        net = total_load - pv_generation
        surplus_energy = round(max(-net, 0.0), 1)
        deficit_energy = round(max(net, 0.0), 1)
        peak_risk = _peak_risk_label(total_load, peak_threshold_kw)

        # ESS 상태 (NaN이면 None)
        soc_raw = row.get("ess_soc_pct")
        if pd.isna(soc_raw) if not isinstance(soc_raw, str) else False:
            ess_state = {"soc": None, "capacity": None, "available_discharge": None}
        else:
            soc_pct = float(soc_raw) if soc_raw is not None else None
            if soc_pct is not None:
                soc_frac = soc_pct / 100.0
                energy_kwh = soc_frac * ess_capacity_kwh
                avail = round((energy_kwh - ess_soc_min * ess_capacity_kwh) * 0.95, 1)
                ess_state = {
                    "soc": round(soc_pct, 1),
                    "capacity": ess_capacity_kwh,
                    "available_discharge": max(avail, 0.0),
                }
            else:
                ess_state = {"soc": None, "capacity": None, "available_discharge": None}

        results.append({
            "time": time_str,
            "step": step,
            "community_state": {
                "total_load": round(total_load, 1),
                "pv_generation": round(pv_generation, 1),
                "surplus_energy": surplus_energy,
                "deficit_energy": deficit_energy,
                "peak_risk": peak_risk,
            },
            "market_state": {
                "grid_price": None,   # DataFrame에 가격 정보 없을 때 None
                "community_trade_price_range": list(p2p_price_range),
            },
            "ess_state": ess_state,
            "prosumer_states": [],
        })
    return results


def translate_model_history(
    model: "ALFPSimulationModel",
    peak_threshold_kw: float = 500.0,
    p2p_price_range: tuple[float, float] = (80.0, 110.0),
) -> list[dict]:
    """
    DataCollector의 model/agent history를 사용해 step별 prosumer 상태를 포함한 JSON을 생성한다.
    """
    model_df = model.datacollector.get_model_vars_dataframe().reset_index(drop=True)
    agent_df = model.datacollector.get_agent_vars_dataframe().reset_index()
    from simulation.agents.prosumer import ProsumerAgent
    prosumer_agents = {
        a.prosumer_id: a for a in (model.agents_by_type.get(ProsumerAgent) or [])
    }

    step_col = "Step" if "Step" in agent_df.columns else ("step" if "step" in agent_df.columns else None)
    if step_col is None:
        return translate_dataframe(
            model_df.assign(step=range(len(model_df))),
            peak_threshold_kw=peak_threshold_kw,
            p2p_price_range=p2p_price_range,
        )

    results = []
    for step_idx, row in model_df.reset_index(drop=True).iterrows():
        total_load = float(row.get("community_load_kw", 0.0))
        pv_generation = float(row.get("community_pv_kw", 0.0))
        net = total_load - pv_generation
        prosumer_rows = agent_df[agent_df[step_col] == step_idx].copy()
        prosumer_states = []
        for _, arow in prosumer_rows.iterrows():
            pid = arow.get("prosumer_id")
            if pid is None:
                continue
            load_kw = float(arow.get("load_kw") or 0.0)
            pv_kw = float(arow.get("pv_kw") or 0.0)
            agent = prosumer_agents.get(pid)
            price_buy = None
            price_sell = None
            price_p2p = None
            if agent is not None and step_idx < len(agent.timeseries):
                ts_row = agent.timeseries.iloc[step_idx]
                price_buy = None if pd.isna(ts_row.get("price_buy")) else float(ts_row.get("price_buy"))
                price_sell = None if pd.isna(ts_row.get("price_sell")) else float(ts_row.get("price_sell"))
                price_p2p = None if pd.isna(ts_row.get("price_p2p")) else float(ts_row.get("price_p2p"))
            prosumer_states.append({
                "prosumer_id": pid,
                "prosumer_type": arow.get("type", "Unknown"),
                "load_kw": round(load_kw, 2),
                "pv_kw": round(pv_kw, 2),
                "surplus_energy": round(max(pv_kw - load_kw, 0.0), 2),
                "deficit_energy": round(max(load_kw - pv_kw, 0.0), 2),
                "price_buy": price_buy,
                "price_sell": price_sell,
                "price_p2p": price_p2p,
            })

        results.append({
            "time": f"{int(row.get('hour', 0)):02d}:{(step_idx % 4) * 15:02d}",
            "step": int(step_idx),
            "community_state": {
                "total_load": round(total_load, 1),
                "pv_generation": round(pv_generation, 1),
                "surplus_energy": round(max(-net, 0.0), 1),
                "deficit_energy": round(max(net, 0.0), 1),
                "peak_risk": _peak_risk_label(total_load, peak_threshold_kw),
            },
            "market_state": {
                "grid_price": None,
                "community_trade_price_range": list(p2p_price_range),
            },
            "ess_state": {
                "soc": None if pd.isna(row.get("ess_soc_pct")) else float(row.get("ess_soc_pct")),
                "capacity": None,
                "available_discharge": None,
            },
            "prosumer_states": prosumer_states,
        })
    return results


def generate_summary(state_json: dict) -> str:
    """
    단일 state JSON에서 LLM 프롬프트용 자연어 요약 생성.

    Args:
        state_json: translate_model_state() 반환값

    Returns:
        사람이 읽을 수 있는 요약 문자열
    """
    t = state_json.get("time", "?")
    cs = state_json.get("community_state", {})
    ms = state_json.get("market_state", {})
    es = state_json.get("ess_state", {})

    load = cs.get("total_load", 0)
    pv = cs.get("pv_generation", 0)
    surplus = cs.get("surplus_energy", 0)
    deficit = cs.get("deficit_energy", 0)
    risk = cs.get("peak_risk", "UNKNOWN")

    grid_price = ms.get("grid_price")
    price_range = ms.get("community_trade_price_range", ["-", "-"])

    soc = es.get("soc")
    capacity = es.get("capacity")
    avail = es.get("available_discharge")

    lines = [
        f"[{t}] 커뮤니티 에너지 상태 요약",
        f"  부하: {load} kW, PV 발전: {pv} kW",
        f"  잉여: {surplus} kW, 부족: {deficit} kW, 피크 위험: {risk}",
    ]
    if grid_price is not None:
        lines.append(f"  계통 가격: {grid_price} 원/kWh, P2P 거래 범위: {price_range[0]}~{price_range[1]} 원/kWh")
    if soc is not None:
        lines.append(f"  ESS SoC: {soc}% (용량 {capacity} kWh, 방전 가용량 {avail} kWh)")
    else:
        lines.append("  ESS: 미설치")
    prosumers = state_json.get("prosumer_states") or []
    if prosumers:
        sellers = sum(1 for p in prosumers if float(p.get("surplus_energy", 0)) > 0)
        buyers = sum(1 for p in prosumers if float(p.get("deficit_energy", 0)) > 0)
        lines.append(f"  Prosumer 상태: seller {sellers}명 / buyer {buyers}명")

    return "\n".join(lines)


def translate_and_summarize(
    model: "ALFPSimulationModel",
    peak_threshold_kw: float = 500.0,
    p2p_price_range: tuple[float, float] = (80.0, 110.0),
) -> tuple[dict, str]:
    """
    translate_model_state + generate_summary 를 한 번에 수행.

    Returns:
        (state_json, summary_text)
    """
    state_json = translate_model_state(model, peak_threshold_kw, p2p_price_range)
    summary = generate_summary(state_json)
    return state_json, summary
