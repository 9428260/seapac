"""
SEAPAC Full Integrated Pipeline
================================
전체 아키텍처를 통합하여 실행하는 메인 스크립트.

Architecture (기본):
  [ALFP decision]
        ↓
  Step3  AgentScope Multi-Agent Decision
        ↓
  Step4  Action Execution Engine
        ↓
  Step5  Evaluation Engine

Architecture (--use-parallel, 전력거래와 Parallel Agents 동시 실행):
  [ALFP decision]
        ↓
  Step3  AgentScope Multi-Agent Decision
        ↓
  ┌─────────────────────────────────────────────────────┐
  │  Thread A: Step3.5 Parallel Agents                  │
  │            (Policy / EcoSaver / Storage)            │
  │            ESS·DR 정책 검증 (비토권)                 │
  │                                                     │
  │  Thread B: Step4 전력거래 실행                       │
  │            (run_execution — P2P 시장 + MESA)         │
  └─────────────────────────────────────────────────────┘
        ↓  두 스레드 완료 후 결과 병합
  Step5  Evaluation Engine

Usage:
  python run_full_pipeline.py
  python run_full_pipeline.py --steps 96 --phase 4 --use-parallel
  python run_full_pipeline.py --prosumer bus_48_Commercial --log-file logs/run.log
  python run_full_pipeline.py --skip-alfp  (ALFP 없이 MESA→Step2~5 만 실행)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

# 서브프로세스(Dashboard 기동)에서도 pipeline_dashboard 임포트 성공하도록 프로젝트 루트를 path에 추가
_SCRIPT_ROOT = str(Path(__file__).resolve().parent)
if _SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPT_ROOT)

try:
    from pipeline_dashboard.db import (
        get_db_path,
        init_db,
        create_run,
        add_stage,
        add_pipeline_agent_step,
        finish_run,
        upsert_artifact,
    )
    _DASHBOARD_AVAILABLE = True
except ImportError as e:
    get_db_path = init_db = create_run = add_stage = add_pipeline_agent_step = finish_run = upsert_artifact = None  # type: ignore[misc, assignment]
    _DASHBOARD_AVAILABLE = False
    _DASHBOARD_IMPORT_ERROR = e


# ─────────────────────────────────────────────────────────────────
# 로거 설정
# ─────────────────────────────────────────────────────────────────

_LOG_FORMAT = "%(asctime)s  %(levelname)-7s  %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

def _setup_logger(log_file: str | None = None, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger("seapac")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    logger.addHandler(handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(fh)

    return logger


log = logging.getLogger("seapac")


# ─────────────────────────────────────────────────────────────────
# 단계별 결과 컨테이너
# ─────────────────────────────────────────────────────────────────

@dataclass
class StageResult:
    name: str
    ok: bool = True
    elapsed_sec: float = 0.0
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _utc_now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _record_stage_agent_logs(
    run_id: int | None,
    stage_order: int,
    agent_logs: list[dict[str, Any]] | None,
    db_path: Path | None,
) -> None:
    """Persist one stage's per-agent logs for timeline/detail UI."""
    if not _DASHBOARD_AVAILABLE or run_id is None or db_path is None or not agent_logs:
        return
    for idx, item in enumerate(agent_logs):
        add_pipeline_agent_step(
            run_id=run_id,
            stage_order=stage_order,
            agent_name=str(item.get("agent_name") or f"agent_{idx + 1}"),
            role_label=item.get("role_label"),
            step_order=int(item.get("step_order", idx)),
            started_at=str(item.get("started_at") or _utc_now_str()),
            finished_at=item.get("finished_at"),
            elapsed_sec=item.get("elapsed_sec"),
            ok=bool(item.get("ok", True)),
            summary=item.get("summary") or {},
            error_text=item.get("error_text"),
            db_path=db_path,
        )


@dataclass
class PipelineResult:
    stages: list[StageResult] = field(default_factory=list)
    total_elapsed_sec: float = 0.0
    ok: bool = True

    def add(self, stage: StageResult) -> None:
        self.stages.append(stage)
        if not stage.ok:
            self.ok = False

    def print_summary(self) -> None:
        _divider("=", 72)
        log.info("▶ 파이프라인 실행 요약")
        _divider("-", 72)
        for s in self.stages:
            status = "✓" if s.ok else "✗"
            log.info(
                "  %s  %-40s  %6.2fs",
                status,
                s.name,
                s.elapsed_sec,
            )
            for k, v in s.summary.items():
                log.info("       %-38s  %s", k, v)
            if s.error:
                log.error("       ERROR: %s", s.error)
        _divider("-", 72)
        total_status = "✓ 성공" if self.ok else "✗ 실패"
        log.info("  %s  총 소요: %.2fs", total_status, self.total_elapsed_sec)
        _divider("=", 72)


# ─────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────

def _divider(char: str = "─", width: int = 72) -> None:
    log.info(char * width)


def _stage_start(label: str) -> float:
    _divider()
    log.info("▷ %s  시작", label)
    _divider()
    return time.perf_counter()


def _stage_end(label: str, t0: float, summary: dict[str, Any]) -> StageResult:
    elapsed = time.perf_counter() - t0
    _divider()
    log.info("◁ %s  완료  (%.2fs)", label, elapsed)
    for k, v in summary.items():
        log.info("   • %-36s %s", k + ":", v)
    return StageResult(name=label, ok=True, elapsed_sec=elapsed, summary=summary)


def _stage_error(label: str, t0: float, err: Exception) -> StageResult:
    elapsed = time.perf_counter() - t0
    log.error("◁ %s  오류  (%.2fs) — %s", label, elapsed, err)
    return StageResult(name=label, ok=False, elapsed_sec=elapsed, error=str(err))


# ─────────────────────────────────────────────────────────────────
# 인자 파서
# ─────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="SEAPAC Full Integrated Pipeline (ALFP → MESA → Step2~5 → Parallel Agents)"
    )
    # 데이터
    p.add_argument("--data-path",   default="data/train_2026_seoul.pkl", help="학습 데이터 경로")
    p.add_argument("--measure-date", default=None, help="기준일자 YYYY-MM-DD (해당 날짜 데이터로 실행; 미설정 시 데이터 첫날)")
    p.add_argument("--operating-mode", default="day_ahead", choices=["day_ahead", "short_horizon"], help="운영 모드 (day_ahead 또는 short_horizon)")
    p.add_argument("--live-ingest-path", default=None, help="외부 측정값 CSV/JSON 경로 (실시간 ingest overlay)")
    p.add_argument("--llm-mode", default=os.environ.get("SEAPAC_LLM_MODE", "all"), choices=["off", "forecast", "forecast_plan", "core", "market", "plan", "all"], help="통합 LLM 모드")
    p.add_argument("--alfp-mode", default="full", choices=["full", "forecast_only"], help="ALFP 실행 범위 (전체 또는 예측 전용)")
    p.add_argument("--prosumer",    default="bus_48_Commercial",          help="ALFP 예측 프로슈머 ID (단일)")
    p.add_argument("--prosumers",   nargs="+",  default=None,             help="ALFP 예측 프로슈머 ID 목록 (다중 P2P 거래 모드; 지정 시 --prosumer 무시)")
    # 시뮬레이션
    p.add_argument("--steps",       type=int,   default=96,    help="시뮬레이션 스텝 수 (15분 단위, 기본 96=24h)")
    p.add_argument("--phase",       type=int,   default=4,     choices=[1, 2, 3, 4], help="Mesa 시뮬레이션 단계")
    p.add_argument("--peak-threshold", type=float, default=500.0, help="피크 임계값 (kW)")
    p.add_argument("--ess-capacity",   type=float, default=200.0, help="ESS 용량 (kWh)")
    p.add_argument("--grid-price",     type=float, default=100.0, help="계통 전기 단가 (원/kWh)")
    p.add_argument("--seed",        type=int,   default=42)
    # 실행 옵션
    p.add_argument("--skip-alfp",   action="store_true", help="ALFP 단계를 건너뜀 (MESA→Step2~5 만 실행)")
    p.add_argument("--use-parallel", action="store_true", help="Step3.5 Parallel Agents 활성화")
    p.add_argument("--use-cda",     action="store_true", default=True, help="Step3 CDA 시장 모드 사용 (기본값)")
    p.add_argument("--no-cda",      action="store_false", dest="use_cda", help="Step3 AgentScope 페르소나 모드 사용 (CDA 비활성화)")
    p.add_argument("--use-cda-negotiation", action="store_true", help="Step3 CDA + Strategy Agent(LLM) + Negotiation Layer 사용 (--use-cda 필요)")
    p.add_argument("--log-file",    default=None, help="로그 파일 경로 (지정한 경우에만 파일 기록)")
    p.add_argument("--log-dir",     default="logs", help="로그 디렉토리 (명시적 --log-file 사용 시 참고용)")
    p.add_argument("--verbose",     action="store_true", help="DEBUG 수준 상세 출력")
    p.add_argument("--audit-log",   default=None, help="Parallel Layer 감사 로그 경로")
    return p.parse_args()


# ─────────────────────────────────────────────────────────────────
# 파이프라인 단계 구현
# ─────────────────────────────────────────────────────────────────

