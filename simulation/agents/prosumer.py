"""
ProsumerAgent - Mesa 에이전트

Phase 1: 단순 이동평균 기반 부하 예측 (naive forecast)
Phase 2: 파이프라인 기반 예측 (데이터 품질 → 피처 → 예측 → 검증 → 결정)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import mesa


class ProsumerAgent(mesa.Agent):
    """
    공동주택 단지 내 단일 프로슈머(가구/건물)를 모델링하는 에이전트.

    Attributes:
        prosumer_id: 데이터셋 기준 고유 식별자 (예: 'bus_48_Commercial')
        prosumer_type: 타입 ('Commercial', 'Residential', 'Industrial', 'Rural', 'EnergyHub')
        timeseries: 해당 프로슈머의 전체 시계열 데이터 (pandas DataFrame)
        phase: 현재 시뮬레이션 단계 (1~4)
        window: 이동평균 윈도우 크기 (Phase 1 naive forecast용)
    """

    # Phase 2 파이프라인 단계 이름
    _PIPELINE_STAGES = [
        "data_quality",
        "feature_engineering",
        "forecast",
        "validation",
        "decision",
    ]

    def __init__(
        self,
        model: mesa.Model,
        prosumer_id: str,
        prosumer_type: str,
        timeseries: pd.DataFrame,
        phase: int = 1,
        window: int = 96,
    ):
        super().__init__(model)
        self.prosumer_id = prosumer_id
        self.prosumer_type = prosumer_type
        self.timeseries = timeseries.reset_index(drop=True)
        self.phase = phase
        self.window = window  # 이동평균 윈도우 (96 스텝 = 24시간)

        # ── 현재 스텝 상태 ──────────────────────────────────
        self.current_step: int = 0
        self.current_load_kw: float = 0.0
        self.current_pv_kw: float = 0.0
        self.current_price_buy: float = 0.0
        self.current_price_sell: float = 0.0
        self.current_price_p2p: float = 0.0

        # ── 예측 결과 ────────────────────────────────────────
        self.predicted_load_kw: float = 0.0
        self.predicted_pv_kw: float = 0.0
        self.forecast_error_kw: float = 0.0  # 부하 예측 오차
        self.forecast_mape: float = 0.0

        # ── Phase 2 파이프라인 상태 ─────────────────────────
        self.pipeline_stage: str = "idle"
        self.data_quality_score: float = 1.0   # 0~1 (1 = 완벽)
        self.anomaly_detected: bool = False
        self.feature_count: int = 0
        self.validation_passed: bool = True
        self.decision_action: str = "none"     # 'charge', 'discharge', 'sell', 'none'

        # ── 에너지 균형 ─────────────────────────────────────
        self.net_load_kw: float = 0.0          # load - pv
        self.surplus_kw: float = 0.0           # max(pv - load, 0)
        self.deficit_kw: float = 0.0           # max(load - pv, 0)

        # ── 거래 (Phase 4) ──────────────────────────────────
        self.energy_sold_kw: float = 0.0
        self.energy_bought_kw: float = 0.0
        self.trading_revenue: float = 0.0      # 원 (KRW)
        self.cumulative_saving: float = 0.0    # 누적 요금 절감액 (KRW)

        # ── 이력 (이동평균 계산용) ───────────────────────────
        self._load_history: list[float] = []
        self._pv_history: list[float] = []

    # ──────────────────────────────────────────────────────────────
    # Mesa step() — 각 시뮬레이션 tick마다 호출
    # ──────────────────────────────────────────────────────────────
    def step(self) -> None:
        step = self.model.current_step
        n = len(self.timeseries)
        if step >= n:
            return

        # 1) 실제 관측값 읽기
        row = self.timeseries.iloc[step]
        self.current_load_kw    = float(row["load_kw"])
        self.current_pv_kw      = float(row.get("pv_kw", 0.0))
        self.current_price_buy  = float(row.get("price_buy", 100.0))
        self.current_price_sell = float(row.get("price_sell", 60.0))
        self.current_price_p2p  = float(row.get("price_p2p", 80.0))

        self._load_history.append(self.current_load_kw)
        self._pv_history.append(self.current_pv_kw)

        # 2) Phase별 예측 수행
        if self.phase == 1:
            self._phase1_forecast()
        else:
            self._phase2_pipeline()

        # 3) 에너지 균형 계산
        self.net_load_kw = self.current_load_kw - self.current_pv_kw
        self.surplus_kw  = max(self.current_pv_kw - self.current_load_kw, 0.0)
        self.deficit_kw  = max(self.current_load_kw - self.current_pv_kw, 0.0)

        # 4) 예측 오차
        actual = self.current_load_kw
        pred   = self.predicted_load_kw
        self.forecast_error_kw = abs(pred - actual)
        if actual > 0.01:
            self.forecast_mape = abs(pred - actual) / actual * 100.0

    # ──────────────────────────────────────────────────────────────
    # Phase 1 - 단일 부하 예측 (naive moving average)
    # ──────────────────────────────────────────────────────────────
    def _phase1_forecast(self) -> None:
        """
        Naive 예측: 직전 window 스텝의 이동평균.
        데이터가 부족하면 마지막 관측값으로 대체.
        """
        if len(self._load_history) >= self.window:
            self.predicted_load_kw = float(
                np.mean(self._load_history[-self.window:])
            )
        elif self._load_history:
            self.predicted_load_kw = self._load_history[-1]
        else:
            self.predicted_load_kw = self.current_load_kw

        # PV도 동일한 naive 예측
        if len(self._pv_history) >= self.window:
            self.predicted_pv_kw = float(
                np.mean(self._pv_history[-self.window:])
            )
        elif self._pv_history:
            self.predicted_pv_kw = self._pv_history[-1]
        else:
            self.predicted_pv_kw = self.current_pv_kw

    # ──────────────────────────────────────────────────────────────
    # Phase 2 - Agentic forecast pipeline 시뮬레이션
    # ──────────────────────────────────────────────────────────────
    def _phase2_pipeline(self) -> None:
        """
        에이전트 파이프라인을 순차 실행하여 예측 및 의사결정 수행.
        실제 ALFP 파이프라인의 핵심 로직을 경량 버전으로 구현.
        """
        step = self.model.current_step

        # ── Stage 1: DataQualityAgent ─────────────────────
        self.pipeline_stage = "data_quality"
        self.anomaly_detected = self._check_anomaly()
        self.data_quality_score = 0.7 if self.anomaly_detected else 1.0
        if self.anomaly_detected:
            # 이상치 대체: 이전값 또는 이동평균
            self.current_load_kw = (
                self._load_history[-2] if len(self._load_history) >= 2
                else self.current_load_kw
            )

        # ── Stage 2: FeatureEngineeringAgent ─────────────
        self.pipeline_stage = "feature_engineering"
        features = self._build_features(step)
        self.feature_count = len(features)

        # ── Stage 3: ForecastAgent (LightGBM 대리 모델) ──
        self.pipeline_stage = "forecast"
        self.predicted_load_kw = self._surrogate_lgbm_forecast(features)
        self.predicted_pv_kw   = self._surrogate_pv_forecast(features)

        # ── Stage 4: ValidationAgent ──────────────────────
        self.pipeline_stage = "validation"
        if len(self._load_history) > 1:
            actual_prev = self._load_history[-2]
            pred_prev   = getattr(self, "_prev_predicted_load", actual_prev)
            mape = abs(pred_prev - actual_prev) / (actual_prev + 1e-6) * 100
            self.validation_passed = mape < 15.0
            self.forecast_mape = mape
        self._prev_predicted_load = self.predicted_load_kw

        # ── Stage 5: DecisionAgent ────────────────────────
        self.pipeline_stage = "decision"
        self.decision_action = self._decision_from_alfp_or_rules(step)

    def _check_anomaly(self) -> bool:
        """3σ 규칙으로 이상치 탐지."""
        if len(self._load_history) < 10:
            return False
        hist = np.array(self._load_history[-48:])  # 최근 12시간
        mean, std = hist.mean(), hist.std()
        return abs(self.current_load_kw - mean) > 3 * std if std > 0 else False

    def _build_features(self, step: int) -> dict:
        """시계열 특성 생성 (시간, 요일, lag, 날씨 proxy)."""
        row = self.timeseries.iloc[step]
        ts  = pd.Timestamp(row["timestamp"])
        lag1  = self._load_history[-1]  if self._load_history else 0.0
        lag96 = self._load_history[-96] if len(self._load_history) >= 96 else lag1
        return {
            "hour":        ts.hour,
            "minute":      ts.minute,
            "weekday":     ts.weekday(),
            "is_weekend":  int(ts.weekday() >= 5),
            "lag1":        lag1,
            "lag96":       lag96,
            "price_buy":   float(row.get("price_buy", 100.0)),
            "pv_kw":       float(row.get("pv_kw", 0.0)),
        }

    def _surrogate_lgbm_forecast(self, features: dict) -> float:
        """
        경량 대리 예측 모델 (LightGBM 대신 회귀 공식 사용).
        실제 ALFP는 학습된 LightGBM 모델을 사용함.
        """
        base  = features["lag96"] * 0.6 + features["lag1"] * 0.3
        # 시간대 계수
        hour_factor = 1.0 + 0.3 * np.sin(np.pi * (features["hour"] - 6) / 12)
        # 주말 감소
        weekend_factor = 0.85 if features["is_weekend"] else 1.0
        pred = base * hour_factor * weekend_factor
        # 약간의 노이즈 (시뮬레이션 현실감)
        noise = self.model.rng.normal(0, pred * 0.03)
        return max(float(pred + noise), 0.0)

    def _surrogate_pv_forecast(self, features: dict) -> float:
        """경량 PV 예측 (일사량 패턴 근사)."""
        hour = features["hour"]
        # 낮 시간대만 발전 (6~18시)
        if 6 <= hour <= 18:
            sun_factor = np.sin(np.pi * (hour - 6) / 12)
            base_pv = features["pv_kw"]
            noise = self.model.rng.normal(0, base_pv * 0.05 + 0.01)
            return max(float(base_pv * sun_factor + noise), 0.0)
        return 0.0

    def _decision_from_alfp_or_rules(self, step: int) -> str:
        """
        ALFP decisions가 있으면 해당 스텝의 거래/DR 추천 사용, 없으면 경량 규칙 기반 결정.
        """
        trading_by_step = getattr(self.model, "_trading_by_step", None) or {}
        dr_by_step = getattr(self.model, "_dr_by_step", None) or {}

        if step in trading_by_step and trading_by_step[step]:
            return "sell"  # ALFP 추천: 이 스텝에 P2P 판매
        if step in dr_by_step:
            return "demand_response"  # ALFP 추천: 수요반응
        return self._make_decision()

    def _make_decision(self) -> str:
        """예측 기반 의사결정 (ESS/거래 신호 생성) — decisions 미제공 시 사용."""
        hour = self.model.current_hour
        surplus = self.predicted_pv_kw - self.predicted_load_kw

        if surplus > 0.5 and hour in range(10, 16):
            return "sell"           # 잉여 전력 판매
        elif self.current_price_buy > 120 and hour in range(9, 22):
            return "discharge"      # 피크 요금 → ESS 방전
        elif self.current_price_buy < 80 and hour in range(0, 6):
            return "charge"         # 저요금 → ESS 충전
        return "none"

    # ──────────────────────────────────────────────────────────────
    # 공개 속성 / 유틸리티
    # ──────────────────────────────────────────────────────────────
    @property
    def has_pv(self) -> bool:
        return self.timeseries["pv_kw"].max() > 0.1

    @property
    def total_steps(self) -> int:
        return len(self.timeseries)
