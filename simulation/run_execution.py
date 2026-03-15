"""
Step 4 — Action Execution Engine CLI (PRD: seapac_agentic_prd.md)

실행 흐름: Agent Proposal → Policy Validation → Coordinator Approval → Mesa Update
Mesa 시뮬레이션 결과를 실행 단계로 수행하고, 결과를 Step 5 Evaluation용으로 출력합니다.

사용 예시:
  # ALFP 파이프라인에서 decisions 생성 후 실행 단계 수행
  python simulation/run_execution.py --use-alfp

  # 저장된 decisions JSON 파일로 실행
  python simulation/run_execution.py --decisions-file output/decisions.json

  # 실행 결과를 CSV/JSON으로 저장 (Step 5 입력용)
  python simulation/run_execution.py --use-alfp --output-dir output --save-csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from seapac_agents.execution import ExecutionResult, run_execution


def _load_decisions_from_alfp(data_path: str, n_steps: int, prosumer_id: str) -> dict | None:
    """ALFP 파이프라인 실행 후 decisions 반환."""
    try:
        from alfp.pipeline.graph import run_pipeline
        result = run_pipeline(
            prosumer_id=prosumer_id,
            data_path=data_path,
            forecast_horizon=n_steps,
        )
        return result.get("decisions")
    except Exception as e:
        print(f"  ALFP 파이프라인 오류: {e}", file=sys.stderr)
        return None


def _load_decisions_from_file(path: str) -> dict | None:
    """JSON 파일에서 decisions 로드."""
    p = Path(path)
    if not p.exists():
        print(f"  파일 없음: {path}", file=sys.stderr)
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("decisions", data)


def _fmt_num(val: Any, num_fmt: str = ".2f", default: str = "-") -> str:
    """숫자면 포맷, 아니면 default 반환 (Mesa summary 미존재 시 안전 출력용)."""
    if val is None or (isinstance(val, str) and val == "-"):
        return default
    try:
        n = float(val)
        return format(n, num_fmt) if "f" in num_fmt else str(int(n))
    except (TypeError, ValueError):
        return default


def _print_summary(res: ExecutionResult) -> None:
    """Mesa 시뮬레이션 결과(ExecutionResult.summary, dataframe)를 사용하여 실행 결과 요약 출력."""
    s = res.summary
    print("\n  ── Step 4 실행 결과 (Mesa 시뮬레이션 결과 기준) ──")
    print(f"    승인 여부:        {'승인' if res.approved else '미승인'}")
    print(f"    검증 오류 수:     {len(res.validation_errors)}")
    if res.validation_errors:
        for err in res.validation_errors[:5]:
            print(f"      - {err}")
        if len(res.validation_errors) > 5:
            print(f"      ... 외 {len(res.validation_errors) - 5}건")
    print(f"    프로슈머 수:      {s.get('n_prosumers', '-')}")
    print(f"    시뮬레이션 스텝:  {s.get('n_steps_run', '-')}")
    avg_load = s.get("avg_community_load_kw")
    print(f"    평균 부하:        {_fmt_num(avg_load)} kW")
    peak_load = s.get("peak_load_kw")
    print(f"    피크 부하:        {_fmt_num(peak_load)} kW")
    ess_saving = s.get("ess_saving_krw")
    if ess_saving is not None:
        print(f"    ESS 절감액:        {_fmt_num(ess_saving, ',.0f')} 원")
    comm_saving = s.get("community_saving_krw")
    if comm_saving is not None:
        print(f"    커뮤니티 절감:    {_fmt_num(comm_saving, ',.0f')} 원")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 4 Action Execution: decisions를 Mesa 시뮬레이션에 적용 (PRD seapac_agentic_prd.md)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--use-alfp", action="store_true",
        help="ALFP 파이프라인을 실행해 decisions를 생성한 뒤 실행 단계 수행",
    )
    parser.add_argument(
        "--decisions-file", type=str, default=None,
        help="decisions가 담긴 JSON 파일 경로 (--use-alfp와 둘 중 하나 지정)",
    )
    parser.add_argument(
        "--data", default="data/train_2026_seoul.pkl",
        help="Mesa 시뮬레이션용 데이터 pkl 경로",
    )
    parser.add_argument(
        "--steps", type=int, default=96,
        help="시뮬레이션 스텝 수 (15분 단위, 기본 96 = 24시간)",
    )
    parser.add_argument(
        "--phase", type=int, choices=[1, 2, 3, 4], default=4,
        help="Mesa phase (기본 4: ESS+거래 포함)",
    )
    parser.add_argument(
        "--prosumers", nargs="*", default=None,
        help="프로슈머 ID 목록 (기본: 전체)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="난수 시드",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="결과 저장 디렉터리 (지정 시 summary.json 등 저장)",
    )
    parser.add_argument(
        "--save-csv", action="store_true",
        help="시뮬레이션 시계열을 CSV로 저장 (Step 5 Evaluation 입력용)",
    )
    parser.add_argument(
        "--strict-validation", action="store_true",
        help="검증 오류 시 Mesa 실행 스킵",
    )

    args = parser.parse_args()

    if not args.use_alfp and not args.decisions_file:
        print("  --use-alfp 또는 --decisions-file 중 하나를 지정하세요.", file=sys.stderr)
        sys.exit(1)
    if args.use_alfp and args.decisions_file:
        print("  --use-alfp와 --decisions-file은 동시에 지정할 수 없습니다.", file=sys.stderr)
        sys.exit(1)

    # Decisions 로드
    decisions = None
    if args.use_alfp:
        prosumer_id = (args.prosumers[0] if args.prosumers else None) or "bus_48_Commercial"
        print("  ALFP 파이프라인 실행 중 ...")
        decisions = _load_decisions_from_alfp(args.data, args.steps, prosumer_id)
        if not decisions:
            sys.exit(2)
        print("  decisions 로드 완료")
    else:
        print(f"  decisions 파일 로드: {args.decisions_file}")
        decisions = _load_decisions_from_file(args.decisions_file)
        if not decisions:
            sys.exit(2)
        print("  decisions 로드 완료")

    # Step 4 실행
    print("  Step 4 Action Execution 실행 중 ...")
    result = run_execution(
        decisions,
        data_path=args.data,
        n_steps=args.steps,
        phase=args.phase,
        prosumer_ids=args.prosumers,
        seed=args.seed,
        strict_validation=args.strict_validation,
    )

    _print_summary(result)

    # 출력 디렉터리에 저장
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        with open(out_dir / "execution_summary.json", "w", encoding="utf-8") as f:
            json.dump(result.summary, f, ensure_ascii=False, indent=2)
        print(f"\n  저장: {out_dir / 'execution_summary.json'}")
        if args.save_csv and result.dataframe is not None:
            csv_path = out_dir / "execution_timeseries.csv"
            result.dataframe.to_csv(csv_path, index=False)
            print(f"  저장: {csv_path}")

    if args.save_csv and result.dataframe is not None and not args.output_dir:
        Path("output").mkdir(exist_ok=True)
        result.dataframe.to_csv("output/execution_timeseries.csv", index=False)
        print(f"\n  저장: output/execution_timeseries.csv")

    sys.exit(0 if result.approved or not result.validation_errors else 1)


if __name__ == "__main__":
    main()