def stage_alfp(
    args: argparse.Namespace,
    run_id: int | None = None,
    db_path: Path | None = None,
) -> tuple[StageResult, dict, dict]:
    """[ALFP decision] — LangGraph 부하 예측 및 운영 의사결정. run_id/db_path 있으면 Agent별 단계를 DB에 기록."""
    label = "[ALFP] 전력 사용량 예측" if getattr(args, "alfp_mode", "full") == "forecast_only" else "[ALFP] 부하 예측 및 운영 의사결정"
    t0 = _stage_start(label)
    try:
        from alfp.main import run as alfp_run

        log.info("   프로슈머: %s", args.prosumer)
        log.info("   데이터:   %s", args.data_path)
        log.info("   Horizon:  %d 스텝 (%d시간)", args.steps, args.steps * 15 // 60)

        alfp_result = alfp_run(
            prosumer_id=args.prosumer,
            data_path=args.data_path,
            forecast_horizon=args.steps,
            execution_mode=getattr(args, "alfp_mode", "full"),
            operating_mode=args.operating_mode,
            live_ingest_path=args.live_ingest_path,
            llm_mode=args.llm_mode,
            verbose=args.verbose,
            run_id=run_id,
            db_path=str(db_path) if db_path else None,
        )

        decisions: dict = alfp_result.get("decisions", {})
        metrics = alfp_result.get("validation_metrics", {})

        # 로그: ALFP 출력 요약
        n_ess    = len(decisions.get("ess_schedule", []))
        n_trade  = len(decisions.get("trading_recommendations", []))
        n_dr     = len(decisions.get("demand_response_events", []))

        kpi = metrics.get("kpi", {})
        mape_ok  = kpi.get("MAPE_pass", "N/A")
        mape_val = kpi.get("MAPE_achieved", float("nan"))

        plan = alfp_result.get("forecast_plan", {})
        llm_reasoning = str(plan.get("llm_reasoning", ""))[:80]
        summary = {
            "예측 MAPE": f"{mape_val:.2f}%  (KPI {'✓' if mape_ok is True else '✗' if mape_ok is False else mape_ok})",
            "선택 모델": plan.get("selected_model", "N/A"),
            "예측 Horizon": f"{plan.get('forecast_horizon_steps', args.steps)} steps",
            "예측 계획 근거": llm_reasoning or "(미사용)",
        }
        if getattr(args, "alfp_mode", "full") != "forecast_only":
            llm_strat = decisions.get("llm_strategy", {})
            alert_lv = llm_strat.get("alert_level", "N/A")
            ess_strat = llm_strat.get("ess_strategy", "")[:80] if llm_strat.get("ess_strategy") else ""
            summary.update({
                "ESS 스케줄 건수": n_ess,
                "거래 권고 건수": n_trade,
                "DR 이벤트 건수": n_dr,
                "LLM 경보 수준": alert_lv,
                "LLM ESS 전략 (요약)": ess_strat or "(없음)",
            })

        log.debug("   ALFP decisions keys: %s", list(decisions.keys()))
        if args.verbose and n_ess > 0:
            log.debug("   ESS 스케줄[0]: %s", decisions["ess_schedule"][0])

        return _stage_end(label, t0, summary), decisions, alfp_result

    except Exception as exc:
        log.exception("ALFP 실행 오류")
        return _stage_error(label, t0, exc), {}, {}


def _merge_alfp_decisions(results: list[tuple[str, dict]]) -> dict:
    """
    여러 프로슈머의 ALFP decisions를 하나로 병합.
    각 항목에 prosumer_id 태그를 추가하여 P2P 거래 매칭 시 식별 가능하게 함.
    """
    merged: dict = {
        "ess_schedule": [],
        "trading_recommendations": [],
        "demand_response_events": [],
        "llm_strategy": {},
        "prosumer_decisions": {},  # prosumer_id → 원본 decisions
    }
    for prosumer_id, dec in results:
        merged["prosumer_decisions"][prosumer_id] = dec
        for item in dec.get("ess_schedule", []):
            merged["ess_schedule"].append({**item, "prosumer_id": prosumer_id})
        for item in dec.get("trading_recommendations", []):
            merged["trading_recommendations"].append({**item, "prosumer_id": prosumer_id})
        for item in dec.get("demand_response_events", []):
            merged["demand_response_events"].append({**item, "prosumer_id": prosumer_id})
        # llm_strategy는 마지막 성공 프로슈머 것을 사용 (대표값)
        if dec.get("llm_strategy"):
            merged["llm_strategy"] = dec["llm_strategy"]
    return merged


def stage_alfp_multi(
    args: argparse.Namespace,
    prosumers: list[str],
    run_id: int | None = None,
    db_path: "Path | None" = None,
) -> tuple["StageResult", dict, dict]:
    """
    [ALFP Multi-Prosumer] — 선택된 프로슈머 각각에 대해 ALFP를 병렬 실행하고 decisions를 병합.
    P2P 거래가 가능하도록 각 프로슈머의 ESS·거래·DR 계획을 통합.
    """
    label = f"[ALFP] 다중 프로슈머 병렬 의사결정 ({len(prosumers)}명)"
    t0 = _stage_start(label)
    log.info("   P2P 거래 모드: 프로슈머 %s", prosumers)

    try:
        from alfp.main import run as alfp_run

        def _run_one(prosumer_id: str) -> tuple[str, dict, dict, bool]:
            """(prosumer_id, decisions, alfp_result, ok)"""
            log.info("   [ALFP/%s] 시작", prosumer_id)
            try:
                result = alfp_run(
                    prosumer_id=prosumer_id,
                    data_path=args.data_path,
                    forecast_horizon=args.steps,
                    execution_mode=getattr(args, "alfp_mode", "full"),
                    operating_mode=args.operating_mode,
                    live_ingest_path=args.live_ingest_path,
                    llm_mode=args.llm_mode,
                    verbose=args.verbose,
                    run_id=run_id,
                    db_path=str(db_path) if db_path else None,
                )
                dec = result.get("decisions", {})
                log.info(
                    "   [ALFP/%s] 완료 — ESS %d건 / 거래권고 %d건 / DR %d건",
                    prosumer_id,
                    len(dec.get("ess_schedule", [])),
                    len(dec.get("trading_recommendations", [])),
                    len(dec.get("demand_response_events", [])),
                )
                return prosumer_id, dec, result, True
            except Exception as e:
                log.error("   [ALFP/%s] 오류: %s (%s)", prosumer_id, e, type(e).__name__)
                log.exception("   [ALFP/%s] 상세:", prosumer_id)
                return prosumer_id, {}, {}, False

        # 프로슈머별 ALFP 병렬 실행
        with ThreadPoolExecutor(max_workers=len(prosumers), thread_name_prefix="alfp") as ex:
            futures = [ex.submit(_run_one, p) for p in prosumers]
            raw_results = [f.result() for f in futures]

        ok_results = [(pid, dec) for pid, dec, _, ok in raw_results if ok]
        ok_full_results = {pid: result for pid, _, result, ok in raw_results if ok}
        fail_ids = [pid for pid, _, _, ok in raw_results if not ok]

        if fail_ids:
            log.warning("   ALFP 실패 프로슈머: %s (rule-based fallback 적용)", fail_ids)

        merged = _merge_alfp_decisions(ok_results)

        n_ess   = len(merged["ess_schedule"])
        n_trade = len(merged["trading_recommendations"])
        n_dr    = len(merged["demand_response_events"])

        summary = {
            "실행 프로슈머":       ", ".join(prosumers),
            "성공 / 전체":         f"{len(ok_results)} / {len(prosumers)}",
            "ESS 스케줄 합산":     f"{n_ess}건",
            "P2P 거래 권고 합산":  f"{n_trade}건",
            "DR 이벤트 합산":      f"{n_dr}건",
            "P2P 거래 모드":       "✓ 활성화",
        }

        return _stage_end(label, t0, summary), merged, {"prosumer_results": ok_full_results}

    except Exception as exc:
        log.exception("ALFP 다중 프로슈머 실행 오류")
        return _stage_error(label, t0, exc), {}, {}


def _peak_risk_label(current_load_kw: float, peak_threshold_kw: float) -> str:
    if peak_threshold_kw <= 0:
        return "LOW"
    ratio = current_load_kw / peak_threshold_kw
    if ratio < 0.70:
        return "LOW"
    if ratio < 0.85:
        return "MEDIUM"
    return "HIGH"


def _alfp_forecast_to_state_json_list(
    alfp_result: dict,
    peak_threshold_kw: float,
    ess_capacity_kwh: float,
) -> list[dict]:
    """ALFP forecast 결과를 Agent planner 입력 state_json_list로 변환."""
    import pandas as pd

    load_df = alfp_result.get("load_forecast")
    pv_df = alfp_result.get("pv_forecast")
    net_df = alfp_result.get("net_load_forecast")
    feature_df = alfp_result.get("feature_df")
    plan = alfp_result.get("forecast_plan") or {}
    prosumer_id = plan.get("prosumer_id", "unknown")
    prosumer_type = plan.get("prosumer_type", "Unknown")

    if load_df is None or pv_df is None:
        return []

    merged = pd.merge(
        load_df[["timestamp", "predicted_load_kw"]].assign(timestamp=lambda df: df["timestamp"].astype(str)),
        pv_df[["timestamp", "predicted_pv_kw"]].assign(timestamp=lambda df: df["timestamp"].astype(str)),
        on="timestamp",
        how="inner",
    )
    if net_df is not None and "predicted_net_load_kw" in net_df.columns:
        merged = pd.merge(
            merged,
            net_df[["timestamp", "predicted_net_load_kw"]].assign(timestamp=lambda df: df["timestamp"].astype(str)),
            on="timestamp",
            how="left",
        )
    if feature_df is not None:
        cols = [c for c in ["timestamp", "price_buy", "price_sell", "price_p2p"] if c in feature_df.columns]
        if cols:
            merged = pd.merge(
                merged,
                feature_df[cols].assign(timestamp=lambda df: df["timestamp"].astype(str)).drop_duplicates("timestamp"),
                on="timestamp",
                how="left",
            )

    if merged.empty:
        return []

    dynamic_peak = float(merged["predicted_load_kw"].quantile(0.85))
    threshold = peak_threshold_kw
    if threshold <= 0 or threshold > float(merged["predicted_load_kw"].max()) * 2:
        threshold = dynamic_peak

    state_json_list: list[dict] = []
    ess_soc_pct = 50.0
    available_discharge = max((ess_soc_pct / 100.0 - 0.10) * ess_capacity_kwh * 0.95, 0.0)

    for _, row in merged.iterrows():
        ts = pd.Timestamp(row["timestamp"])
        pred_load = float(row.get("predicted_load_kw", 0.0) or 0.0)
        pred_pv = float(row.get("predicted_pv_kw", 0.0) or 0.0)
        pred_net = float(row.get("predicted_net_load_kw", pred_load - pred_pv) or 0.0)
        surplus = max(pred_pv - pred_load, 0.0)
        deficit = max(pred_load - pred_pv, 0.0)
        price_buy = float(row.get("price_buy", 100.0) or 100.0)
        price_sell = float(row.get("price_sell", max(price_buy * 0.7, 1.0)) or max(price_buy * 0.7, 1.0))
        p2p_floor = round(max(price_sell, 1.0), 2)
        p2p_ceil = round(max(price_buy * 0.95, p2p_floor + 1.0), 2)

        state_json_list.append({
            "time": ts.strftime("%H:%M"),
            "timestamp": str(ts),
            "step": len(state_json_list),
            "community_state": {
                "total_load": round(pred_load, 2),
                "pv_generation": round(pred_pv, 2),
                "surplus_energy": round(surplus, 2),
                "deficit_energy": round(deficit, 2),
                "peak_risk": _peak_risk_label(pred_load, threshold),
                "predicted_net_load_kw": round(pred_net, 2),
            },
            "market_state": {
                "grid_price": round(price_buy, 2),
                "community_trade_price_range": [p2p_floor, p2p_ceil],
            },
            "ess_state": {
                "soc": ess_soc_pct,
                "capacity": ess_capacity_kwh,
                "available_discharge": round(available_discharge, 2),
            },
            "prosumer_states": [{
                "prosumer_id": prosumer_id,
                "prosumer_type": prosumer_type,
                "load_kw": round(pred_load, 2),
                "pv_kw": round(pred_pv, 2),
                "surplus_energy": round(surplus, 2),
                "deficit_energy": round(deficit, 2),
                "price_buy": round(price_buy, 2),
                "price_sell": round(price_sell, 2),
                "price_p2p": p2p_floor,
            }],
        })
    return state_json_list


def stage_agent_plan_from_alfp(
    args: argparse.Namespace,
    alfp_result: dict,
) -> tuple[StageResult, dict]:
    """ALFP forecast_only 결과를 기반으로 policy/trading/storage agent 계획 및 실행."""
    label = "Agent Plan (Forecast-based)"
    t0 = _stage_start(label)
    try:
        from seapac_agents.agent_planner import run_agent_plan

        state_json_list = _alfp_forecast_to_state_json_list(
            alfp_result=alfp_result,
            peak_threshold_kw=args.peak_threshold,
            ess_capacity_kwh=args.ess_capacity,
        )
        if not state_json_list:
            raise ValueError("ALFP forecast 결과에서 agent planner 입력 state를 구성하지 못했습니다.")

        from alfp.llm import is_llm_enabled

        use_llm_plan = is_llm_enabled("agent_plan")
        decisions = run_agent_plan(
            state_json_list=state_json_list,
            alfp_decisions=alfp_result.get("decisions") or {},
            peak_threshold_kw=args.peak_threshold,
            max_charge_kw=min(50.0, args.ess_capacity / 4),
            max_discharge_kw=min(50.0, args.ess_capacity / 4),
            use_llm=use_llm_plan,
            max_revisions=0,
            data_path=args.data_path,
            n_steps=args.steps,
            phase=args.phase,
            seed=args.seed,
            ess_capacity_kwh=args.ess_capacity,
            verbose=args.verbose,
        )
        plan_meta = decisions.get("agent_plan") or {}
        plan_steps = plan_meta.get("steps") or []
        agent_logs = plan_meta.get("agent_logs") or []
        planning_mode = str(plan_meta.get("planning_mode") or ("llm" if use_llm_plan else "rule_based"))
        summary = {
            "계획 방식": "LLM Agent Plan" if planning_mode == "llm" else "Rule-based Agent Plan",
            "LLM 연계": "사용" if plan_meta.get("llm_used") else "미사용",
            "계획 ID": plan_meta.get("plan_id", "—"),
            "계획 목표": plan_meta.get("objective", "—"),
            "전력거래 권고": f"{len(decisions.get('trading_recommendations') or [])}건",
            "ESS 스케줄": f"{len(decisions.get('ess_schedule') or [])}건",
            "DR 이벤트": f"{len(decisions.get('demand_response_events') or [])}건",
            "정책 위반": f"{len(decisions.get('policy_violations') or [])}건",
            "실행 에이전트": "Policy / Trading / Storage / EcoSaver / Simulation",
            "계획 스텝": plan_steps,
            "실행 로그": agent_logs,
            "실행 완료": "완료" if plan_meta.get("execution_completed") else "미완료",
            "시뮬레이션 검증": "건너뜀" if plan_meta.get("simulation_skipped") else ("승인" if plan_meta.get("simulation_approved") else "미승인"),
        }
        return _stage_end(label, t0, summary), decisions
    except Exception as exc:
        log.exception("Forecast-based Agent Plan 오류")
        return _stage_error(label, t0, exc), {}



def stage_multi_agent_decision(
    args: argparse.Namespace,
    state_json_list: list[dict],
    alfp_decisions: dict | None = None,
) -> tuple[StageResult, dict, list[dict[str, Any]]]:
    """Step3 — AgentScope Multi-Agent Decision."""
    label = "AgentScope Multi-Agent Decision"
    t0 = _stage_start(label)
    try:
        max_kw = min(50.0, args.ess_capacity / 4)
        log.info("   입력:  %d 스텝 state JSON", len(state_json_list))
        log.info("   모드:  %s", "CDA 시장" if args.use_cda else "AgentScope 페르소나")
        log.info("   최대 충방전: %.1f kW", max_kw)
        if alfp_decisions:
            log.info(
                "   ALFP seed decisions: ESS %d건 / 거래 %d건 / DR %d건",
                len(alfp_decisions.get("ess_schedule", [])),
                len(alfp_decisions.get("trading_recommendations", [])),
                len(alfp_decisions.get("demand_response_events", [])),
            )

        if getattr(args, "use_cda_negotiation", False) and args.use_cda:
            from seapac_agents.decision import (
                _init_agentscope, PolicyAgentAS, SmartSellerAgentAS,
                StorageMasterAgentAS, EcoSaverAgentAS, _PROMPTS,
            )
            from cda import run_cda_decision_series_with_agents_and_negotiation

            _init_agentscope()
            policy  = PolicyAgentAS(max_charge_kw=max_kw, max_discharge_kw=max_kw)
            seller  = SmartSellerAgentAS()
            storage = StorageMasterAgentAS()
            eco     = EcoSaverAgentAS(peak_threshold_kw=args.peak_threshold)
            decisions = run_cda_decision_series_with_agents_and_negotiation(
                state_json_list, policy, seller, storage, eco,
                state_message_template=_PROMPTS["state_message_template"],
                use_llm_strategy=True,
            )
        elif args.use_cda:
            from seapac_agents.decision import (
                _init_agentscope, PolicyAgentAS, SmartSellerAgentAS,
                StorageMasterAgentAS, EcoSaverAgentAS, _PROMPTS,
            )
            from cda import run_cda_decision_series_with_agents

            _init_agentscope()
            policy  = PolicyAgentAS(max_charge_kw=max_kw, max_discharge_kw=max_kw)
            seller  = SmartSellerAgentAS()
            storage = StorageMasterAgentAS()
            eco     = EcoSaverAgentAS(peak_threshold_kw=args.peak_threshold)
            decisions = run_cda_decision_series_with_agents(
                state_json_list, policy, seller, storage, eco,
                state_message_template=_PROMPTS["state_message_template"],
            )
        else:
            from seapac_agents.decision import run_agentscope_decision_series

            decisions = run_agentscope_decision_series(
                state_json_list,
                peak_threshold_kw=args.peak_threshold,
                max_charge_kw=max_kw,
                max_discharge_kw=max_kw,
            )

        # ALFP seed decisions 병합: 멀티에이전트 결과에 없는 항목은 ALFP 결과로 보완
        if alfp_decisions:
            if not decisions.get("llm_strategy") and alfp_decisions.get("llm_strategy"):
                decisions["llm_strategy"] = alfp_decisions["llm_strategy"]
            if not decisions.get("ess_schedule") and alfp_decisions.get("ess_schedule"):
                decisions["ess_schedule"] = alfp_decisions["ess_schedule"]
                log.info("   ALFP ESS 스케줄 %d건 보완 적용", len(decisions["ess_schedule"]))
            if not decisions.get("demand_response_events") and alfp_decisions.get("demand_response_events"):
                decisions["demand_response_events"] = alfp_decisions["demand_response_events"]
                log.info("   ALFP DR 이벤트 %d건 보완 적용", len(decisions["demand_response_events"]))
            decisions["alfp_seed"] = {
                "ess_schedule_count": len(alfp_decisions.get("ess_schedule", [])),
                "trading_recommendations_count": len(alfp_decisions.get("trading_recommendations", [])),
                "demand_response_events_count": len(alfp_decisions.get("demand_response_events", [])),
            }

        n_ess   = len(decisions.get("ess_schedule", []))
        n_trade = len(decisions.get("trading_recommendations", []))
        n_dr    = len(decisions.get("demand_response_events", []))

        # ESS 액션 분포
        ess_sched = decisions.get("ess_schedule", [])
        charge_n  = sum(1 for e in ess_sched if e.get("action") == "charge")
        disch_n   = sum(1 for e in ess_sched if e.get("action") == "discharge")
        idle_n    = sum(1 for e in ess_sched if e.get("action") == "idle")

        log.info("   [출력] ESS %d건 (충전%d/방전%d/대기%d) / 거래%d건 / DR%d건",
                 n_ess, charge_n, disch_n, idle_n, n_trade, n_dr)

        summary = {
            "ESS 스케줄":    f"{n_ess}건  (충전 {charge_n} / 방전 {disch_n} / 대기 {idle_n})",
            "거래 권고":      f"{n_trade}건",
            "DR 이벤트":      f"{n_dr}건",
            "결정 모드":      "CDA+Negotiation" if getattr(args, "use_cda_negotiation", False) and args.use_cda else ("CDA 시장" if args.use_cda else "AgentScope"),
        }
        if decisions.get("trading_evidence"):
            summary["trading_evidence"] = decisions["trading_evidence"]
            summary["거래 증빙"] = f"{len(decisions.get('trading_evidence') or [])}건"
        if decisions.get("strategy_reasoning_logs"):
            summary["strategy_reasoning_logs"] = decisions["strategy_reasoning_logs"]
        if decisions.get("negotiation_logs"):
            summary["negotiation_logs"] = decisions["negotiation_logs"]

        if args.verbose and n_ess > 0:
            log.debug("   ESS[0]: %s", ess_sched[0])

        stage_r = _stage_end(label, t0, summary)
        mode_label = "CDA+Negotiation" if getattr(args, "use_cda_negotiation", False) and args.use_cda else ("CDA 시장" if args.use_cda else "AgentScope")
        agent_logs = [
            {
                "agent_name": "Policy-Agent",
                "role_label": "ESS·거래·DR 제약 검증 및 클램핑",
                "step_order": 0,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"제약 검증": "완료", "결정 모드": mode_label},
            },
            {
                "agent_name": "SmartSeller-Agent",
                "role_label": "잉여 에너지 판매 전략 수립",
                "step_order": 1,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"거래 권고": f"{n_trade}건", "거래 증빙": summary.get("거래 증빙", "0건")},
            },
            {
                "agent_name": "StorageMaster-Agent",
                "role_label": "ESS 충방전 최적화",
                "step_order": 2,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"ESS 스케줄": summary.get("ESS 스케줄", "—")},
            },
            {
                "agent_name": "EcoSaver-Agent",
                "role_label": "수요반응 절감 전략 생성",
                "step_order": 3,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"DR 이벤트": f"{n_dr}건"},
            },
            {
                "agent_name": "MarketCoordinator-Agent",
                "role_label": "충돌 조정 및 최종 decisions 생성",
                "step_order": 4,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": stage_r.elapsed_sec,
                "ok": True,
                "summary": {"결정 모드": mode_label, "최종 요약": f"ESS {n_ess} / 거래 {n_trade} / DR {n_dr}"},
            },
        ]
        return stage_r, decisions, agent_logs

    except Exception as exc:
        log.exception("Multi-Agent Decision 오류")
        stage_r = _stage_error(label, t0, exc)
        agent_logs = [{
            "agent_name": "MarketCoordinator-Agent",
            "role_label": "다중 에이전트 의사결정 오케스트레이션",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": stage_r.elapsed_sec,
            "ok": False,
            "summary": {},
            "error_text": str(exc),
        }]
        return stage_r, {}, agent_logs


