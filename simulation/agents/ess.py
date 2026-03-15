"""
ESSAgent - Phase 3: ESS 연동

TOU(Time-of-Use) 요금제 기반 충방전 최적화.
예측된 부하/PV 결과를 받아 Rule-based 스케줄을 결정하고
Peak Shaving 및 요금 절감 효과를 추적합니다.
"""

from __future__ import annotations

import mesa


class ESSAgent(mesa.Agent):
    """
    배터리 에너지 저장 시스템(BESS) 에이전트.

    하나의 ESSAgent는 단지 내 공유 ESS(EnergyHub 노드)를 모델링하거나
    개별 프로슈머에 연결된 가정용 ESS를 모델링합니다.

    충방전 전략:
      - 저요금 시간대(가격 낮음): 충전 (valley filling)
      - 고요금 / 피크 시간대: 방전 (peak shaving)
      - 잉여 PV 있을 때: 충전 (self-consumption 극대화)
      - SoC 상·하한 보호: SoC ∈ [soc_min, soc_max]
    """

    def __init__(
        self,
        model: mesa.Model,
        capacity_kwh: float = 200.0,
        max_charge_kw: float = 50.0,
        max_discharge_kw: float = 50.0,
        soc_init: float = 0.5,
        soc_min: float = 0.10,
        soc_max: float = 0.95,
        efficiency: float = 0.95,
        price_charge_threshold: float = 85.0,    # 원/kWh 이하 → 충전 고려
        price_discharge_threshold: float = 115.0, # 원/kWh 이상 → 방전 고려
        peak_load_threshold_kw: float = 500.0,   # 커뮤니티 피크 임계값
    ):
        super().__init__(model)
        self.capacity_kwh            = capacity_kwh
        self.max_charge_kw           = max_charge_kw
        self.max_discharge_kw        = max_discharge_kw
        self.soc: float              = soc_init          # 현재 SoC (0~1)
        self.soc_min                 = soc_min
        self.soc_max                 = soc_max
        self.efficiency              = efficiency
        self.price_charge_threshold  = price_charge_threshold
        self.price_discharge_threshold = price_discharge_threshold
        self.peak_load_threshold_kw  = peak_load_threshold_kw

        # ── 스텝 상태 ────────────────────────────────────────
        self.action: str          = "idle"    # 'charge' | 'discharge' | 'idle'
        self.power_kw: float      = 0.0       # (+)충전 / (-)방전
        self.energy_kwh: float    = soc_init * capacity_kwh  # 저장 에너지

        # ── 누적 통계 ────────────────────────────────────────
        self.total_charged_kwh: float    = 0.0
        self.total_discharged_kwh: float = 0.0
        self.peak_shaving_count: int     = 0    # 피크 억제 횟수
        self.cumulative_saving_krw: float = 0.0 # 누적 요금 절감 (원)

        # ── 스케줄 이력 ──────────────────────────────────────
        self.schedule_log: list[dict] = []

    # ─────────────────────────────────────────────────────────────
    # Mesa step()
    # ─────────────────────────────────────────────────────────────
    def step(self) -> None:
        """
        1) ALFP decisions의 ess_schedule이 있으면 해당 스텝 스케줄 사용
        2) 없으면 커뮤니티 상태 수집 후 TOU + 피크 기반 충방전 결정
        3) SoC 업데이트
        4) 절감액 계산
        """
        step = self.model.current_step
        ess_by_step = getattr(self.model, "_ess_schedule_by_step", None) or {}

        # 커뮤니티 상태·가격·시간은 항상 수집 (절감/피크 판단·로그용)
        community_load = self._community_predicted_load()
        community_pv   = self._community_predicted_pv()
        avg_price_buy  = self._avg_price_buy()
        hour           = self.model.current_hour

        if step in ess_by_step:
            # ALFP DecisionAgent의 ESS 스케줄 사용
            s = ess_by_step[step]
            self.action = s.get("action", "idle")
            self.power_kw = float(s.get("power_kw", 0.0))
        else:
            # 기존 경량 규칙 기반 결정
            self.action, self.power_kw = self._decide(
                hour, community_load, community_pv, avg_price_buy
            )

        # ── SoC 업데이트 (15분 = 0.25시간) ───────────────────
        dt = 0.25  # 시간 단위

        if self.action == "charge":
            energy_in = self.power_kw * dt * self.efficiency
            self.energy_kwh = min(
                self.energy_kwh + energy_in, self.soc_max * self.capacity_kwh
            )
            self.total_charged_kwh += energy_in
        elif self.action == "discharge":
            energy_out = self.power_kw * dt / self.efficiency
            self.energy_kwh = max(
                self.energy_kwh - energy_out, self.soc_min * self.capacity_kwh
            )
            self.total_discharged_kwh += energy_out
            if community_load > self.peak_load_threshold_kw:
                self.peak_shaving_count += 1

        self.soc = self.energy_kwh / self.capacity_kwh

        # ── 요금 절감 추정 ────────────────────────────────────
        if self.action == "discharge":
            saving = self.power_kw * dt * (avg_price_buy - self.price_charge_threshold)
            self.cumulative_saving_krw += max(saving, 0.0)

        # 이력 기록
        self.schedule_log.append({
            "step":     self.model.current_step,
            "hour":     hour,
            "action":   self.action,
            "power_kw": round(self.power_kw, 2),
            "soc":      round(self.soc, 3),
        })

    # ─────────────────────────────────────────────────────────────
    # 내부 로직
    # ─────────────────────────────────────────────────────────────
    def _decide(
        self,
        hour: int,
        community_load: float,
        community_pv: float,
        price_buy: float,
    ) -> tuple[str, float]:
        """TOU + 피크 + 잉여PV 기반 충방전 결정."""

        net_load  = community_load - community_pv
        surplus   = max(-net_load, 0.0)
        available_energy = (self.soc_max * self.capacity_kwh - self.energy_kwh)
        storable_energy  = (self.energy_kwh - self.soc_min * self.capacity_kwh)

        # ── 피크 방전 (최우선) ────────────────────────────────
        if (
            community_load > self.peak_load_threshold_kw
            and self.soc > self.soc_min + 0.05
        ):
            power = min(self.max_discharge_kw, community_load - self.peak_load_threshold_kw)
            return "discharge", round(power, 2)

        # ── 잉여 PV 충전 ──────────────────────────────────────
        if surplus > 1.0 and available_energy > 0.1:
            power = min(self.max_charge_kw, surplus)
            return "charge", round(power, 2)

        # ── TOU 저요금 충전 ───────────────────────────────────
        if price_buy <= self.price_charge_threshold and available_energy > 0.1:
            power = min(self.max_charge_kw, available_energy / 0.25)
            return "charge", round(power, 2)

        # ── TOU 고요금 방전 ───────────────────────────────────
        if (
            price_buy >= self.price_discharge_threshold
            and storable_energy > 0.1
            and self.soc > self.soc_min
        ):
            power = min(self.max_discharge_kw, storable_energy / 0.25)
            return "discharge", round(power, 2)

        return "idle", 0.0

    def _community_predicted_load(self) -> float:
        """전체 ProsumerAgent 예측 부하 합산."""
        from simulation.agents.prosumer import ProsumerAgent
        aset = self.model.agents_by_type.get(ProsumerAgent) or []
        return sum(a.predicted_load_kw for a in aset)

    def _community_predicted_pv(self) -> float:
        """전체 ProsumerAgent 예측 PV 합산."""
        from simulation.agents.prosumer import ProsumerAgent
        aset = self.model.agents_by_type.get(ProsumerAgent) or []
        return sum(a.predicted_pv_kw for a in aset)

    def _avg_price_buy(self) -> float:
        """전체 ProsumerAgent 평균 구매 단가."""
        from simulation.agents.prosumer import ProsumerAgent
        prosumers = list(self.model.agents_by_type.get(ProsumerAgent) or [])
        if not prosumers:
            return 100.0
        return sum(a.current_price_buy for a in prosumers) / len(prosumers)

    # ─────────────────────────────────────────────────────────────
    # 공개 속성
    # ─────────────────────────────────────────────────────────────
    @property
    def soc_pct(self) -> float:
        return round(self.soc * 100, 1)

    @property
    def utilization_rate(self) -> float:
        """ESS 활용률: 방전량 / 충전량."""
        if self.total_charged_kwh < 0.01:
            return 0.0
        return min(self.total_discharged_kwh / self.total_charged_kwh, 1.0)
