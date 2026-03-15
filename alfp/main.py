"""
ALFP - Agentic Load Forecast Platform
메인 실행 모듈
"""

import json
import sys
import time
from pathlib import Path

from alfp.data.loader import load_dataset, get_prosumer_list, describe_dataset
from alfp.pipeline.graph import run_pipeline


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def print_metrics(metrics: dict):
    for key in ["load", "pv", "net_load"]:
        if key not in metrics:
            continue
        m = metrics[key]
        print(f"  [{m['label']}]")
        print(f"    MAE  : {m['MAE']:.3f} kW")
        print(f"    RMSE : {m['RMSE']:.3f} kW")
        print(f"    MAPE : {m['MAPE']:.2f} %")
        print(f"    Peak Error: {m['peak']['peak_error_pct']:.2f}%  "
              f"(실제 {m['peak']['true_peak_kw']:.1f} kW / 예측 {m['peak']['pred_peak_kw']:.1f} kW)")

    if "kpi" in metrics:
        kpi = metrics["kpi"]
        print(f"\n  ── KPI ──")
        print(f"    MAPE  : {'✓ 달성' if kpi['MAPE_pass'] else '✗ 미달'} "
              f"({kpi['MAPE_achieved']:.2f}% / 목표 {kpi['MAPE_target']}%)")
        print(f"    피크  : {'✓ 달성' if kpi['peak_acc_pass'] else '✗ 미달'} "
              f"({kpi['peak_acc_achieved']:.2f}% / 목표 {kpi['peak_acc_target']}%)")


def print_llm_plan(plan: dict):
    """LLM ForecastPlanner 결과 출력."""
    reasoning = plan.get("llm_reasoning", "")
    insights = plan.get("llm_data_insights", "")
    risks = plan.get("llm_risk_factors", [])
    if reasoning:
        print(f"\n  [LLM 모델 선택 근거]")
        print(f"  {reasoning}")
    if insights:
        print(f"\n  [LLM 데이터 인사이트]")
        print(f"  {insights}")
    if risks:
        print(f"\n  [LLM 위험 요소]")
        for r in risks:
            print(f"    • {r}")


def print_llm_validation(metrics: dict):
    """LLM ValidationAgent 분석 결과 출력."""
    llm = metrics.get("llm_analysis", {})
    if not llm:
        return
    print(f"\n  [LLM 종합 평가] (신뢰도: {llm.get('confidence_level','N/A')})")
    print(f"  {llm.get('overall_assessment','')}")
    print(f"\n  [LLM 부하 분석]  {llm.get('load_analysis','')}")
    print(f"  [LLM PV 분석]    {llm.get('pv_analysis','')}")
    print(f"  [LLM NetLoad]    {llm.get('net_load_analysis','')}")
    suggestions = llm.get("improvement_suggestions", [])
    if suggestions:
        print(f"\n  [LLM 개선 제안]")
        for s in suggestions:
            print(f"    • {s}")
    impact = llm.get("operational_impact", "")
    if impact:
        print(f"\n  [LLM 운영 영향] {impact}")


def print_decisions(decisions: dict):
    ess = decisions.get("ess_summary", {})
    print(f"  ESS 스케줄: 충전 {ess.get('charge_steps',0)}스텝 / "
          f"방전 {ess.get('discharge_steps',0)}스텝 / "
          f"대기 {ess.get('idle_steps',0)}스텝")

    trading = decisions.get("trading_summary", {})
    print(f"  에너지 거래: 잉여 {trading.get('total_surplus_events',0)}건, "
          f"총 {trading.get('total_surplus_kw',0):.1f} kW")

    dr = decisions.get("dr_summary", {})
    print(f"  DR 이벤트: {dr.get('dr_event_count',0)}건 "
          f"(피크 임계값 {dr.get('peak_threshold_kw',0):.1f} kW)")

    # LLM 전략
    llm = decisions.get("llm_strategy", {})
    if llm:
        alert = llm.get("alert_level", "N/A")
        print(f"\n  [LLM 경보 수준] {alert}")
        print(f"\n  [LLM ESS 전략]")
        print(f"  {llm.get('ess_strategy','')}")
        print(f"\n  [LLM 거래 전략]")
        print(f"  {llm.get('trading_strategy','')}")
        print(f"\n  [LLM DR 전략]")
        print(f"  {llm.get('dr_strategy','')}")
        print(f"\n  [LLM 종합 추천]")
        print(f"  {llm.get('overall_recommendation','')}")
        actions = llm.get("priority_actions", [])
        if actions:
            print(f"\n  [즉시 실행 권고]")
            for a in actions:
                print(f"    • {a}")
        savings = llm.get("expected_savings", "")
        if savings:
            print(f"\n  [예상 절감 효과] {savings}")