def stage_parallel_agents(
    args: argparse.Namespace,
    decisions: dict,
    state_json_list: list[dict],
) -> tuple[StageResult, dict, list[dict[str, Any]]]:
    """Step3.5 — Final Parallel Execution Layer."""
    label = "Parallel Agents (Policy / EcoSaver / Storage)"
    t0 = _stage_start(label)
    try:
        from parallel_agents import (
            run_parallel_evaluation_and_convert,
            PolicyConfig,
            decisions_to_candidate_bundle,
        )
        from parallel_agents.audit_log import log_parallel_evaluation

        max_kw = min(50.0, args.ess_capacity / 4)
        policy_cfg = PolicyConfig(
            max_charge_kw=max_kw,
            max_discharge_kw=max_kw,
        )

        n_candidates = (
            len(decisions.get("ess_schedule", []))
            + len(decisions.get("trading_recommendations", []))
            + len(decisions.get("demand_response_events", []))
        )
        log.info("   입력:  후보 액션 약 %d건", n_candidates)
        log.info("   최대 충방전: %.1f kW  /  피크 임계: %.0f kW", max_kw, args.peak_threshold)
        log.info("   병렬 실행: asyncio.gather (Policy + EcoSaver + Storage)")

        bundle_for_audit = decisions_to_candidate_bundle(decisions, state_json_list)

        updated_decisions = run_parallel_evaluation_and_convert(
            decisions,
            state_json_list=state_json_list,
            policy_config=policy_cfg,
            peak_threshold_kw=args.peak_threshold,
            max_charge_kw=max_kw,
            max_discharge_kw=max_kw,
            use_async=True,
        )

        pl = updated_decisions.get("parallel_layer") or {}
        approved   = pl.get("approved_actions") or []
        rejected   = pl.get("rejected_actions") or []
        modified   = pl.get("modified_actions") or []
        recs       = pl.get("recommendations") or []
        violations = pl.get("policy_violation_report") or []
        risk_score = pl.get("risk_score", 0.0)

        log.info("   [출력] 승인 %d건 / 거절 %d건 / 수정 %d건",
                 len(approved), len(rejected), len(modified))
        log.info("   권고사항: %d건  /  위반 보고: %d건  /  위험도: %.2f",
                 len(recs), len(violations), risk_score)

        if violations and args.verbose:
            for v in violations[:3]:
                log.debug("   위반: %s", v)

        if args.audit_log:
            log_parallel_evaluation(
                bundle_for_audit, pl, updated_decisions,
                audit_path=args.audit_log,
            )
            log.info("   감사 로그 기록: %s", args.audit_log)

        summary = {
            "승인 액션":     f"{len(approved)}건",
            "거절 액션":     f"{len(rejected)}건",
            "수정 액션":     f"{len(modified)}건",
            "EcoSaver 권고": f"{len(recs)}건",
            "정책 위반":     f"{len(violations)}건",
            "위험 점수":     f"{risk_score:.2f}",
        }

        stage_r = _stage_end(label, t0, summary)
        agent_logs = [
            {
                "agent_name": "Policy-Agent",
                "role_label": "병렬 정책 검증",
                "step_order": 0,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"거절 액션": f"{len(rejected)}건", "정책 위반": f"{len(violations)}건"},
            },
            {
                "agent_name": "EcoSaver-Agent",
                "role_label": "DR 권고 병렬 평가",
                "step_order": 1,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"EcoSaver 권고": f"{len(recs)}건"},
            },
            {
                "agent_name": "StorageMaster-Agent",
                "role_label": "ESS 액션 수정·보정",
                "step_order": 2,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"수정 액션": f"{len(modified)}건", "승인 액션": f"{len(approved)}건"},
            },
            {
                "agent_name": "Parallel Coordinator",
                "role_label": "병렬 평가 결과 취합",
                "step_order": 3,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": stage_r.elapsed_sec,
                "ok": True,
                "summary": summary,
            },
        ]
        return stage_r, updated_decisions, agent_logs

    except Exception as exc:
        log.exception("Parallel Agents 오류")
        stage_r = _stage_error(label, t0, exc)
        agent_logs = [{
            "agent_name": "Parallel Coordinator",
            "role_label": "Policy / EcoSaver / Storage 병렬 평가",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": stage_r.elapsed_sec,
            "ok": False,
            "summary": {},
            "error_text": str(exc),
        }]
        return stage_r, decisions, agent_logs


