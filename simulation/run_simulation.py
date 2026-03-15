"""
ALFP Mesa 시뮬레이션 실행 스크립트

사용 예시:
  # 단일 Phase 실행
  python simulation/run_simulation.py --phase 1
  python simulation/run_simulation.py --phase 4 --steps 192

  # ALFP 파이프라인 실행 후 decisions를 Mesa에 넘겨 시뮬레이션
  python simulation/run_simulation.py --phase 3 --use-alfp
  python simulation/run_simulation.py --phase 4 --use-alfp --prosumers bus_48_Commercial

  # 4단계 비교 실행
  python simulation/run_simulation.py --all-phases

  # 특정 프로슈머만
  python simulation/run_simulation.py --phase 3 --prosumers bus_48_Commercial bus_78_Commercial
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (패키지 임포트 보장)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from simulation.model import ALFPSimulationModel


# ─────────────────────────────────────────────────────────────────
# 출력 헬퍼
# ─────────────────────────────────────────────────────────────────

PHASE_LABELS = {
    1: "Phase 1 - 단일 부하 예측",
    2: "Phase 2 - Agentic forecast pipeline",
    3: "Phase 3 - ESS 연동",
    4: "Phase 4 - 에너지 거래 연동",
}


def _bar(title: str, width: int = 62) -> None:
    print(f"\n{'='*width}")
    print(f"  {title}")
    print("=" * width)


def _section(title: str) -> None:
    print(f"\n  ── {title} ──")


def _kv(key: str, val, unit: str = "") -> None:
    print(f"    {key:<35} {val}{unit}")


def _print_summary(summary: dict, df) -> None:
    phase = summary["phase"]
    _bar(PHASE_LABELS[phase])

    _section("커뮤니티 부하")
    _kv("에이전트 수",         summary["n_prosumers"])
    _kv("시뮬레이션 스텝",     summary["n_steps_run"],   " steps (15분 단위)")
    _kv("평균 부하",            summary["avg_community_load_kw"],  " kW")
    _kv("평균 PV 발전량",       summary["avg_community_pv_kw"],    " kW")
    _kv("최대 피크 부하",       summary["peak_load_kw"],            " kW")

    _section("예측 성능 (Phase 1: naive / Phase 2+: pipeline)")
    _kv("평균 MAPE",            f"{summary['avg_forecast_mape_pct']:.2f}", " %")

    if phase >= 3:
        _section("ESS 운영 (Phase 3+)")
        _kv("총 충전량",         summary.get("ess_total_charged_kwh", "-"),    " kWh")
        _kv("총 방전량",         summary.get("ess_total_discharged_kwh", "-"), " kWh")
        _kv("피크 억제 횟수",    summary.get("ess_peak_shaving_count", "-"),   " 회")
        _kv("ESS 활용률",        f"{summary.get('ess_utilization_rate', 0)*100:.1f}", " %")
        _kv("최종 SoC",          summary.get("final_soc_pct", "-"),            " %")
        _kv("ESS 절감액",        f"{summary.get('ess_saving_krw', 0):,.0f}",   " 원")

    if phase >= 4:
        _section("에너지 거래 (Phase 4)")
        _kv("총 거래 건수",      summary.get("total_trades", 0),               " 건")
        _kv("총 거래량",         summary.get("total_matched_kwh", 0),          " kWh")
        _kv("판매자 수익",       f"{summary.get('seller_revenue_krw', 0):,.0f}", " 원")
        _kv("구매자 절감",       f"{summary.get('buyer_saving_krw', 0):,.0f}",  " 원")
        _kv("커뮤니티 총 절감",  f"{summary.get('community_saving_krw', 0):,.0f}", " 원")
        _kv("마켓 수수료",       f"{summary.get('market_revenue_krw', 0):,.0f}", " 원")


def _print_timeseries_preview(df, n: int = 5) -> None:
    """수집된 시계열 데이터 앞부분 출력."""
    cols = [c for c in ["step", "hour", "community_load_kw", "community_pv_kw",
                         "avg_forecast_mape", "ess_soc_pct", "market_matched_kw",
                         "cumulative_saving_krw"] if c in df.columns]
    print(f"\n  [수집 데이터 미리보기 (첫 {n} 행)]")
    print(df[cols].head(n).to_string(index=False))


# ─────────────────────────────────────────────────────────────────
# 단일 Phase 실행
# ─────────────────────────────────────────────────────────────────

def run_phase(
    phase: int,
    data_path: str,
    n_steps: int,
    prosumer_ids: list[str] | None,
    seed: int,
    use_alfp: bool = False,
    alfp_decisions: dict | None = None,
    verbose: bool = True,
) -> dict:
    print(f"\n  [{PHASE_LABELS[phase]}] 시뮬레이션 시작 ...")
    decisions_to_use = alfp_decisions
    if decisions_to_use is None and use_alfp:
        decisions_to_use = _run_alfp_pipeline(data_path, n_steps, prosumer_ids)
        if decisions_to_use is None:
            print("  [경고] ALFP decisions를 사용하지 않고 경량 규칙으로 진행합니다.")
        else:
            print("  ALFP decisions 적용: ESS 스케줄·거래·DR 추천 사용")

    t0 = time.time()
    model = ALFPSimulationModel(
        phase=phase,
        data_path=data_path,
        n_steps=n_steps,
        prosumer_ids=prosumer_ids,
        seed=seed,
        alfp_decisions=decisions_to_use,
    )
    df = model.run()
    elapsed = time.time() - t0

    summary = model.summary()
    if verbose:
        _print_summary(summary, df)
        _print_timeseries_preview(df)
    print(f"\n  완료: {elapsed:.1f}초")

    return summary


def _run_alfp_pipeline(
    data_path: str,
    forecast_horizon: int,
    prosumer_ids: list[str] | None,
) -> dict | None:
    """ALFP 파이프라인을 실행하고 decisions만 반환. 실패 시 None."""
    try:
        from alfp.pipeline.graph import run_pipeline
        prosumer_id = (prosumer_ids[0] if prosumer_ids else None) or "bus_48_Commercial"
        print(f"  ALFP 파이프라인 실행 중 (prosumer_id={prosumer_id}, horizon={forecast_horizon}) ...")
        result = run_pipeline(
            prosumer_id=prosumer_id,
            data_path=data_path,
            forecast_horizon=forecast_horizon,
        )
        decisions = result.get("decisions")
        if not decisions:
            return None
        return decisions
    except Exception as e:
        print(f"  ALFP 파이프라인 오류: {e}")
        return None


# ─────────────────────────────────────────────────────────────────
# 4단계 비교 실행
# ─────────────────────────────────────────────────────────────────

def run_all_phases(
    data_path: str,
    n_steps: int,
    prosumer_ids: list[str] | None,
    seed: int,
    use_alfp: bool = False,
) -> None:
    """4단계를 순서대로 실행하고 단계별 KPI를 비교합니다."""
    alfp_decisions = None
    if use_alfp:
        alfp_decisions = _run_alfp_pipeline(data_path, n_steps, prosumer_ids)
        if alfp_decisions is None:
            print("  [경고] ALFP decisions를 사용하지 않고 경량 규칙으로 진행합니다.")
        else:
            print("  ALFP decisions 적용: ESS 스케줄·거래·DR 추천 사용")

    results = {}
    for phase in [1, 2, 3, 4]:
        results[phase] = run_phase(
            phase=phase,
            data_path=data_path,
            n_steps=n_steps,
            prosumer_ids=prosumer_ids,
            seed=seed,
            use_alfp=False,
            alfp_decisions=alfp_decisions,
            verbose=True,
        )

    # ── 비교 요약 ────────────────────────────────────────────────
    _bar("Phase 비교 요약")
    print(f"\n  {'KPI':<30} {'P1':>10} {'P2':>10} {'P3':>10} {'P4':>10}")
    print(f"  {'-'*70}")

    def _row(label, key, fmt="{:.2f}", fallback="-"):
        vals = []
        for p in [1, 2, 3, 4]:
            v = results[p].get(key)
            vals.append(fmt.format(v) if v is not None else fallback)
        print(f"  {label:<30} {vals[0]:>10} {vals[1]:>10} {vals[2]:>10} {vals[3]:>10}")

    _row("평균 부하 (kW)",     "avg_community_load_kw")
    _row("평균 MAPE (%)",       "avg_forecast_mape_pct")
    _row("피크 부하 (kW)",      "peak_load_kw", fmt="{:.1f}")
    _row("ESS 충전 (kWh)",      "ess_total_charged_kwh",    fmt="{:.1f}", fallback="N/A")
    _row("ESS 절감 (원)",       "ess_saving_krw",           fmt="{:,.0f}", fallback="N/A")
    _row("거래 건수",           "total_trades",             fmt="{:.0f}", fallback="N/A")
    _row("커뮤니티 절감 (원)",  "community_saving_krw",     fmt="{:,.0f}", fallback="N/A")


# ─────────────────────────────────────────────────────────────────
# CLI 엔트리포인트
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ALFP Mesa 멀티 에이전트 시뮬레이션",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2, 3, 4], default=1,
        help="시뮬레이션 단계 (1~4):\n"
             "  1: 단일 부하 예측\n"
             "  2: Agentic forecast pipeline\n"
             "  3: ESS 연동\n"
             "  4: 에너지 거래 연동",
    )
    parser.add_argument(
        "--all-phases", action="store_true",
        help="Phase 1~4 전체 비교 실행",
    )
    parser.add_argument(
        "--data", default="data/train_2026_seoul.pkl",
        help="학습 데이터 pkl 경로 (기본: data/train_2026_seoul.pkl)",
    )
    parser.add_argument(
        "--steps", type=int, default=96,
        help="시뮬레이션 스텝 수 (기본 96 = 24시간, 15분 단위)",
    )
    parser.add_argument(
        "--prosumers", nargs="*", default=None,
        help="시뮬레이션할 프로슈머 ID 목록 (기본: 전체)\n"
             "예: --prosumers bus_48_Commercial bus_78_Commercial",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="난수 시드 (기본: 42)",
    )
    parser.add_argument(
        "--use-alfp", action="store_true",
        help="ALFP 파이프라인을 먼저 실행하고 decisions를 Mesa 시뮬레이션에 전달",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="요약 출력 생략",
    )

    args = parser.parse_args()

    _bar("ALFP Mesa 멀티 에이전트 시뮬레이션")
    print(f"  데이터: {args.data}")
    print(f"  스텝:   {args.steps} (= {args.steps * 15 // 60}시간)")
    if args.prosumers:
        print(f"  프로슈머: {', '.join(args.prosumers)}")
    if args.use_alfp:
        print("  ALFP decisions: 사용 (파이프라인 실행 후 전달)")

    if args.all_phases:
        run_all_phases(
            data_path=args.data,
            n_steps=args.steps,
            prosumer_ids=args.prosumers,
            seed=args.seed,
            use_alfp=args.use_alfp,
        )
    else:
        run_phase(
            phase=args.phase,
            data_path=args.data,
            n_steps=args.steps,
            prosumer_ids=args.prosumers,
            seed=args.seed,
            use_alfp=args.use_alfp,
            verbose=not args.quiet,
        )


if __name__ == "__main__":
    main()
