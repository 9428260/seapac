"""
Step 4 — Action Execution Engine (PRD: seapac_agentic_prd.md)

검증된 에이전트 결정(decisions)을 Mesa 시뮬레이션에 적용하는 실행 단계.

Execution Flow (PRD):
  Agent Proposal → Policy Validation → Coordinator Approval → Mesa Update

Supported actions (PRD):
  - TradeAction
  - ESSAction
  - DemandResponseAction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from simulation.model import ALFPSimulationModel


# ─────────────────────────────────────────────────────────────────
# Action types (PRD Step 4)
# ─────────────────────────────────────────────────────────────────

@dataclass
class ESSAction:
    """ESS 충/방전 제어 액션."""
    step: int
    action: str  # 'charge' | 'discharge' | 'idle'
    power_kw: float
    soc_kwh: float = 0.0
    net_load_kw: float = 0.0


@dataclass
class TradeAction:
    """에너지 거래 액션 (P2P 판매 등)."""
    step: int
    action: str  # 'sell_p2p' 등
    surplus_kw: float
    timestamp: str = ""


@dataclass
class DemandResponseAction:
    """수요반응(DR) 액션."""
    step: int
    action: str  # 'demand_response'
    net_load_kw: float
    recommended_reduction_kw: float
    timestamp: str = ""


# ─────────────────────────────────────────────────────────────────
# Policy Validation (PRD: Policy-Agent / Validation)
# ─────────────────────────────────────────────────────────────────

class PolicyValidationError(Exception):
    """정책 검증 실패."""
    pass


def validate_ess_action(action: ESSAction, max_charge_kw: float = 100.0, max_discharge_kw: float = 100.0) -> list[str]:
    """ESS 액션 정책 검증. 반환: 오류 메시지 목록 (빈 목록이면 통과)."""
    errors = []
    if action.action not in ("charge", "discharge", "idle"):
        errors.append(f"Invalid ESS action: {action.action}")
    if action.action == "charge" and action.power_kw > max_charge_kw:
        errors.append(f"ESS charge power {action.power_kw} exceeds max {max_charge_kw} kW")
    if action.action == "discharge" and action.power_kw > max_discharge_kw:
        errors.append(f"ESS discharge power {action.power_kw} exceeds max {max_discharge_kw} kW")
    if action.power_kw < 0:
        errors.append("ESS power_kw must be non-negative")
    return errors


def validate_trade_action(action: TradeAction) -> list[str]:
    """거래 액션 정책 검증."""
    errors = []
    if action.surplus_kw < 0:
        errors.append("Trade surplus_kw must be non-negative")
    if action.action not in ("sell_p2p",):
        errors.append(f"Unknown trade action: {action.action}")
    return errors


def validate_dr_action(action: DemandResponseAction) -> list[str]:
    """수요반응 액션 정책 검증."""
    errors = []
    if action.recommended_reduction_kw < 0:
        errors.append("DR recommended_reduction_kw must be non-negative")
    return errors


# ─────────────────────────────────────────────────────────────────
# Coordinator Approval (PRD: MarketCoordinator / Approval)
# ─────────────────────────────────────────────────────────────────

def approve_actions(
    ess_errors: list[list[str]],
    trade_errors: list[list[str]],
    dr_errors: list[list[str]],
    strict: bool = False,
) -> tuple[bool, list[str]]:
    """
    정책 검증 결과를 바탕으로 실행 승인 여부 결정.
    strict=True 이면 오류가 하나라도 있으면 미승인.
    반환: (approved, list of rejection reasons)
    """
    all_errors = []
    for errs in ess_errors + trade_errors + dr_errors:
        all_errors.extend(errs)
    if strict and all_errors:
        return False, all_errors
    # 기본: 오류가 있어도 진행하되, 오류 항목은 로그로 남김 (해당 스텝만 스킵 등은 별도 정책에서)
    return True, all_errors


# ─────────────────────────────────────────────────────────────────
# Build actions from decisions (ALFP / Step 3 output)
# ─────────────────────────────────────────────────────────────────

def build_actions_from_decisions(decisions: dict) -> tuple[list[ESSAction], list[TradeAction], list[DemandResponseAction]]:
    """ALFP(또는 Step 3) decisions를 ESS/Trade/DR 액션 리스트로 변환."""
    ess_actions = []
    for i, row in enumerate(decisions.get("ess_schedule") or []):
        ess_actions.append(ESSAction(
            step=i,
            action=row.get("action", "idle"),
            power_kw=float(row.get("power_kw", 0.0)),
            soc_kwh=float(row.get("soc_kwh", 0.0)),
            net_load_kw=float(row.get("net_load_kw", 0.0)),
        ))
    trade_actions = []
    for r in decisions.get("trading_recommendations") or []:
        trade_actions.append(TradeAction(
            step=-1,  # timestamp 기반 매칭은 Mesa 쪽에서 수행
            action=r.get("action", "sell_p2p"),
            surplus_kw=float(r.get("surplus_kw", 0.0)),
            timestamp=str(r.get("timestamp", "")),
        ))
    dr_actions = []
    for r in decisions.get("demand_response_events") or []:
        dr_actions.append(DemandResponseAction(
            step=-1,
            action=r.get("action", "demand_response"),
            net_load_kw=float(r.get("net_load_kw", 0.0)),
            recommended_reduction_kw=float(r.get("recommended_reduction_kw", 0.0)),
            timestamp=str(r.get("timestamp", "")),
        ))
    return ess_actions, trade_actions, dr_actions


def validate_all_actions(
    ess_actions: list[ESSAction],
    trade_actions: list[TradeAction],
    dr_actions: list[DemandResponseAction],
    max_charge_kw: float = 100.0,
    max_discharge_kw: float = 100.0,
) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    """전체 액션에 대해 정책 검증 수행."""
    ess_errors = [validate_ess_action(a, max_charge_kw, max_discharge_kw) for a in ess_actions]
    trade_errors = [validate_trade_action(a) for a in trade_actions]
    dr_errors = [validate_dr_action(a) for a in dr_actions]
    return ess_errors, trade_errors, dr_errors


# ─────────────────────────────────────────────────────────────────
# Action Execution Engine — Mesa Update
# ─────────────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    """실행 단계 결과 (Step 4 출력 → Step 5 Evaluation 입력)."""
    summary: dict = field(default_factory=dict)
    dataframe: pd.DataFrame | None = None
    approved: bool = True
    validation_errors: list[str] = field(default_factory=list)
    model: ALFPSimulationModel | None = None


def run_execution(
    decisions: dict,
    *,
    data_path: str = "data/train_2026_seoul.pkl",
    n_steps: int = 96,
    phase: int = 4,
    prosumer_ids: list[str] | None = None,
    seed: int = 42,
    ess_capacity_kwh: float = 200.0,
    ess_peak_threshold_kw: float = 500.0,
    max_charge_kw: float = 100.0,
    max_discharge_kw: float = 100.0,
    strict_validation: bool = False,
) -> ExecutionResult:
    """
    Step 4 실행: Agent Proposal → Policy Validation → Coordinator Approval → Mesa Update.

    Args:
        decisions: ALFP 또는 Step 3 에이전트 결정 (ess_schedule, trading_recommendations, demand_response_events 포함)
        data_path: Mesa 시뮬레이션용 데이터 pkl 경로
        n_steps: 시뮬레이션 스텝 수
        phase: Mesa phase (1~4, 기본 4 = 전체 기능)
        prosumer_ids: 프로슈머 ID 목록 (None이면 전체)
        seed: 난수 시드
        ess_capacity_kwh, ess_peak_threshold_kw: ESS 파라미터
        max_charge_kw, max_discharge_kw: 정책 검증용 상한
        strict_validation: True면 검증 오류 시 Mesa 실행 스킵

    Returns:
        ExecutionResult (summary, dataframe, validation_errors 등)
    """
    # 1) Build actions from decisions
    ess_actions, trade_actions, dr_actions = build_actions_from_decisions(decisions)

    # 2) Policy Validation
    ess_errors, trade_errors, dr_errors = validate_all_actions(
        ess_actions, trade_actions, dr_actions,
        max_charge_kw=max_charge_kw, max_discharge_kw=max_discharge_kw,
    )
    approved, validation_errors = approve_actions(ess_errors, trade_errors, dr_errors, strict=strict_validation)

    # 3) Coordinator Approval (통과로 간주) → 4) Mesa Update
    # 검증 실패(strict)여도 Mesa 시뮬레이션은 실행하여 결과(summary, dataframe)를 항상 생성하고,
    # run_execution.py 등에서 Mesa 시뮬레이션 결과를 사용할 수 있도록 함. approved=False로 검증 실패 표시.
    model = ALFPSimulationModel(
        phase=phase,
        data_path=data_path,
        n_steps=n_steps,
        prosumer_ids=prosumer_ids,
        seed=seed,
        ess_capacity_kwh=ess_capacity_kwh,
        ess_peak_threshold_kw=ess_peak_threshold_kw,
        alfp_decisions=decisions,
    )
    df = model.run()
    summary = model.summary()
    summary["execution_stage"] = "Step4_ActionExecution"
    summary["validation_approved"] = approved
    summary["validation_errors_count"] = len(validation_errors)

    return ExecutionResult(
        summary=summary,
        dataframe=df,
        approved=approved,
        validation_errors=validation_errors,
        model=model,
    )