def stage_execution(
    args: argparse.Namespace,
    decisions: dict,
) -> tuple[StageResult, Any, list[dict[str, Any]]]:
    """Step4 — Action Execution Engine."""
    label = "Action Execution Engine"
    t0 = _stage_start(label)
    try:
        max_kw = min(50.0, args.ess_capacity / 4)
        log.info("   입력:  decisions (ESS %d건 / 거래 %d건 / DR %d건)",
                 len(decisions.get("ess_schedule", [])),
                 len(decisions.get("trading_recommendations", [])),
                 len(decisions.get("demand_response_events", [])))

        if args.use_cda:
            from cda import run_execution
            log.info("   모드:  CDA Settlement Engine")
        else:
            from seapac_agents.execution import run_execution
            log.info("   모드:  Policy Validation → Coordinator Approval → Mesa Update")

        result = run_execution(
            decisions,
            data_path=args.data_path,
            n_steps=args.steps,
            phase=args.phase,
            measure_date=getattr(args, "measure_date", None),
            seed=args.seed,
            ess_capacity_kwh=args.ess_capacity,
            ess_peak_threshold_kw=args.peak_threshold,
            max_charge_kw=max_kw,
            max_discharge_kw=max_kw,
        )

        approved = result.approved
        n_errors = len(result.validation_errors)
        df_shape = result.dataframe.shape if result.dataframe is not None else (0, 0)

        log.info("   [출력] 승인: %s  /  검증 오류: %d건  /  DataFrame: %d×%d",
                 "✓" if approved else "✗", n_errors, df_shape[0], df_shape[1])

        if result.validation_errors and args.verbose:
            for e in result.validation_errors[:5]:
                log.debug("   검증 오류: %s", e)

        # DataFrame 통계
        df = result.dataframe
        ess_ops = 0
        trade_kw = 0.0
        if df is not None:
            if "ess_action" in df.columns:
                ess_ops = df["ess_action"].notna().sum()
            if "market_matched_kw" in df.columns:
                trade_kw = df["market_matched_kw"].sum()

        summary = {
            "실행 승인 여부":    "✓ 승인" if approved else "✗ 미승인",
            "검증 오류":         f"{n_errors}건",
            "결과 DataFrame":   f"{df_shape[0]} rows × {df_shape[1]} cols",
            "ESS 실행 스텝":    f"{ess_ops}건",
            "P2P 거래량 합계":   f"{trade_kw:.1f} kW",
        }

        stage_r = _stage_end(label, t0, summary)
        if args.use_cda:
            agent_logs = [
                {
                    "agent_name": "CDA Market Engine",
                    "role_label": "호가 매칭 및 전력거래 실행",
                    "step_order": 0,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": None,
                    "ok": True,
                    "summary": {"P2P 거래량 합계": f"{trade_kw:.1f} kW"},
                },
                {
                    "agent_name": "Settlement Validator",
                    "role_label": "거래 승인 및 정산 검증",
                    "step_order": 1,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": None,
                    "ok": approved,
                    "summary": {"실행 승인 여부": "✓ 승인" if approved else "✗ 미승인", "검증 오류": f"{n_errors}건"},
                },
                {
                    "agent_name": "Mesa Update",
                    "role_label": "실행 결과를 커뮤니티 상태에 반영",
                    "step_order": 2,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": stage_r.elapsed_sec,
                    "ok": approved,
                    "summary": {"결과 DataFrame": f"{df_shape[0]} rows × {df_shape[1]} cols"},
                },
            ]
        else:
            agent_logs = [
                {
                    "agent_name": "Policy Validator",
                    "role_label": "실행 전 정책 위반 검증",
                    "step_order": 0,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": None,
                    "ok": n_errors == 0,
                    "summary": {"검증 오류": f"{n_errors}건"},
                },
                {
                    "agent_name": "Execution Coordinator",
                    "role_label": "최종 승인 여부 결정",
                    "step_order": 1,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": None,
                    "ok": approved,
                    "summary": {"실행 승인 여부": "✓ 승인" if approved else "✗ 미승인"},
                },
                {
                    "agent_name": "Mesa Update",
                    "role_label": "실행 결과를 커뮤니티 상태에 반영",
                    "step_order": 2,
                    "started_at": _utc_now_str(),
                    "finished_at": _utc_now_str(),
                    "elapsed_sec": stage_r.elapsed_sec,
                    "ok": approved,
                    "summary": {"ESS 실행 스텝": f"{ess_ops}건", "P2P 거래량 합계": f"{trade_kw:.1f} kW"},
                },
            ]

        return stage_r, result, agent_logs

    except Exception as exc:
        log.exception("Action Execution 오류")
        stage_r = _stage_error(label, t0, exc)
        agent_logs = [{
            "agent_name": "Execution Coordinator",
            "role_label": "실행 단계 오케스트레이션",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": stage_r.elapsed_sec,
            "ok": False,
            "summary": {},
            "error_text": str(exc),
        }]
        return stage_r, None, agent_logs


