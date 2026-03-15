"""
CDA Settlement Engine (PRD §9) — Execution 대체

기능: 거래 기록 반영, 정책 검증, Mesa 시뮬레이션 업데이트.
seapac_agents.execution과 동일한 run_execution(decisions, ...) → ExecutionResult
인터페이스를 제공하여 파이프라인에서 그대로 교체 가능.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from simulation.model import ALFPSimulationModel


@dataclass
class ExecutionResult:
    """실행 단계 결과 (Step 4 출력 → Step 5 Evaluation 입력). seapac_agents.execution.ExecutionResult와 동일."""
    summary: dict = field(default_factory=dict)
    dataframe: "pd.DataFrame | None" = None
    approved: bool = True
    validation_errors: list[str] = field(default_factory=list)
    model: "ALFPSimulationModel | None" = None


def run_execution(
    decisions: dict,
    *,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    prosumer_ids: list[str] | None = None,
    measure_date: str | None = None,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    ess_peak_threshold_kw: float = 500.0,
    max_charge_kw: float = 100.0,
    max_discharge_kw: float = 100.0,
    strict_validation: bool = False,
    max_peak_load_kw: float | None = None,
    min_ess_soc_pct: float | None = 10.0,
    max_ess_soc_pct: float | None = 95.0,
) -> ExecutionResult:
    """
    CDA Settlement: decisions(CDA 코디네이터 출력) 검증 후 Mesa 시뮬레이션 실행.

    seapac_agents.execution.run_execution과 동일 시그니처·반환형.
    내부적으로 seapac_agents.execution을 호출하여 정책 검증 및 Mesa 업데이트를
    한 곳에서 수행 (중복 제거).
    """
    from seapac_agents.execution import run_execution as _run_execution

    return _run_execution(
        decisions,
        data_path=data_path,
        n_steps=n_steps,
        phase=phase,
        prosumer_ids=prosumer_ids,
        measure_date=measure_date,
        seed=seed,
        ess_capacity_kwh=ess_capacity_kwh,
        ess_peak_threshold_kw=ess_peak_threshold_kw,
        max_charge_kw=max_charge_kw,
        max_discharge_kw=max_discharge_kw,
        strict_validation=strict_validation,
        max_peak_load_kw=max_peak_load_kw,
        min_ess_soc_pct=min_ess_soc_pct,
        max_ess_soc_pct=max_ess_soc_pct,
    )
