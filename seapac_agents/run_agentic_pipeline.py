"""
SEAPAC Agentic Decision Layer — 전체 파이프라인 실행 (Step 3~5)

실행 순서:
  Step 3: Multi-Agent Decision Engine — 5개 에이전트 의사결정
  Step 4: Action Execution Engine  — 검증·승인 → 실행
  Step 5: Evaluation Engine  — KPI 평가 및 등급 산정

Usage:
  python seapac_agents/run_agentic_pipeline.py
  python seapac_agents/run_agentic_pipeline.py --steps 96 --phase 4
  PYTHONPATH=. python seapac_agents/run_agentic_pipeline.py --use-cda --steps 96
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Allow `python3 seapac_agents/run_agentic_pipeline.py` direct execution.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_dashboard.db import create_run, get_db_path, init_db, upsert_artifact


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SEAPAC Agentic Pipeline (Step 2~5)")
    p.add_argument("--data-path", default="data/train_2026_seoul.pkl", help="Mesa 학습 데이터 경로")
    p.add_argument("--llm-mode", default=os.environ.get("SEAPAC_LLM_MODE", "all"), choices=["off", "forecast", "forecast_plan", "core", "market", "plan", "all"], help="통합 LLM 모드")
    p.add_argument("--steps", type=int, default=96, help="시뮬레이션 스텝 수 (기본 96 = 24h)")
    p.add_argument("--phase", type=int, default=4, choices=[1, 2, 3, 4], help="Mesa 시뮬레이션 단계")
    p.add_argument("--peak-threshold", type=float, default=500.0, help="피크 임계값 (kW)")
    p.add_argument("--ess-capacity", type=float, default=200.0, help="ESS 용량 (kWh)")
    p.add_argument("--grid-price", type=float, default=100.0, help="계통 전기 단가 (원/kWh)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--verbose", action="store_true", help="상세 출력")
    p.add_argument("--use-cda", action="store_true", default=True, help="CDA 시장 사용 (기본값)")
    p.add_argument("--no-cda", action="store_false", dest="use_cda", help="AgentScope 페르소나 모드 사용 (CDA 비활성화)")
    p.add_argument("--use-cda-negotiation", action="store_true", help="CDA + Strategy Agent(LLM) + Negotiation Layer 사용 (cda_strategy_negotiation_prd.md). --use-cda 필요")
    p.add_argument("--use-parallel", action="store_true", help="Final Parallel Execution Layer 사용 (Policy/Eco/Storage 에이전트 병렬 평가 후 실행)")
    p.add_argument("--audit-log", default=None, help="병렬 레이어 감사 로그 파일 경로 (--use-parallel 시 append)")
    p.add_argument(
        "--use-agent-plan",
        action="store_true",
        default=True,
        help="LLM 에이전트 계획(AgentPlan) 실행 (기본값): LLM이 Policy/Storage/EcoSaver 실행 순서와 파라미터를 계획하고 전력거래 decisions를 수립합니다.",
    )
    p.add_argument(
        "--no-agent-plan",
        action="store_false",
        dest="use_agent_plan",
        help="AgentPlan 비활성화 (Step 3-P 건너뜀)",
    )
    p.add_argument(
        "--agent-plan-no-llm",
        action="store_true",
        help="--use-agent-plan 시 규칙 기반 기본 계획 사용 (LLM 미사용)",
    )
    p.add_argument(
        "--agent-plan-max-revisions",
        type=int,
        default=1,
        help="AgentPlan 시뮬레이션 실패 시 LLM 재수립 최대 횟수 (기본 1)",
    )
    return p.parse_args()


def _default_max_peak_load_kw(peak_threshold_kw: float) -> float:
    """Shared approval threshold for Agent Plan simulation and final Step 4 execution."""
    return peak_threshold_kw * 1.10


def main() -> None:
    args = _parse_args()
    from alfp.llm import set_llm_mode, get_llm_mode
    set_llm_mode(args.llm_mode)
    print(f"\n[Config] LLM mode: {get_llm_mode()}")

    state_json_list: list[dict] = []

    # ── Step 3: Multi-Agent Decision Engine ───────────────────────
    max_kw = min(50.0, args.ess_capacity / 4)
    max_peak_load_kw = _default_max_peak_load_kw(args.peak_threshold)
    if args.use_cda_negotiation:
        if not args.use_cda:
            args.use_cda = True
        print("\n[Multi-Agent Decision] CDA + Strategy Agent + Negotiation 실행...")
        from seapac_agents.decision import (
            _init_agentscope,
            PolicyAgentAS,
            SmartSellerAgentAS,
            StorageMasterAgentAS,
            EcoSaverAgentAS,
            _PROMPTS,
        )
        from cda import run_cda_decision_series_with_agents_and_negotiation

        _init_agentscope()
        policy = PolicyAgentAS(max_charge_kw=max_kw, max_discharge_kw=max_kw)
        seller = SmartSellerAgentAS()
        storage = StorageMasterAgentAS()
        eco_saver = EcoSaverAgentAS(peak_threshold_kw=args.peak_threshold)
        decisions = run_cda_decision_series_with_agents_and_negotiation(
            state_json_list,
            policy,
            seller,
            storage,
            eco_saver,
            state_message_template=_PROMPTS["state_message_template"],
            use_llm_strategy=True,
        )
    elif args.use_cda:
        print("\n[Multi-Agent Decision] CDA 시장 — Order Book + 매칭 실행...")
        from seapac_agents.decision import (
            _init_agentscope,
            PolicyAgentAS,
            SmartSellerAgentAS,
            StorageMasterAgentAS,
            EcoSaverAgentAS,
            _PROMPTS,
        )
        from cda import run_cda_decision_series_with_agents

        _init_agentscope()
        policy = PolicyAgentAS(max_charge_kw=max_kw, max_discharge_kw=max_kw)
        seller = SmartSellerAgentAS()
        storage = StorageMasterAgentAS()
        eco_saver = EcoSaverAgentAS(peak_threshold_kw=args.peak_threshold)
        decisions = run_cda_decision_series_with_agents(
            state_json_list,
            policy,
            seller,
            storage,
            eco_saver,
            state_message_template=_PROMPTS["state_message_template"],
        )
    else:
        print("\n[Multi-Agent Decision] AgentScope — 페르소나 주입 실행...")
        from seapac_agents.decision import run_agentscope_decision_series

        decisions = run_agentscope_decision_series(
            state_json_list,
            peak_threshold_kw=args.peak_threshold,
            max_charge_kw=max_kw,
            max_discharge_kw=max_kw,
        )

    n_ess = len(decisions.get("ess_schedule", []))
    n_trade = len(decisions.get("trading_recommendations", []))
    n_dr = len(decisions.get("demand_response_events", []))
    print(f"  완료: ESS 스케줄 {n_ess}건, 거래 권고 {n_trade}건, DR 이벤트 {n_dr}건")

    if args.verbose and n_ess > 0:
        sample = decisions["ess_schedule"][0]
        print(f"  ESS 샘플: {sample}")

    # ── Step 3-P: LLM Agent Plan (전력거래 에이전트 계획 수립·실행) ─────
    if args.use_agent_plan:
        use_llm_plan = not args.agent_plan_no_llm
        mode_str = "LLM 계획 수립" if use_llm_plan else "규칙 기반 기본 계획"
        print(f"\n[Agent Plan] {mode_str} 실행...")
        print("  Policy → Storage → EcoSaver → Simulate 순서로 에이전트를 계획·실행합니다.")
        from seapac_agents.agent_planner import run_agent_plan

        decisions = run_agent_plan(
            state_json_list=state_json_list,
            alfp_decisions=decisions,
            peak_threshold_kw=args.peak_threshold,
            max_charge_kw=max_kw,
            max_discharge_kw=max_kw,
            use_llm=use_llm_plan,
            max_revisions=args.agent_plan_max_revisions,
            data_path=args.data_path,
            n_steps=args.steps,
            phase=args.phase,
            seed=args.seed,
            ess_capacity_kwh=args.ess_capacity,
            max_peak_load_kw=max_peak_load_kw,
            verbose=args.verbose,
        )
        ap = decisions.get("agent_plan", {})
        ap_approved = "승인" if ap.get("simulation_approved") else "미승인"
        ap_revised = " (재수립됨)" if ap.get("revised") else ""
        n_logs = len(ap.get("agent_logs") or [])
        print(
            f"  완료: 계획 ID={ap.get('plan_id')} | 시뮬레이션={ap_approved}{ap_revised} | "
            f"ESS {len(decisions.get('ess_schedule', []))}건 | "
            f"DR {len(decisions.get('demand_response_events', []))}건 | "
            f"에이전트 로그 {n_logs}건"
        )
        if args.verbose:
            print(f"  계획 목표: {ap.get('objective')}")
            for log in (ap.get("agent_logs") or []):
                print(f"    Step {log.get('step_id')} [{log.get('agent')}] → {log.get('status')}")

    # ── Step 3.5: Final Parallel Execution Layer (PRD: seapac_parallel_agents_prd.md) ──
    if args.use_parallel:
        print("\n[Parallel Agents] Policy / Eco Saver / Storage 에이전트 병렬 평가...")
        from parallel_agents import (
            run_parallel_evaluation_and_convert,
            PolicyConfig,
            decisions_to_candidate_bundle,
        )
        from parallel_agents.audit_log import log_parallel_evaluation

        max_kw_parallel = min(50.0, args.ess_capacity / 4)
        policy_cfg = PolicyConfig(
            max_charge_kw=max_kw_parallel,
            max_discharge_kw=max_kw_parallel,
        )
        bundle_for_audit = decisions_to_candidate_bundle(
            decisions,
            state_json_list,
            peak_threshold_kw=args.peak_threshold,
        )
        decisions = run_parallel_evaluation_and_convert(
            decisions,
            state_json_list=state_json_list,
            policy_config=policy_cfg,
            peak_threshold_kw=args.peak_threshold,
            max_charge_kw=max_kw_parallel,
            max_discharge_kw=max_kw_parallel,
            use_async=True,
        )
        pl = decisions.get("parallel_layer") or {}
        print(f"  병렬 레이어 완료: 승인 {len(pl.get('approved_actions') or [])}건, 거절 {len(pl.get('rejected_actions') or [])}건, 권고 {len(pl.get('recommendations') or [])}건")
        if args.audit_log:
            log_parallel_evaluation(
                bundle_for_audit,
                pl,
                decisions,
                audit_path=args.audit_log,
            )
            print(f"  감사 로그 기록: {args.audit_log}")

    # ── Step 4: Action Execution Engine (또는 CDA Settlement) ───────
    if args.use_cda:
        print("\n[Action Execution] CDA Settlement Engine 실행...")
        from cda import run_execution
    else:
        print("\n[Action Execution] Action Execution Engine 실행...")
        from seapac_agents.execution import run_execution

    result = run_execution(
        decisions,
        data_path=args.data_path,
        n_steps=args.steps,
        phase=args.phase,
        seed=args.seed,
        ess_capacity_kwh=args.ess_capacity,
        ess_peak_threshold_kw=args.peak_threshold,
        max_charge_kw=max_kw,
        max_discharge_kw=max_kw,
        max_peak_load_kw=max_peak_load_kw,
    )

    approved_str = "승인" if result.approved else "미승인"
    print(f"  완료: {approved_str}, 검증 오류 {len(result.validation_errors)}건")
    if result.validation_errors and args.verbose:
        for e in result.validation_errors[:5]:
            print(f"    - {e}")

    # ── Evaluation Engine ─────────────────────────────────────────
    print("\n[Evaluation] KPI 평가...")
    from seapac_agents.evaluation import evaluate_from_execution_result, EvaluationConfig

    eval_cfg = EvaluationConfig(
        grid_price_krw_per_kwh=args.grid_price,
        baseline_peak_kw=0.0,
    )
    report = evaluate_from_execution_result(result, decisions=decisions, config=eval_cfg)
    report.print_report()

    # ── DB 저장 ──────────────────────────────────────────────────
    db_path = get_db_path(os.environ.get("PIPELINE_DB_DIR"))
    init_db(db_path)
    run_id = create_run(
        {
            "source": "seapac_agents.run_agentic_pipeline",
            "data_path": args.data_path,
            "steps": args.steps,
            "phase": args.phase,
            "use_agent_plan": args.use_agent_plan,
            "use_parallel": args.use_parallel,
            "use_cda": args.use_cda,
        },
        db_path=db_path,
    )
    upsert_artifact(run_id, "multi_agent_decisions", decisions, db_path=db_path)
    upsert_artifact(run_id, "evaluation_report", report.to_dict(), db_path=db_path)
    if args.use_agent_plan and "agent_plan" in decisions:
        upsert_artifact(run_id, "agent_plan", decisions["agent_plan"], db_path=db_path)
    if result.dataframe is not None:
        upsert_artifact(run_id, "execution_timeseries", result.dataframe.to_dict(orient="records"), db_path=db_path)
    print(f"\n  DB 저장: run_id={run_id} ({db_path})")

    print("\n파이프라인 완료.\n")


if __name__ == "__main__":
    main()