def stage_execution_with_parallel(
    args: argparse.Namespace,
    decisions: dict,
    state_json_list: list[dict],
) -> tuple[list[StageResult], dict, Any, list[list[dict[str, Any]]]]:
    """
    전력거래(Step4)와 Parallel Agents(Step3.5)를 동시 실행.

    Thread A — Step3.5 Parallel Agents (Policy / EcoSaver / Storage)
        · ESS·DR 정책 검증 및 비토 판단
        · asyncio.gather 내부 병렬 실행
    Thread B — Step4 전력거래 실행 (run_execution)
        · P2P 시장 매칭 + MESA 시뮬레이션
        · Parallel Agents 결과를 기다리지 않고 즉시 실행

    병합 규칙:
        · exec_result(Thread B)가 주 실행 결과 (DataFrame, KPI 기반)
        · parallel_layer(Thread A)의 승인·거절·권고를 decisions에 첨부
        · Thread A가 거절한 ESS/DR 액션은 validation_errors에 추가 기록
    """
    label_pa  = "Parallel Agents (Thread A)"
    label_ex  = "전력거래 실행 (Thread B)"
    label_mrg = "병합"

    _divider("=", 72)
    log.info("▷▷ 전력거래 ↔ Parallel Agents 동시 실행 시작")
    _divider("=", 72)

    wall_t0 = time.perf_counter()

    # ── Thread A 작업 정의 ────────────────────────────────────────
    def _run_parallel_agents() -> tuple[dict, dict, float, str]:
        """(updated_decisions, pl_meta, elapsed, error_msg)"""
        t0 = time.perf_counter()
        try:
            from parallel_agents import (
                run_parallel_evaluation_and_convert,
                PolicyConfig,
                decisions_to_candidate_bundle,
            )
            from parallel_agents.audit_log import log_parallel_evaluation

            max_kw = min(50.0, args.ess_capacity / 4)
            policy_cfg = PolicyConfig(max_charge_kw=max_kw, max_discharge_kw=max_kw)

            n_cand = (
                len(decisions.get("ess_schedule", []))
                + len(decisions.get("trading_recommendations", []))
                + len(decisions.get("demand_response_events", []))
            )
            log.info("   [Thread A] Parallel Agents 시작 — 후보 액션 %d건", n_cand)

            bundle_for_audit = decisions_to_candidate_bundle(decisions, state_json_list)
            updated = run_parallel_evaluation_and_convert(
                decisions,
                state_json_list=state_json_list,
                policy_config=policy_cfg,
                peak_threshold_kw=args.peak_threshold,
                max_charge_kw=max_kw,
                max_discharge_kw=max_kw,
                use_async=True,
            )

            pl = updated.get("parallel_layer") or {}
            elapsed = time.perf_counter() - t0
            log.info(
                "   [Thread A] 완료 (%.2fs) — 승인 %d / 거절 %d / 권고 %d",
                elapsed,
                len(pl.get("approved_actions") or []),
                len(pl.get("rejected_actions") or []),
                len(pl.get("recommendations") or []),
            )

            if args.audit_log:
                log_parallel_evaluation(
                    bundle_for_audit, pl, updated, audit_path=args.audit_log
                )
                log.info("   [Thread A] 감사 로그: %s", args.audit_log)

            return updated, pl, elapsed, ""
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            log.error("   [Thread A] 오류 (%.2fs) — %s", elapsed, exc)
            return decisions, {}, elapsed, str(exc)

    # ── Thread B 작업 정의 ────────────────────────────────────────
    def _run_trading_execution() -> tuple[Any, float, str]:
        """(exec_result, elapsed, error_msg)"""
        t0 = time.perf_counter()
        try:
            max_kw = min(50.0, args.ess_capacity / 4)
            log.info(
                "   [Thread B] 전력거래 실행 시작 — ESS %d건 / 거래 %d건 / DR %d건",
                len(decisions.get("ess_schedule", [])),
                len(decisions.get("trading_recommendations", [])),
                len(decisions.get("demand_response_events", [])),
            )

            if args.use_cda:
                from cda import run_execution
            else:
                from seapac_agents.execution import run_execution

            result = run_execution(
                decisions,
                data_path=args.data_path,
                n_steps=args.steps,
                phase=args.phase,
                measure_date=getattr(args, "measure_date", None),
                seed=args.seed,
                ess_capacity_kwh=args.ess_capacity,
                ess_peak_threshold_kw=args.peak_threshold,
                max_charge_kw=max_kw,
                max_discharge_kw=max_kw,
            )

            elapsed = time.perf_counter() - t0
            df = result.dataframe
            trade_kw = (
                df["market_matched_kw"].sum()
                if df is not None and "market_matched_kw" in df.columns
                else 0.0
            )
            log.info(
                "   [Thread B] 완료 (%.2fs) — 승인=%s / 거래 합계 %.1f kW / 검증오류 %d건",
                elapsed,
                "✓" if result.approved else "✗",
                trade_kw,
                len(result.validation_errors),
            )
            return result, elapsed, ""
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            log.error("   [Thread B] 오류 (%.2fs) — %s", elapsed, exc)
            return None, elapsed, str(exc)

    # ── 두 스레드 동시 실행 ───────────────────────────────────────
    log.info("   ⇉ Thread A (Parallel Agents) + Thread B (전력거래) 동시 시작")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="seapac") as executor:
        future_pa: Future = executor.submit(_run_parallel_agents)
        future_ex: Future = executor.submit(_run_trading_execution)
        pa_updated, pl_meta, pa_elapsed, pa_err = future_pa.result()
        exec_result, ex_elapsed, ex_err     = future_ex.result()

    wall_elapsed = time.perf_counter() - wall_t0
    log.info(
        "   ⇇ 두 스레드 완료 — Thread A: %.2fs / Thread B: %.2fs / 총 벽시계: %.2fs",
        pa_elapsed, ex_elapsed, wall_elapsed,
    )

    # ── 결과 병합 ─────────────────────────────────────────────────
    _divider("-", 72)
    log.info("   ▶ 병합: Parallel Agents 검증 결과 → 전력거래 실행 결과에 첨부")

    # decisions에 parallel_layer 메타 첨부
    merged_decisions = dict(pa_updated)  # Thread A의 승인된 decisions 기반
    merged_decisions["parallel_layer"] = pl_meta

    # Thread A가 거절한 액션을 exec_result validation_errors에 추가 기록
    if exec_result is not None:
        rejected = pl_meta.get("rejected_actions") or []
        if rejected:
            extra_errors = [f"[Parallel-Agent 거절] {aid}" for aid in rejected]
            exec_result.validation_errors.extend(extra_errors)
            log.info("   ↳ Parallel Agents 거절 %d건 → validation_errors 에 추가", len(rejected))

        violations = pl_meta.get("policy_violation_report") or []
        if violations:
            exec_result.validation_errors.extend(
                [f"[정책 위반] {v}" for v in violations]
            )
            log.info("   ↳ 정책 위반 %d건 → validation_errors 에 추가", len(violations))

    # ── 각 스레드별 StageResult 생성 ─────────────────────────────
    approved_pa = pl_meta.get("approved_actions") or []
    rejected_pa = pl_meta.get("rejected_actions") or []
    recs_pa     = pl_meta.get("recommendations") or []
    risk_pa     = pl_meta.get("risk_score", 0.0)

    stage_pa = StageResult(
        name=label_pa,
        ok=(pa_err == ""),
        elapsed_sec=pa_elapsed,
        error=pa_err,
        summary={
            "실행 방식":      "asyncio.gather (내부 병렬)",
            "승인 액션":      f"{len(approved_pa)}건",
            "거절 액션":      f"{len(rejected_pa)}건",
            "EcoSaver 권고":  f"{len(recs_pa)}건",
            "위험 점수":      f"{risk_pa:.2f}",
        },
    )

    df_ex = exec_result.dataframe if exec_result else None
    df_shape = df_ex.shape if df_ex is not None else (0, 0)
    trade_kw_ex = (
        df_ex["market_matched_kw"].sum()
        if df_ex is not None and "market_matched_kw" in df_ex.columns
        else 0.0
    )
    stage_ex = StageResult(
        name=label_ex,
        ok=(ex_err == "" and exec_result is not None),
        elapsed_sec=ex_elapsed,
        error=ex_err,
        summary={
            "실행 모드":       "CDA 시장" if args.use_cda else "Policy→MESA",
            "실행 승인":       "✓" if (exec_result and exec_result.approved) else "✗",
            "DataFrame":      f"{df_shape[0]} rows × {df_shape[1]} cols",
            "P2P 거래량 합계": f"{trade_kw_ex:.1f} kW",
        },
    )

    stage_mrg = StageResult(
        name=label_mrg,
        ok=(exec_result is not None),
        elapsed_sec=wall_elapsed,
        summary={
            "병렬 벽시계 총합": f"{wall_elapsed:.2f}s  (A:{pa_elapsed:.2f}s / B:{ex_elapsed:.2f}s)",
            "순차 대비 절감":   f"~{max(pa_elapsed, ex_elapsed) - wall_elapsed:.2f}s",
        },
    )

    _divider("-", 72)
    log.info("◁◁ 전력거래 ↔ Parallel Agents 병합 완료  (%.2fs)", wall_elapsed)

    stage_pa_agents = [
        {
            "agent_name": "Policy-Agent",
            "role_label": "병렬 정책 검증",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": None,
            "ok": pa_err == "",
            "summary": {"거절 액션": f"{len(rejected_pa)}건", "위험 점수": f"{risk_pa:.2f}"},
            "error_text": pa_err or None,
        },
        {
            "agent_name": "EcoSaver-Agent",
            "role_label": "DR 권고 병렬 평가",
            "step_order": 1,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": None,
            "ok": pa_err == "",
            "summary": {"EcoSaver 권고": f"{len(recs_pa)}건"},
            "error_text": pa_err or None,
        },
        {
            "agent_name": "StorageMaster-Agent",
            "role_label": "ESS 액션 수정·보정",
            "step_order": 2,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": None,
            "ok": pa_err == "",
            "summary": {"승인 액션": f"{len(approved_pa)}건", "거절 액션": f"{len(rejected_pa)}건"},
            "error_text": pa_err or None,
        },
        {
            "agent_name": "Parallel Coordinator",
            "role_label": "병렬 평가 결과 취합",
            "step_order": 3,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": pa_elapsed,
            "ok": pa_err == "",
            "summary": stage_pa.summary,
            "error_text": pa_err or None,
        },
    ]

    stage_ex_agents = [
        {
            "agent_name": "CDA Market Engine" if args.use_cda else "Policy Validator",
            "role_label": "호가 매칭 및 전력거래 실행" if args.use_cda else "실행 전 정책 위반 검증",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": None,
            "ok": ex_err == "",
            "summary": {"실행 승인": "✓" if (exec_result and exec_result.approved) else "✗"},
            "error_text": ex_err or None,
        },
        {
            "agent_name": "Settlement Validator" if args.use_cda else "Mesa Update",
            "role_label": "거래 승인 및 정산 검증" if args.use_cda else "실행 결과를 커뮤니티 상태에 반영",
            "step_order": 1,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": ex_elapsed,
            "ok": ex_err == "" and exec_result is not None,
            "summary": stage_ex.summary,
            "error_text": ex_err or None,
        },
    ]

    stage_merge_agents = [
        {
            "agent_name": "Merge Coordinator",
            "role_label": "병렬 검증 결과와 실행 결과 병합",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": wall_elapsed,
            "ok": exec_result is not None,
            "summary": stage_mrg.summary,
            "error_text": None if exec_result is not None else "병합할 실행 결과가 없습니다.",
        }
    ]

    return [stage_pa, stage_ex, stage_mrg], merged_decisions, exec_result, [stage_pa_agents, stage_ex_agents, stage_merge_agents]