def run(
    prosumer_id: str = "bus_48_Commercial",
    data_path: str = "data/train_2026_seoul.pkl",
    forecast_horizon: int = 96,
    verbose: bool = True,
):
    """
    ALFP 파이프라인 실행.

    Args:
        prosumer_id: 예측할 프로슈머 ID
        data_path: 학습 데이터 경로
        forecast_horizon: 예측 horizon (스텝 수, 15분 단위)
        verbose: 상세 로그 출력 여부
    """
    print_section("Agentic Load Forecast Platform (ALFP)")
    print(f"  데이터: {data_path}")
    print(f"  프로슈머: {prosumer_id}")
    print(f"  예측 Horizon: {forecast_horizon} 스텝 ({forecast_horizon*15//60}시간 {forecast_horizon*15%60}분)")

    start = time.time()

    # 파이프라인 실행
    result = run_pipeline(
        prosumer_id=prosumer_id,
        data_path=data_path,
        forecast_horizon=forecast_horizon,
    )

    elapsed = time.time() - start

    # 에이전트 로그 출력
    if verbose:
        print_section("에이전트 실행 로그")
        for msg in result.get("messages", []):
            print(f"  {msg}")
        if result.get("errors"):
            print("\n  [오류]")
            for err in result["errors"]:
                print(f"  {err}")

    # 예측 계획 출력
    print_section("예측 계획 (ForecastPlannerAgent)")
    plan = result.get("forecast_plan", {})
    skip_keys = {"llm_reasoning", "llm_data_insights", "llm_risk_factors"}
    for k, v in plan.items():
        if k not in skip_keys:
            print(f"  {k}: {v}")
    print_llm_plan(plan)

    # 검증 지표 출력
    print_section("예측 성능 검증 (ValidationAgent)")
    print_metrics(result.get("validation_metrics", {}))
    print_llm_validation(result.get("validation_metrics", {}))

    # 의사결정 출력
    print_section("운영 의사결정 (DecisionAgent)")
    print_decisions(result.get("decisions", {}))

    print_section(f"완료 ({elapsed:.1f}초)")

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ALFP 전력 사용량 예측 시스템")
    parser.add_argument(
        "--prosumer",
        default="bus_48_Commercial",
        help="예측할 프로슈머 ID",
    )
    parser.add_argument(
        "--data",
        default="data/train_2026_seoul.pkl",
        help="학습 데이터 pkl 경로",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=96,
        help="예측 horizon (15분 단위 스텝 수, 기본 96=24시간)",
    )
    parser.add_argument(
        "--list-prosumers",
        action="store_true",
        help="사용 가능한 프로슈머 목록 출력",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="에이전트 로그 출력 생략",
    )

    args = parser.parse_args()

    if args.list_prosumers:
        data = load_dataset(args.data)
        print("=== 데이터셋 정보 ===")
        print(describe_dataset(data))
        print("\n=== 사용 가능한 프로슈머 목록 ===")
        for pid in get_prosumer_list(data):
            print(f"  {pid}")
        sys.exit(0)

    run(
        prosumer_id=args.prosumer,
        data_path=args.data,
        forecast_horizon=args.horizon,
        verbose=not args.quiet,
    )
