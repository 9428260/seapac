"""
SEAPAC Full Integrated Pipeline
================================
전체 아키텍처를 통합하여 실행하는 메인 스크립트.

Architecture (기본):
  [ALFP decision]
        ↓
  [MESA Simulation Engine]
        ↓
  Step2  State Translator
        ↓
  Step3  AgentScope Multi-Agent Decision
        ↓
  Step4  Action Execution Engine
        ↓
  Step5  Evaluation Engine

Architecture (--use-parallel, 전력거래와 Parallel Agents 동시 실행):
  [ALFP decision]
        ↓
  [MESA Simulation Engine]
        ↓
  Step2  State Translator
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
  python run_full_pipeline.py --prosumer bus_48_Commercial --save-json --log-file output/run.log
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
        finish_run,
    )
    _DASHBOARD_AVAILABLE = True
except ImportError as e:
    get_db_path = init_db = create_run = add_stage = finish_run = None  # type: ignore[misc, assignment]
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
    # 출력
    p.add_argument("--output-dir",  default=None, help="결과 저장 디렉토리")
    p.add_argument("--save-json",   action="store_true", help="JSON 파일로 결과 저장")
    p.add_argument("--log-file",    default=None, help="로그 파일 경로 (미지정 시 logs/pipeline_YYYYMMDD_HHMMSS.log)")
    p.add_argument("--log-dir",     default="logs", help="시간별 로그 파일을 저장할 디렉토리 (--log-file 미지정 시 사용)")
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
) -> tuple[StageResult, dict]:
    """[ALFP decision] — LangGraph 부하 예측 및 운영 의사결정. run_id/db_path 있으면 Agent별 단계를 DB에 기록."""
    label = "[ALFP] 부하 예측 및 운영 의사결정"
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

        llm_strat = decisions.get("llm_strategy", {})
        alert_lv  = llm_strat.get("alert_level", "N/A")
        ess_strat = llm_strat.get("ess_strategy", "")[:80] if llm_strat.get("ess_strategy") else ""

        summary = {
            "ESS 스케줄 건수":      n_ess,
            "거래 권고 건수":        n_trade,
            "DR 이벤트 건수":        n_dr,
            "예측 MAPE":            f"{mape_val:.2f}%  (KPI {'✓' if mape_ok is True else '✗' if mape_ok is False else mape_ok})",
            "LLM 경보 수준":        alert_lv,
            "LLM ESS 전략 (요약)":  ess_strat or "(없음)",
        }

        log.debug("   ALFP decisions keys: %s", list(decisions.keys()))
        if args.verbose and n_ess > 0:
            log.debug("   ESS 스케줄[0]: %s", decisions["ess_schedule"][0])

        return _stage_end(label, t0, summary), decisions

    except Exception as exc:
        log.exception("ALFP 실행 오류")
        return _stage_error(label, t0, exc), {}


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
) -> tuple["StageResult", dict]:
    """
    [ALFP Multi-Prosumer] — 선택된 프로슈머 각각에 대해 ALFP를 병렬 실행하고 decisions를 병합.
    P2P 거래가 가능하도록 각 프로슈머의 ESS·거래·DR 계획을 통합.
    """
    label = f"[ALFP] 다중 프로슈머 병렬 의사결정 ({len(prosumers)}명)"
    t0 = _stage_start(label)
    log.info("   P2P 거래 모드: 프로슈머 %s", prosumers)

    try:
        from alfp.main import run as alfp_run

        def _run_one(prosumer_id: str) -> tuple[str, dict, bool]:
            """(prosumer_id, decisions, ok)"""
            log.info("   [ALFP/%s] 시작", prosumer_id)
            try:
                result = alfp_run(
                    prosumer_id=prosumer_id,
                    data_path=args.data_path,
                    forecast_horizon=args.steps,
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
                return prosumer_id, dec, True
            except Exception as e:
                log.error("   [ALFP/%s] 오류: %s (%s)", prosumer_id, e, type(e).__name__)
                log.exception("   [ALFP/%s] 상세:", prosumer_id)
                return prosumer_id, {}, False

        # 프로슈머별 ALFP 병렬 실행
        with ThreadPoolExecutor(max_workers=len(prosumers), thread_name_prefix="alfp") as ex:
            futures = [ex.submit(_run_one, p) for p in prosumers]
            raw_results = [f.result() for f in futures]

        ok_results = [(pid, dec) for pid, dec, ok in raw_results if ok]
        fail_ids = [pid for pid, _, ok in raw_results if not ok]

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

        return _stage_end(label, t0, summary), merged

    except Exception as exc:
        log.exception("ALFP 다중 프로슈머 실행 오류")
        return _stage_error(label, t0, exc), {}


def stage_mesa(
    args: argparse.Namespace,
    alfp_decisions: dict | None,
) -> tuple[StageResult, Any, Any]:
    """[MESA Simulation Engine] — 커뮤니티 멀티 에이전트 시뮬레이션."""
    label = "[MESA] 시뮬레이션 실행"
    t0 = _stage_start(label)
    try:
        from simulation.model import ALFPSimulationModel

        using_decisions = bool(alfp_decisions)
        measure_date = getattr(args, "measure_date", None) or os.environ.get("PIPELINE_MEASURE_DATE")
        log.info("   Phase:    %d", args.phase)
        log.info("   Steps:    %d", args.steps)
        if measure_date:
            log.info("   기준일자: %s", measure_date)
        log.info("   ESS:      %.0f kWh  (피크 임계 %.0f kW)", args.ess_capacity, args.peak_threshold)
        log.info("   ALFP 연동: %s", "✓ decisions 주입" if using_decisions else "✗ rule-based fallback")

        model = ALFPSimulationModel(
            phase=args.phase,
            data_path=args.data_path,
            n_steps=args.steps,
            measure_date=measure_date,
            seed=args.seed,
            ess_capacity_kwh=args.ess_capacity,
            ess_peak_threshold_kw=args.peak_threshold,
            alfp_decisions=alfp_decisions if using_decisions else None,
        )
        df = model.run()

        # 통계
        load_max  = df["community_load_kw"].max()  if "community_load_kw"  in df.columns else float("nan")
        load_mean = df["community_load_kw"].mean() if "community_load_kw"  in df.columns else float("nan")
        pv_mean   = df["community_pv_kw"].mean()   if "community_pv_kw"    in df.columns else float("nan")
        net_mean  = df["community_net_kw"].mean()  if "community_net_kw"   in df.columns else float("nan")
        mape_mean = df["avg_forecast_mape"].mean() if "avg_forecast_mape"  in df.columns else float("nan")
        ess_mean  = df["ess_soc_pct"].mean()       if "ess_soc_pct"        in df.columns else float("nan")
        trade_sum = df["market_matched_kw"].sum()  if "market_matched_kw"  in df.columns else float("nan")

        log.info("   [입력] 데이터: %s  (Phase %d, %d steps)", args.data_path, args.phase, len(df))

        summary = {
            "시뮬레이션 스텝":       len(df),
            "커뮤니티 최대 부하":    f"{load_max:.1f} kW",
            "커뮤니티 평균 부하":    f"{load_mean:.1f} kW",
            "평균 PV 발전":         f"{pv_mean:.1f} kW",
            "평균 Net Load":        f"{net_mean:.1f} kW",
            "평균 예측 MAPE":       f"{mape_mean:.2f} %",
            "ESS 평균 SoC":         f"{ess_mean:.1f} %" if not (ess_mean != ess_mean) else "N/A (Phase<3)",
            "P2P 거래량 합계":       f"{trade_sum:.1f} kW" if not (trade_sum != trade_sum) else "N/A (Phase<4)",
            "ALFP decisions 연동":  "✓" if using_decisions else "✗",
        }

        log.debug("   DataFrame columns: %s", list(df.columns))

        return _stage_end(label, t0, summary), df, model

    except Exception as exc:
        log.exception("MESA 시뮬레이션 오류")
        return _stage_error(label, t0, exc), None, None


def stage_state_translator(
    args: argparse.Namespace,
    df: Any,
) -> tuple[StageResult, list[dict]]:
    """Step2 — State Translator."""
    label = "Step2  State Translator"
    t0 = _stage_start(label)
    try:
        from seapac_agents.state_translator import translate_dataframe, generate_summary

        log.info("   입력:  DataFrame %d rows × %d cols", df.shape[0], df.shape[1])
        log.info("   피크 임계: %.0f kW  /  ESS 용량: %.0f kWh", args.peak_threshold, args.ess_capacity)

        state_json_list = translate_dataframe(
            df,
            peak_threshold_kw=args.peak_threshold,
            ess_capacity_kwh=args.ess_capacity,
        )

        # 첫 / 마지막 스텝 요약
        first_summary = generate_summary(state_json_list[0])  if state_json_list else ""
        last_summary  = generate_summary(state_json_list[-1]) if state_json_list else ""

        # 피크 리스크 통계
        peak_risks = [s.get("community_state", {}).get("peak_risk", "LOW") for s in state_json_list]
        high_risk_steps = sum(1 for r in peak_risks if r == "HIGH")

        log.info("   [출력] state JSON %d 스텝 생성", len(state_json_list))
        log.info("   첫 스텝: %s", first_summary)
        log.info("   끝 스텝: %s", last_summary)

        summary = {
            "생성된 state JSON 수":  len(state_json_list),
            "첫 스텝 요약":          first_summary[:80] if first_summary else "",
            "끝 스텝 요약":          last_summary[:80]  if last_summary  else "",
            "피크 위험 스텝 (≥0.7)": high_risk_steps,
        }

        if args.verbose and state_json_list:
            log.debug("   state[0] 샘플:\n%s", json.dumps(state_json_list[0], indent=2, ensure_ascii=False)[:600])

        return _stage_end(label, t0, summary), state_json_list

    except Exception as exc:
        log.exception("State Translator 오류")
        return _stage_error(label, t0, exc), []


def stage_multi_agent_decision(
    args: argparse.Namespace,
    state_json_list: list[dict],
) -> tuple[StageResult, dict]:
    """Step3 — AgentScope Multi-Agent Decision."""
    label = "Step3  AgentScope Multi-Agent Decision"
    t0 = _stage_start(label)
    try:
        max_kw = min(50.0, args.ess_capacity / 4)
        log.info("   입력:  %d 스텝 state JSON", len(state_json_list))
        log.info("   모드:  %s", "CDA 시장" if args.use_cda else "AgentScope 페르소나")
        log.info("   최대 충방전: %.1f kW", max_kw)

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
        if decisions.get("strategy_reasoning_logs"):
            summary["strategy_reasoning_logs"] = decisions["strategy_reasoning_logs"]
        if decisions.get("negotiation_logs"):
            summary["negotiation_logs"] = decisions["negotiation_logs"]

        if args.verbose and n_ess > 0:
            log.debug("   ESS[0]: %s", ess_sched[0])

        return _stage_end(label, t0, summary), decisions

    except Exception as exc:
        log.exception("Multi-Agent Decision 오류")
        return _stage_error(label, t0, exc), {}


def stage_parallel_agents(
    args: argparse.Namespace,
    decisions: dict,
    state_json_list: list[dict],
) -> tuple[StageResult, dict]:
    """Step3.5 — Final Parallel Execution Layer."""
    label = "Step3.5 Parallel Agents (Policy / EcoSaver / Storage)"
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

        return _stage_end(label, t0, summary), updated_decisions

    except Exception as exc:
        log.exception("Parallel Agents 오류")
        return _stage_error(label, t0, exc), decisions


def stage_execution(
    args: argparse.Namespace,
    decisions: dict,
) -> tuple[StageResult, Any]:
    """Step4 — Action Execution Engine."""
    label = "Step4  Action Execution Engine"
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

        return _stage_end(label, t0, summary), result

    except Exception as exc:
        log.exception("Action Execution 오류")
        return _stage_error(label, t0, exc), None


def stage_execution_with_parallel(
    args: argparse.Namespace,
    decisions: dict,
    state_json_list: list[dict],
) -> tuple[list[StageResult], dict, Any]:
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
    label_pa  = "Step3.5 Parallel Agents (Thread A)"
    label_ex  = "Step4  전력거래 실행   (Thread B)"
    label_mrg = "Step3.5+4 병합"

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

    return [stage_pa, stage_ex, stage_mrg], merged_decisions, exec_result


def stage_evaluation(
    args: argparse.Namespace,
    result: Any,
    decisions: dict,
    baseline_peak_kw: float,
) -> tuple[StageResult, Any]:
    """Step5 — Evaluation Engine."""
    label = "Step5  Evaluation Engine"
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

        log.info("   [출력] 에너지 비용: %.0f원  /  거래 수익: %.0f원  /  피크 감소: %.1f%%",
                 kpis.get("energy_cost_krw", 0),
                 kpis.get("trading_profit_krw", 0),
                 kpis.get("peak_reduction_pct", 0))

        summary = {
            "에너지 비용":     f"{kpis.get('energy_cost_krw', 0):,.0f} 원",
            "거래 수익":        f"{kpis.get('trading_profit_krw', 0):,.0f} 원",
            "피크 감소율":      f"{kpis.get('peak_reduction_pct', 0):.1f} %",
            "ESS 마모 비용":   f"{kpis.get('ess_degradation_cost_krw', 0):,.0f} 원",
            "DR 수락율":       f"{kpis.get('dr_acceptance_rate', 0)*100:.0f} %",
            "종합 등급":        rd.get("grade", "N/A"),
        }

        return _stage_end(label, t0, summary), report

    except Exception as exc:
        log.exception("Evaluation 오류")
        return _stage_error(label, t0, exc), None


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
) -> None:
    out_dir = Path(args.output_dir or "output")
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.save_json:
        # 파이프라인 요약 메타
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
        _write_json(out_dir / f"pipeline_meta_{ts}.json", meta)

        # State JSON
        _write_json(out_dir / "state_translations.json", state_json_list)

        # Decisions
        _write_json(out_dir / "multi_agent_decisions.json", decisions)

        # Evaluation report (공통 파일 + run별 파일로 Dashboard에서 run_id로 조회 가능)
        if eval_report is not None:
            _write_json(out_dir / "evaluation_report.json", eval_report.to_dict())
            if run_id is not None:
                _write_json(out_dir / f"run_{run_id}_evaluation_report.json", eval_report.to_dict())

        # CDA 실행 탭용 스냅샷 (Order Book, Matching, Buyer, Settlement)
        if run_id is not None and (decisions.get("cda_trades") is not None or decisions.get("cda_snapshot")):
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
            _write_json(out_dir / f"run_{run_id}_cda_execution.json", cda_execution)

    # Timeseries CSV
    if exec_result is not None and exec_result.dataframe is not None:
        csv_path = out_dir / "execution_timeseries.csv"
        exec_result.dataframe.to_csv(csv_path, index=False)
        log.info("   시계열 CSV 저장: %s", csv_path)

    log.info("   출력 디렉토리: %s", out_dir)


def _write_json(path: Path, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    log.info("   JSON 저장: %s", path)


# ─────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────

def _save_mesa_trajectory(run_id: int, df: Any, args: argparse.Namespace) -> None:
    """Dashboard MESA 그리드/궤적 화면용 스텝별 지표 JSON 저장."""
    out_dir = Path(args.output_dir or "output")
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run_id}_mesa_trajectory.json"
    try:
        df.to_json(path, orient="records", date_format="iso", default_handler=str)
        log.info("   MESA 궤적 저장: %s", path)
    except Exception as e:
        log.warning("   MESA 궤적 저장 실패: %s", e)


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
    # 로그 파일 미지정 시 logs 디렉토리에 실행 시각 기준 파일 생성
    if args.log_file is None:
        log_dir = Path(args.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        args.log_file = str(log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    _setup_logger(log_file=args.log_file, verbose=args.verbose)

    pipeline = PipelineResult()
    pipeline_t0 = time.perf_counter()
    run_id: int | None = None
    db_path: Path | None = get_db_path(Path(args.output_dir or "output")) if _DASHBOARD_AVAILABLE else None
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
    if is_p2p_mode:
        log.info("  ★ P2P 거래 모드: 프로슈머 %d명 병렬 ALFP → 통합 MESA 실행", len(multi_prosumers))
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
            stage_r, alfp_decisions = stage_alfp_multi(
                args, multi_prosumers, run_id=run_id, db_path=db_path
            )
        else:
            # ── 단일 프로슈머 모드 ─────────────────────────────────
            stage_r, alfp_decisions = stage_alfp(args, run_id=run_id, db_path=db_path)

        pipeline.add(stage_r)
        stage_order += 1
        _record_stage(run_id, stage_order, stage_r, db_path)
        alfp_result = alfp_decisions  # reference for saving
        if not stage_r.ok:
            log.warning("ALFP 실패 → rule-based fallback으로 계속합니다.")
            alfp_decisions = {}
    else:
        log.info("◎ ALFP 단계 건너뜀 (--skip-alfp)")

    # ── [MESA] ────────────────────────────────────────────────────
    stage_r, df, model = stage_mesa(args, alfp_decisions or None)
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)
    # Dashboard용: run_id가 있으면 MESA 궤적(스텝별 지표) JSON 저장 → 그리드/궤적 화면에서 사용
    if run_id is not None and df is not None:
        _save_mesa_trajectory(run_id, df, args)
    if not stage_r.ok or df is None:
        log.error("MESA 실패 — 파이프라인 중단")
        pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
        if _DASHBOARD_AVAILABLE and run_id is not None:
            finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
        pipeline.print_summary()
        sys.exit(1)

    baseline_peak_kw = float(df["community_load_kw"].max()) if "community_load_kw" in df.columns else 0.0

    # ── Step2 State Translator ────────────────────────────────────
    stage_r, state_json_list = stage_state_translator(args, df)
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)
    if not stage_r.ok or not state_json_list:
        log.error("State Translator 실패 — 파이프라인 중단")
        pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
        if _DASHBOARD_AVAILABLE and run_id is not None:
            finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
        pipeline.print_summary()
        sys.exit(1)

    # ── Step3 AgentScope Multi-Agent Decision ─────────────────────
    stage_r, decisions = stage_multi_agent_decision(args, state_json_list)
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)
    if not stage_r.ok:
        log.error("Multi-Agent Decision 실패 — 파이프라인 중단")
        pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
        if _DASHBOARD_AVAILABLE and run_id is not None:
            finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
        pipeline.print_summary()
        sys.exit(1)

    # ── Step3.5 + Step4: Parallel Agents ↔ 전력거래 동시 실행 (선택) ──
    if args.use_parallel:
        stages, decisions, exec_result = stage_execution_with_parallel(
            args, decisions, state_json_list
        )
        for s in stages:
            pipeline.add(s)
            stage_order += 1
            _record_stage(run_id, stage_order, s, db_path)
        if exec_result is None:
            log.error("전력거래 실행 실패 — 파이프라인 중단")
            pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
            if _DASHBOARD_AVAILABLE and run_id is not None:
                finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
            pipeline.print_summary()
            sys.exit(1)
    else:
        # ── Step4 Action Execution Engine (순차) ──────────────────
        stage_r, exec_result = stage_execution(args, decisions)
        pipeline.add(stage_r)
        stage_order += 1
        _record_stage(run_id, stage_order, stage_r, db_path)
        if not stage_r.ok or exec_result is None:
            log.error("Action Execution 실패 — 파이프라인 중단")
            pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
            if _DASHBOARD_AVAILABLE and run_id is not None:
                finish_run(run_id, pipeline.total_elapsed_sec, ok=False, db_path=db_path)
            pipeline.print_summary()
            sys.exit(1)

    # ── Step5 Evaluation Engine ───────────────────────────────────
    stage_r, eval_report = stage_evaluation(args, exec_result, decisions, baseline_peak_kw)
    pipeline.add(stage_r)
    stage_order += 1
    _record_stage(run_id, stage_order, stage_r, db_path)

    # ── 결과 저장 ─────────────────────────────────────────────────
    pipeline.total_elapsed_sec = time.perf_counter() - pipeline_t0
    if _DASHBOARD_AVAILABLE and run_id is not None:
        finish_run(run_id, pipeline.total_elapsed_sec, ok=pipeline.ok, db_path=db_path)

    if args.output_dir or args.save_json:
        _divider()
        log.info("▷ 결과 저장")
        _divider()
        _save_outputs(
            args, alfp_result, state_json_list,
            decisions, exec_result, eval_report, pipeline,
            run_id=run_id,
        )

    # ── 최종 요약 ─────────────────────────────────────────────────
    pipeline.print_summary()


if __name__ == "__main__":
    main()