def stage_evaluation(
    args: argparse.Namespace,
    result: Any,
    decisions: dict,
    baseline_peak_kw: float,
) -> tuple[StageResult, Any, list[dict[str, Any]]]:
    """Step5 — Evaluation Engine."""
    label = "Evaluation Engine"
    t0 = _stage_start(label)
    try:
        from seapac_agents.evaluation import evaluate_from_execution_result, EvaluationConfig

        log.info("   입력:  ExecutionResult (approved=%s)", result.approved)
        log.info("   기준 피크: %.1f kW  /  계통 단가: %.1f 원/kWh",
                 baseline_peak_kw, args.grid_price)

        eval_cfg = EvaluationConfig(
            grid_price_krw_per_kwh=args.grid_price,
            baseline_peak_kw=baseline_peak_kw,
        )
        report = evaluate_from_execution_result(result, decisions=decisions, config=eval_cfg)
        report.print_report()

        rd = report.to_dict()
        kpis = rd.get("kpis", {})
        energy_cost = kpis.get("energy_cost", {})
        trading_profit = kpis.get("trading_profit", {})
        peak_reduction = kpis.get("peak_reduction", {})
        ess_degradation = kpis.get("ess_degradation", {})
        user_acceptance = kpis.get("user_acceptance", {})

        log.info("   [출력] 에너지 비용: %.0f원  /  거래 수익: %.0f원  /  피크 감소: %.1f%%",
                 energy_cost.get("total_grid_cost_krw", 0),
                 trading_profit.get("community_saving_krw", 0),
                 peak_reduction.get("peak_reduction_pct", 0))

        summary = {
            "에너지 비용":     f"{energy_cost.get('total_grid_cost_krw', 0):,.0f} 원",
            "거래 수익":        f"{trading_profit.get('community_saving_krw', 0):,.0f} 원",
            "피크 감소율":      f"{peak_reduction.get('peak_reduction_pct', 0):.1f} %",
            "ESS 마모 비용":   f"{ess_degradation.get('ess_degradation_cost_krw', 0):,.0f} 원",
            "DR 수락율":       f"{user_acceptance.get('acceptance_rate_pct', 0):.0f} %",
            "종합 등급":        rd.get("grade", "N/A"),
        }

        stage_r = _stage_end(label, t0, summary)
        agent_logs = [
            {
                "agent_name": "Energy Cost Evaluator",
                "role_label": "계통 비용 KPI 계산",
                "step_order": 0,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"에너지 비용": summary["에너지 비용"]},
            },
            {
                "agent_name": "Trading Profit Evaluator",
                "role_label": "거래 수익 KPI 계산",
                "step_order": 1,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"거래 수익": summary["거래 수익"], "피크 감소율": summary["피크 감소율"]},
            },
            {
                "agent_name": "ESS / DR Evaluator",
                "role_label": "ESS 마모·DR 수락률 계산",
                "step_order": 2,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": None,
                "ok": True,
                "summary": {"ESS 마모 비용": summary["ESS 마모 비용"], "DR 수락율": summary["DR 수락율"]},
            },
            {
                "agent_name": "Evaluation Aggregator",
                "role_label": "종합 등급 산정",
                "step_order": 3,
                "started_at": _utc_now_str(),
                "finished_at": _utc_now_str(),
                "elapsed_sec": stage_r.elapsed_sec,
                "ok": True,
                "summary": {"종합 등급": summary["종합 등급"]},
            },
        ]
        return stage_r, report, agent_logs

    except Exception as exc:
        log.exception("Evaluation 오류")
        stage_r = _stage_error(label, t0, exc)
        agent_logs = [{
            "agent_name": "Evaluation Aggregator",
            "role_label": "KPI 계산 및 등급 산정",
            "step_order": 0,
            "started_at": _utc_now_str(),
            "finished_at": _utc_now_str(),
            "elapsed_sec": stage_r.elapsed_sec,
            "ok": False,
            "summary": {},
            "error_text": str(exc),
        }]
        return stage_r, None, agent_logs


