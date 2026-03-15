"""
ALFPSimulationModel - Mesa 기반 멀티 에이전트 시뮬레이션 메인 모델

4단계 시뮬레이션:
  Phase 1 - 단일 부하 예측  (naive moving average)
  Phase 2 - Agentic forecast pipeline  (경량 파이프라인 에이전트)
  Phase 3 - ESS 연동  (TOU 기반 충방전 최적화)
  Phase 4 - 에너지 거래 연동  (P2P 그리디 매칭)

ALFP decisions 연동:
  alfp_decisions를 넘기면 ESS 스케줄·거래 추천·DR 이벤트를 스텝별로 적용합니다.
  미제공 시 기존 경량 규칙 기반으로 동작합니다.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Literal

import mesa
import numpy as np
import pandas as pd
from mesa.datacollection import DataCollector

from simulation.agents.prosumer import ProsumerAgent
from simulation.agents.ess import ESSAgent
from simulation.agents.market import EnergyMarketAgent

Phase = Literal[1, 2, 3, 4]


# ─────────────────────────────────────────────────────────────────
# ALFP decisions → 스텝별 lookup 빌드
# ─────────────────────────────────────────────────────────────────

def _build_decisions_lookups(
    decisions: dict,
    step_timestamps: list[pd.Timestamp],
) -> tuple[dict[int, dict], dict[int, list], dict[int, dict]]:
    """
    ALFP decisions를 시뮬레이션 스텝 인덱스별 lookup으로 변환.

    Returns:
        ess_by_step: step -> {action, power_kw, soc_kwh, net_load_kw}
        trading_by_step: step -> [ {surplus_kw, action}, ... ]
        dr_by_step: step -> {net_load_kw, recommended_reduction_kw, action}
    """
    ess_by_step: dict[int, dict] = {}
    trading_by_step: dict[int, list] = {}
    dr_by_step: dict[int, dict] = {}

    def ts_norm(t) -> pd.Timestamp:
        if t is None:
            return pd.NaT
        ts = pd.Timestamp(t)
        # 타임존 통일: naive로 비교 (ALFP/시뮬 데이터 혼용 시 tz 충돌 방지)
        if getattr(ts, "tz", None) is not None:
            try:
                ts = pd.Timestamp(ts.to_pydatetime().replace(tzinfo=None))
            except (TypeError, ValueError):
                pass
        return ts

    # 스텝 i의 timestamp와 가장 가까운 시간 매칭용 (15분 단위 동일 가정)
    step_ts_set = {i: ts_norm(t) for i, t in enumerate(step_timestamps) if i < len(step_timestamps)}

    # ESS 스케줄: 스텝 인덱스로 매핑 (동일 horizon이면 1:1 대응)
    ess_schedule = decisions.get("ess_schedule") or []
    n_steps = len(step_ts_set)
    for step in range(min(len(ess_schedule), n_steps)):
        item = ess_schedule[step]
        ess_by_step[step] = {
            "action": item.get("action", "idle"),
            "power_kw": float(item.get("power_kw", 0.0)),
            "soc_kwh": float(item.get("soc_kwh", 0.0)),
            "net_load_kw": float(item.get("net_load_kw", 0.0)),
        }

    # trading_recommendations
    for item in decisions.get("trading_recommendations") or []:
        t = ts_norm(item.get("timestamp"))
        if pd.isna(t):
            continue
        for step, st in step_ts_set.items():
            if pd.isna(st):
                continue
            diff_sec = abs((t - st).total_seconds()) if t != st else 0
            if t != st and diff_sec >= 60:
                continue
            trading_by_step.setdefault(step, []).append({
                "surplus_kw": float(item.get("surplus_kw", 0.0)),
                "action": item.get("action", "sell_p2p"),
            })
            break

    # demand_response_events
    for item in decisions.get("demand_response_events") or []:
        t = ts_norm(item.get("timestamp"))
        if pd.isna(t):
            continue
        for step, st in step_ts_set.items():
            if pd.isna(st):
                continue
            diff_sec = abs((t - st).total_seconds()) if t != st else 0
            if t != st and diff_sec >= 60:
                continue
            dr_by_step[step] = {
                "net_load_kw": float(item.get("net_load_kw", 0.0)),
                "recommended_reduction_kw": float(item.get("recommended_reduction_kw", 0.0)),
                "action": item.get("action", "demand_response"),
            }
            break

    return ess_by_step, trading_by_step, dr_by_step


# ─────────────────────────────────────────────────────────────────
# DataCollector 헬퍼
# ─────────────────────────────────────────────────────────────────

def _community_load(model: "ALFPSimulationModel") -> float:
    """전체 프로슈머 실제 부하 합계 (kW)."""
    return sum(a.current_load_kw for a in (model.agents_by_type.get(ProsumerAgent) or []))


def _community_pv(model: "ALFPSimulationModel") -> float:
    """전체 프로슈머 실제 PV 발전량 합계 (kW)."""
    return sum(a.current_pv_kw for a in (model.agents_by_type.get(ProsumerAgent) or []))


def _community_net_load(model: "ALFPSimulationModel") -> float:
    return _community_load(model) - _community_pv(model)


def _avg_forecast_mape(model: "ALFPSimulationModel") -> float:
    """평균 부하 예측 MAPE (%)."""
    prosumers = list(model.agents_by_type.get(ProsumerAgent) or [])
    if not prosumers:
        return 0.0
    mapes = [a.forecast_mape for a in prosumers if a.forecast_mape > 0]
    return float(np.mean(mapes)) if mapes else 0.0


def _ess_soc(model: "ALFPSimulationModel") -> float:
    """ESS SoC (%). Phase < 3이면 NaN."""
    ess_agents = list(model.agents_by_type.get(ESSAgent) or [])
    return ess_agents[0].soc_pct if ess_agents else float("nan")


def _ess_power(model: "ALFPSimulationModel") -> float:
    """ESS 충방전 전력 (kW, +충전 / -방전). Phase < 3이면 NaN."""
    ess_agents = list(model.agents_by_type.get(ESSAgent) or [])
    if not ess_agents:
        return float("nan")
    e = ess_agents[0]
    return e.power_kw if e.action == "charge" else -e.power_kw


def _market_matched_kw(model: "ALFPSimulationModel") -> float:
    """이번 스텝 P2P 거래 매칭량 (kW). Phase < 4이면 NaN."""
    mkt = list(model.agents_by_type.get(EnergyMarketAgent) or [])
    return mkt[0].matched_kw_this_step if mkt else float("nan")


def _market_trades(model: "ALFPSimulationModel") -> int:
    """이번 스텝 거래 건수. Phase < 4이면 NaN."""
    mkt = list(model.agents_by_type.get(EnergyMarketAgent) or [])
    return len(mkt[0].trades_this_step) if mkt else 0


def _community_saving_krw(model: "ALFPSimulationModel") -> float:
    """이번 스텝 커뮤니티 절감액 누적 합산 (원)."""
    prosumers = list(model.agents_by_type.get(ProsumerAgent) or [])
    return sum(a.cumulative_saving for a in prosumers)


# ─────────────────────────────────────────────────────────────────
# 메인 모델
# ─────────────────────────────────────────────────────────────────

class ALFPSimulationModel(mesa.Model):
    """
    ALFP 멀티 에이전트 시뮬레이션 모델.

    Args:
        phase: 활성화할 시뮬레이션 단계 (1~4)
        data_path: 학습 데이터 pkl 경로
        n_steps: 시뮬레이션 스텝 수 (15분 단위, 기본 96 = 24시간)
        prosumer_ids: 시뮬레이션할 프로슈머 ID 목록 (None이면 전체)
        seed: 난수 시드
        ess_capacity_kwh: ESS 용량 (Phase 3+)
        ess_peak_threshold_kw: ESS 피크 억제 임계값 (Phase 3+)
        alfp_decisions: ALFP DecisionAgent 출력(decisions). 제공 시 ESS/거래/DR을 스텝별로 적용.
    """

    def __init__(
        self,
        phase: Phase = 1,
        data_path: str = "data/train_2026_seoul.pkl",
        n_steps: int = 96,
        prosumer_ids: list[str] | None = None,
        seed: int = 42,
        ess_capacity_kwh: float = 200.0,
        ess_peak_threshold_kw: float = 500.0,
        alfp_decisions: dict | None = None,
    ):
        self.rng = np.random.default_rng(seed)
        super().__init__(rng=self.rng)
        self.phase = phase
        self.n_steps = n_steps
        self.current_step: int = 0
        self.alfp_decisions = alfp_decisions

        # ── 데이터 로드 ───────────────────────────────────────
        data = self._load_data(data_path)
        ts: pd.DataFrame = data["timeseries"]

        if prosumer_ids is None:
            prosumer_ids = ts["prosumer_id"].unique().tolist()

        # ── 에이전트 등록 ─────────────────────────────────────

        # 1) ProsumerAgent (Phase 1 ~ 4 모두 활성)
        for pid in prosumer_ids:
            pdata = ts[ts["prosumer_id"] == pid].copy()
            pdata = pdata.sort_values("timestamp").head(n_steps).reset_index(drop=True)
            ptype = pdata["prosumer_type"].iloc[0] if "prosumer_type" in pdata.columns else "Unknown"
            ProsumerAgent(
                model=self,
                prosumer_id=pid,
                prosumer_type=ptype,
                timeseries=pdata,
                phase=phase,
            )

        # ── ALFP decisions → 스텝별 lookup (Prosumer 생성 후 timestamp 수집)
        self._ess_schedule_by_step = {}
        self._trading_by_step = {}
        self._dr_by_step = {}
        if alfp_decisions:
            prosumers = list(self.agents_by_type.get(ProsumerAgent) or [])
            if prosumers:
                step_ts = [
                    pd.Timestamp(prosumers[0].timeseries.iloc[i]["timestamp"])
                    for i in range(min(n_steps, len(prosumers[0].timeseries)))
                ]
                self._ess_schedule_by_step, self._trading_by_step, self._dr_by_step = _build_decisions_lookups(
                    alfp_decisions, step_ts
                )

        # 2) ESSAgent (Phase 3 ~ 4)
        if phase >= 3:
            ESSAgent(
                model=self,
                capacity_kwh=ess_capacity_kwh,
                peak_load_threshold_kw=ess_peak_threshold_kw,
            )

        # 3) EnergyMarketAgent (Phase 4)
        if phase >= 4:
            EnergyMarketAgent(model=self)

        # ── DataCollector 설정 ────────────────────────────────
        self.datacollector = DataCollector(
            model_reporters={
                "step":               lambda m: m.current_step,
                "hour":               lambda m: m.current_hour,
                "community_load_kw":  _community_load,
                "community_pv_kw":    _community_pv,
                "community_net_kw":   _community_net_load,
                "avg_forecast_mape":  _avg_forecast_mape,
                "ess_soc_pct":        _ess_soc,
                "ess_power_kw":       _ess_power,
                "market_matched_kw":  _market_matched_kw,
                "market_trade_count": _market_trades,
                "cumulative_saving_krw": _community_saving_krw,
            },
            agent_reporters={
                "prosumer_id":      lambda a: getattr(a, "prosumer_id", None),
                "type":             lambda a: getattr(a, "prosumer_type", type(a).__name__),
                "load_kw":          lambda a: getattr(a, "current_load_kw", None),
                "pv_kw":            lambda a: getattr(a, "current_pv_kw", None),
                "pred_load_kw":     lambda a: getattr(a, "predicted_load_kw", None),
                "surplus_kw":       lambda a: getattr(a, "surplus_kw", None),
                "forecast_mape":    lambda a: getattr(a, "forecast_mape", None),
                "soc_pct":          lambda a: getattr(a, "soc_pct", None),
            },
        )

    # ─────────────────────────────────────────────────────────────
    # Mesa step()
    # ─────────────────────────────────────────────────────────────
    def step(self) -> None:
        """
        시뮬레이션 1 tick (15분) 진행.

        실행 순서:
          1) ProsumerAgent.step() - 관측 + 예측
          2) ESSAgent.step()      - 충방전 결정 (Phase 3+)
          3) EnergyMarketAgent.step() - P2P 거래 매칭 (Phase 4)
          4) DataCollector 수집
          5) current_step 증가
        """
        # ProsumerAgent 먼저 (예측값 필요)
        pset = self.agents_by_type.get(ProsumerAgent)
        if pset:
            pset.do("step")

        # ESS (Phase 3+)
        eset = self.agents_by_type.get(ESSAgent)
        if eset:
            eset.do("step")

        # 에너지 시장 (Phase 4)
        mset = self.agents_by_type.get(EnergyMarketAgent)
        if mset:
            mset.do("step")

        self.datacollector.collect(self)
        self.current_step += 1

    def run(self, n_steps: int | None = None) -> pd.DataFrame:
        """
        시뮬레이션 전체 실행 후 DataFrame 반환.

        Args:
            n_steps: 실행 스텝 수. None이면 self.n_steps 사용.

        Returns:
            모델 레벨 수집 데이터 (pd.DataFrame)
        """
        steps = n_steps or self.n_steps
        for _ in range(steps):
            if self.current_step >= self._max_steps():
                break
            self.step()
        return self.datacollector.get_model_vars_dataframe()

    # ─────────────────────────────────────────────────────────────
    # 공개 속성
    # ─────────────────────────────────────────────────────────────
    @property
    def current_hour(self) -> int:
        """현재 스텝의 시(hour). ProsumerAgent의 timestamp에서 계산."""
        prosumers = list(self.agents_by_type.get(ProsumerAgent) or [])
        if prosumers:
            step = min(self.current_step, len(prosumers[0].timeseries) - 1)
            ts = prosumers[0].timeseries.iloc[step]["timestamp"]
            return pd.Timestamp(ts).hour
        return (self.current_step // 4) % 24

    def summary(self) -> dict:
        """시뮬레이션 종료 후 요약 통계 반환."""
        df = self.datacollector.get_model_vars_dataframe()
        prosumers = list(self.agents_by_type.get(ProsumerAgent) or [])
        ess_agents = list(self.agents_by_type.get(ESSAgent) or [])
        mkt_agents = list(self.agents_by_type.get(EnergyMarketAgent) or [])

        result: dict = {
            "phase": self.phase,
            "n_prosumers": len(prosumers),
            "n_steps_run": self.current_step,
            "avg_community_load_kw": round(df["community_load_kw"].mean(), 2),
            "avg_community_pv_kw": round(df["community_pv_kw"].mean(), 2),
            "peak_load_kw": round(df["community_load_kw"].max(), 2),
            "avg_forecast_mape_pct": round(df["avg_forecast_mape"].mean(), 2),
        }

        if self.phase >= 3 and ess_agents:
            e = ess_agents[0]
            result.update({
                "ess_total_charged_kwh":    round(e.total_charged_kwh, 2),
                "ess_total_discharged_kwh": round(e.total_discharged_kwh, 2),
                "ess_peak_shaving_count":   e.peak_shaving_count,
                "ess_utilization_rate":     round(e.utilization_rate, 3),
                "ess_saving_krw":           round(e.cumulative_saving_krw, 0),
                "final_soc_pct":            e.soc_pct,
            })

        if self.phase >= 4 and mkt_agents:
            mkt = mkt_agents[0]
            result.update({
                "total_trades":           mkt.total_trades,
                "total_matched_kwh":      round(mkt.total_matched_kwh, 2),
                "market_revenue_krw":     round(mkt.total_revenue_krw, 0),
                "seller_revenue_krw":     round(mkt.total_seller_revenue_krw, 0),
                "buyer_saving_krw":       round(mkt.total_buyer_saving_krw, 0),
                "community_saving_krw":   round(mkt.total_community_saving_krw, 0),
            })

        return result

    # ─────────────────────────────────────────────────────────────
    # 내부 유틸리티
    # ─────────────────────────────────────────────────────────────
    @staticmethod
    def _load_data(data_path: str) -> dict:
        path = Path(data_path)
        if not path.exists():
            raise FileNotFoundError(f"데이터 파일을 찾을 수 없습니다: {data_path}")
        with open(path, "rb") as f:
            return pickle.load(f)

    def _max_steps(self) -> int:
        prosumers = list(self.agents_by_type.get(ProsumerAgent) or [])
        return prosumers[0].total_steps if prosumers else self.n_steps
