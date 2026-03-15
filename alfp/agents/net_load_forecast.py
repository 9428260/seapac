"""
NetLoadForecastAgent - Net Load 계산
Net Load = Load - PV Generation
"""

import pandas as pd
from alfp.agents.state import ALFPState


def net_load_forecast_agent(state: ALFPState) -> ALFPState:
    """
    NetLoadForecastAgent 노드 함수.
    - Load 예측값과 PV 예측값을 병합
    - Net Load = predicted_load_kw - predicted_pv_kw
    - 피크 Net Load 식별
    """
    log = state.get("messages", [])
    errors = state.get("errors", [])
    log.append("[NetLoadForecastAgent] Net Load 계산 시작")

    try:
        load_df = state["load_forecast"][["timestamp", "load_kw", "predicted_load_kw"]].copy()
        pv_df = state["pv_forecast"][["timestamp", "pv_kw", "predicted_pv_kw"]].copy()

        net_df = pd.merge(load_df, pv_df, on="timestamp", how="inner")

        # Net Load 계산
        net_df["actual_net_load_kw"] = (net_df["load_kw"] - net_df["pv_kw"]).clip(lower=0)
        net_df["predicted_net_load_kw"] = (
            net_df["predicted_load_kw"] - net_df["predicted_pv_kw"]
        ).clip(lower=0)

        # 피크 Net Load 타임스탬프
        peak_idx = net_df["predicted_net_load_kw"].idxmax()
        peak_time = net_df.loc[peak_idx, "timestamp"]
        peak_val = net_df.loc[peak_idx, "predicted_net_load_kw"]

        log.append(f"  Net Load 레코드: {len(net_df):,}건")
        log.append(f"  예측 피크 Net Load: {peak_val:.2f} kW @ {peak_time}")
        log.append("[NetLoadForecastAgent] 완료")

    except Exception as e:
        errors.append(f"[NetLoadForecastAgent] 오류: {e}")
        raise

    return {
        **state,
        "net_load_forecast": net_df,
        "messages": log,
        "errors": errors,
    }