# ─────────────────────────────────────────────────────────────────
# 결과 저장
# ─────────────────────────────────────────────────────────────────

def _save_outputs(
    args: argparse.Namespace,
    alfp_result: dict | None,
    state_json_list: list[dict],
    decisions: dict,
    exec_result: Any,
    eval_report: Any,
    pipeline_result: PipelineResult,
    run_id: int | None = None,
    db_path: Path | None = None,
) -> None:
    if not _DASHBOARD_AVAILABLE or run_id is None or db_path is None:
        log.info("   결과 파일 저장 건너뜀: DB run context 없음")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    meta = {
        "run_timestamp": ts,
        "args": {k: v for k, v in vars(args).items()},
        "stages": [
            {
                "name": s.name,
                "ok": s.ok,
                "elapsed_sec": round(s.elapsed_sec, 3),
                "summary": s.summary,
                "error": s.error,
            }
            for s in pipeline_result.stages
        ],
        "total_elapsed_sec": round(pipeline_result.total_elapsed_sec, 3),
        "ok": pipeline_result.ok,
    }
    upsert_artifact(run_id, "pipeline_meta", meta, db_path=db_path)
    upsert_artifact(run_id, "multi_agent_decisions", decisions, db_path=db_path)

    if alfp_result:
        alfp_snapshot = _build_alfp_dashboard_snapshot(alfp_result)
        upsert_artifact(run_id, "alfp_result", alfp_snapshot, db_path=db_path)

    if eval_report is not None:
        upsert_artifact(run_id, "evaluation_report", eval_report.to_dict(), db_path=db_path)

    if decisions.get("cda_trades") is not None or decisions.get("cda_snapshot"):
        snapshot = decisions.get("cda_snapshot") or {}
        cda_execution = {
            "order_book": {
                "bids": snapshot.get("bids", []),
                "asks": snapshot.get("asks", []),
                "snapshot_time": snapshot.get("time", ""),
            },
            "matching": {
                "trades": decisions.get("cda_trades", []),
                "total_trades": len(decisions.get("cda_trades", [])),
                "total_quantity_kw": round(
                    sum(float(t.get("quantity_kw", 0)) for t in (decisions.get("cda_trades") or [])), 2
                ),
            },
            "buyer": {
                "bids": snapshot.get("bids", []),
                "description": "Deficit / Market Price / Peak Risk 기반 구매 입찰 (CommunityBuyer 등)",
            },
            "settlement": None,
        }
        if exec_result is not None:
            cda_execution["settlement"] = {
                "approved": getattr(exec_result, "approved", True),
                "validation_errors": getattr(exec_result, "validation_errors", []) or [],
                "summary": getattr(exec_result, "summary", None) or {},
            }
        upsert_artifact(run_id, "cda_execution", cda_execution, db_path=db_path)

    if exec_result is not None and exec_result.dataframe is not None:
        upsert_artifact(
            run_id,
            "execution_timeseries",
            exec_result.dataframe.to_dict(orient="records"),
            db_path=db_path,
        )

    log.info("   결과 아티팩트 저장: run_id=%s db=%s", run_id, db_path)


def _update_strategy_feedback(
    args: argparse.Namespace,
    decisions: dict,
    exec_result: Any,
    eval_report: Any,
) -> None:
    """Step4/5 실제 실행 결과를 최신 strategy memory entry에 반영한다."""
    try:
        from alfp.memory import update_latest_strategy_actual_result
    except Exception:
        return

    if exec_result is None:
        return

    kpis = {}
    grade = None
    if eval_report is not None and hasattr(eval_report, "to_dict"):
        eval_dict = eval_report.to_dict()
        kpis = eval_dict.get("kpis", {}) or {}
        grade = eval_dict.get("grade")

    trading_profit = kpis.get("trading_profit", {}) or {}
    peak_reduction = kpis.get("peak_reduction", {}) or {}
    ess_degradation = kpis.get("ess_degradation", {}) or {}
    actual_result = {
        "execution_summary": getattr(exec_result, "summary", {}) or {},
        "trading_feedback": {
            "total_trades": trading_profit.get("total_trades", 0),
            "total_matched_kwh": trading_profit.get("total_matched_kwh", 0),
            "community_saving_krw": trading_profit.get("community_saving_krw", 0),
        },
        "peak_feedback": {
            "peak_reduction_pct": peak_reduction.get("peak_reduction_pct", 0),
        },
        "ess_feedback": {
            "ess_degradation_cost_krw": ess_degradation.get("ess_degradation_cost_krw", 0),
            "total_dr_reduction_kwh": (getattr(exec_result, "summary", {}) or {}).get("total_dr_reduction_kwh", 0),
        },
        "decision_feedback": {
            "trading_recommendations": len(decisions.get("trading_recommendations") or []),
            "demand_response_events": len(decisions.get("demand_response_events") or []),
        },
        "grade": grade,
    }
    performance_score = {
        "A": 0.95,
        "B": 0.80,
        "C": 0.60,
        "D": 0.35,
    }.get(str(grade or "").upper(), None)

    prosumer_ids = list(args.prosumers or [])
    if not prosumer_ids:
        prosumer_ids = [args.prosumer]
    for prosumer_id in prosumer_ids:
        update_latest_strategy_actual_result(
            prosumer_id,
            actual_result=actual_result,
            performance_score=performance_score,
        )


def _df_preview(df: Any, columns: list[str] | None = None, limit: int = 12) -> list[dict[str, Any]]:
    if df is None or not hasattr(df, "to_dict"):
        return []
    work = df.copy()
    if columns:
        available = [c for c in columns if c in getattr(work, "columns", [])]
        if available:
            work = work[available]
    if hasattr(work, "head"):
        work = work.head(limit)
    records = work.to_dict(orient="records")
    return [{k: v for k, v in row.items()} for row in records]


def _build_alfp_dashboard_snapshot(alfp_result: dict[str, Any]) -> dict[str, Any]:
    plan = alfp_result.get("forecast_plan") or {}
    metrics = alfp_result.get("validation_metrics") or {}
    feature_df = alfp_result.get("feature_df")
    load_df = alfp_result.get("load_forecast")
    pv_df = alfp_result.get("pv_forecast")
    net_df = alfp_result.get("net_load_forecast")
    messages = alfp_result.get("messages") or []

    return {
        "framework": {
            "name": "LangChain DeepAgent",
            "pipeline": [
                "data_loader",
                "data_quality",
                "feature_engineering",
                "forecast_planner",
                "load_forecast",
                "pv_forecast",
                "net_load_forecast",
                "validation",
            ],
            "execution_mode": alfp_result.get("execution_mode") or "forecast_only",
            "llm_used_in_forecast_planner": bool(plan.get("llm_used")),
            "llm_reasoning": plan.get("llm_reasoning", ""),
            "message_count": len(messages),
        },
        "input_data": {
            "prosumer_id": plan.get("prosumer_id"),
            "prosumer_type": plan.get("prosumer_type"),
            "data_range_days": plan.get("data_range_days"),
            "n_train_records": plan.get("n_train_records"),
            "feature_names": (alfp_result.get("feature_names") or [])[:24],
            "feature_sample": _df_preview(
                feature_df,
                columns=[
                    "timestamp", "prosumer_id", "prosumer_type", "load_kw", "pv_kw",
                    "price_buy", "price_sell", "weather_temp_c", "weather_clouds_pct",
                ],
                limit=12,
            ),
        },
        "forecast_plan": plan,
        "validation_metrics": metrics,
        "forecast_outputs": {
            "load_forecast": _df_preview(load_df, ["timestamp", "load_kw", "predicted_load_kw"], limit=24),
            "pv_forecast": _df_preview(pv_df, ["timestamp", "pv_kw", "predicted_pv_kw"], limit=24),
            "net_load_forecast": _df_preview(
                net_df,
                ["timestamp", "load_kw", "pv_kw", "actual_net_load_kw", "predicted_net_load_kw"],
                limit=24,
            ),
        },
    }
    log.info("   JSON 저장: %s", path)


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────


def _record_stage(run_id: int, order: int, stage_r: StageResult, db_path: Path | None) -> None:
    """Persist one stage to dashboard DB if available."""
    if not _DASHBOARD_AVAILABLE or run_id is None:
        return
    add_stage(
        run_id,
        stage_order=order,
        stage_name=stage_r.name,
        ok=stage_r.ok,
        elapsed_sec=stage_r.elapsed_sec,
        summary=stage_r.summary,
        error_text=stage_r.error or None,
        db_path=db_path,
    )


def main() -> None:
    args = _parse_args()
    from alfp.llm import set_llm_mode, get_llm_mode
    set_llm_mode(args.llm_mode)
    _setup_logger(log_file=args.log_file, verbose=args.verbose)

    pipeline = PipelineResult()
    pipeline_t0 = time.perf_counter()
    run_id: int | None = None
    db_path: Path | None = get_db_path(os.environ.get("PIPELINE_DB_DIR")) if _DASHBOARD_AVAILABLE else None
    if _DASHBOARD_AVAILABLE and db_path is not None:
        init_db(db_path)
        existing_run_id = os.environ.get("PIPELINE_RUN_ID")
        if existing_run_id:
            run_id = int(existing_run_id)
            log.info("  Dashboard DB: 기존 run_id 사용 (UI 실행) run_id=%s  path=%s", run_id, db_path)
        else:
            run_id = create_run(args, db_path=db_path)
            log.info("  Dashboard DB: run_id=%s  path=%s", run_id, db_path)

    # 다중 프로슈머 목록 확정 (--prosumers 우선, 없으면 --prosumer 단일값)
    multi_prosumers: list[str] = args.prosumers if args.prosumers else []
    is_p2p_mode = len(multi_prosumers) > 1

    _divider("=", 72)
    log.info("  SEAPAC Full Integrated Pipeline")
    log.info("  실행 시각: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("  Phase=%d  Steps=%d  Parallel=%s  SkipALFP=%s",
             args.phase, args.steps, args.use_parallel, args.skip_alfp)
    log.info("  LLM mode=%s", get_llm_mode())
    log.info("  ALFP mode=%s", args.alfp_mode)
    if is_p2p_mode:
        log.info("  ★ P2P 거래 모드: 프로슈머 %d명 병렬 ALFP 실행", len(multi_prosumers))
        log.info("    참여 프로슈머: %s", multi_prosumers)
    _divider("=", 72)

    stage_order = 0

    # ── [ALFP] ────────────────────────────────────────────────────
    alfp_result: dict | None = None
    alfp_decisions: dict = {}

    if not args.skip_alfp:
        if run_id is not None and db_path is not None:
            log.info("  ALFP Agent 단계 로깅 활성화 run_id=%s  db_path=%s", run_id, db_path)
        else:
            log.warning("  ALFP Agent 단계 로깅 비활성화 (run_id=%s, db_path=%s) — Dashboard에서 실행 시에만 기록됨", run_id, db_path)

        if is_p2p_mode:
            # ── P2P 거래 모드: 다중 프로슈머 병렬 ALFP ────────────
            stage_r, alfp_decisions, alfp_result = stage_alfp_multi(
                args, multi_prosumers, run_id=run_id, db_path=db_path
            )
        else:
            # ── 단일 프로슈머 모드 ─────────────────────────────────
            stage_r, alfp_decisions, alfp_result = stage_alfp(args, run_id=run_id, db_path=db_path)

        pipeline.add(stage_r)
        stage_order += 1
        _record_stage(run_id, stage_order, stage_r, db_path)
        if not stage_r.ok:
            log.warning("ALFP 실패 → rule-based fallback으로 계속합니다.")
            alfp_decisions = {}
    else:
        log.info("◎ ALFP 단계 건너뜀 (--skip-alfp)")

    if args.alfp_mode == "forecast_only" and not args.skip_alfp:
        stage_r, forecast_agent_decisions = stage_agent_plan_from_alfp(args, alfp_result or {})
        pipeline.add(stage_r)
        stage_order += 1
        _record_stage(run_id, stage_order, stage_r, db_path)
        if stage_r.ok:
            alfp_decisions = forecast_agent_decisions
        pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
        if _DASHBOARD_AVAILABLE and run_id is not None:
            finish_run(run_id, pipeline.total_elapsed_sec, ok=pipeline.ok, db_path=db_path)
        _save_outputs(
            args,
            alfp_result,
            [],
            alfp_decisions,
            None,
            None,
            pipeline,
            run_id=run_id,
            db_path=db_path,
        )
        pipeline.print_summary()
        return

    state_json_list: list[dict] = []
    baseline_peak_kw = 0.0

    # ── AgentScope Multi-Agent Decision ──────────────────────────
    stage_r, decisions, stage_agent_logs = stage_multi_agent_decision(
        args, state_json_list, alfp_decisions=alfp_decisions or None
    )
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)
    _record_stage_agent_logs(run_id, stage_order, stage_agent_logs, db_path)
    if not stage_r.ok:
        log.error("Multi-Agent Decision 실패 — 파이프라인 중단")
        pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
        if _DASHBOARD_AVAILABLE and run_id is not None:
            finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
        pipeline.print_summary()
        sys.exit(1)

    # ── Parallel Agents ↔ 전력거래 동시 실행 (선택) ──────────────
    if args.use_parallel:
        stages, decisions, exec_result, stage_agent_logs_by_stage = stage_execution_with_parallel(
            args, decisions, state_json_list
        )
        for idx, s in enumerate(stages):
            pipeline.add(s)
            stage_order += 1
            _record_stage(run_id, stage_order, s, db_path)
            _record_stage_agent_logs(run_id, stage_order, stage_agent_logs_by_stage[idx], db_path)
        if exec_result is None:
            log.error("전력거래 실행 실패 — 파이프라인 중단")
            pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
            if _DASHBOARD_AVAILABLE and run_id is not None:
                finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
            pipeline.print_summary()
            sys.exit(1)
    else:
        # ── Action Execution Engine (순차) ────────────────────────
        stage_r, exec_result, stage_agent_logs = stage_execution(args, decisions)
        pipeline.add(stage_r)
        stage_order += 1
        _record_stage(run_id, stage_order, stage_r, db_path)
        _record_stage_agent_logs(run_id, stage_order, stage_agent_logs, db_path)
        if not stage_r.ok or exec_result is None:
            log.error("Action Execution 실패 — 파이프라인 중단")
            pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
            if _DASHBOARD_AVAILABLE and run_id is not None:
                finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
            pipeline.print_summary()
            sys.exit(1)

    # ── Evaluation Engine ─────────────────────────────────────────
    stage_r, eval_report, stage_agent_logs = stage_evaluation(args, exec_result, decisions, baseline_peak_kw)
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)
    _record_stage_agent_logs(run_id, stage_order, stage_agent_logs, db_path)

    # ── 다음 라운드 전략 업데이트용 실제 피드백 저장 ──────────────
    _update_strategy_feedback(args, decisions, exec_result, eval_report)

    # ── 결과 저장 ─────────────────────────────────────────────────
    pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
    if _DASHBOARD_AVAILABLE and run_id is not None:
        finish_run(run_id, pipeline.total_elapsed_sec, ok=pipeline.ok, db_path=db_path)

    _divider()
    log.info("▷ 결과 저장")
    _divider()
    _save_outputs(
        args, alfp_result, state_json_list,
        decisions, exec_result, eval_report, pipeline,
        run_id=run_id,
        db_path=db_path,
    )

    # ── 최종 요약 ─────────────────────────────────────────────────
    pipeline.print_summary()


if __name__ == "__main__":
    main()
