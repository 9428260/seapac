#!/usr/bin/env python3
"""
pkl 파일을 사람이 볼 수 있게 요약·출력하는 스크립트.

사용법:
  python3 inspect_pkl.py paper_reproduction_dataset_from_screenshot_schema.pkl
  python3 inspect_pkl.py IEEE141_grid.pkl
  python3 inspect_pkl.py paper_reproduction_dataset_from_screenshot_schema.pkl --export-csv
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    pd = None


def is_dataframe_like(obj):
    return pd is not None and hasattr(obj, "head") and hasattr(obj, "columns")


def summarize(obj, indent: str = "", max_str_len: int = 80):
    """객체 구조를 요약해 출력."""
    if obj is None:
        print(f"{indent}None")
        return
    if is_dataframe_like(obj):
        df = obj
        print(f"{indent}DataFrame: shape={df.shape}, columns={list(df.columns)}")
        print(f"{indent}  head:\n{df.head().to_string()}")
        return
    if isinstance(obj, dict):
        print(f"{indent}dict with keys: {list(obj.keys())}")
        for k, v in list(obj.items())[:15]:
            vrepr = repr(v)
            if len(vrepr) > max_str_len:
                vrepr = vrepr[: max_str_len - 3] + "..."
            if is_dataframe_like(v):
                print(f"{indent}  [{k!r}]")
                summarize(v, indent=indent + "    ")
            elif isinstance(v, dict):
                print(f"{indent}  [{k!r}] dict with keys: {list(v.keys())}")
            else:
                print(f"{indent}  [{k!r}] = {vrepr}")
        if len(obj) > 15:
            print(f"{indent}  ... and {len(obj) - 15} more keys")
        return
    if isinstance(obj, (list, tuple)):
        print(f"{indent}{type(obj).__name__}: len={len(obj)}")
        for i, x in enumerate(list(obj)[:5]):
            summarize(x, indent=indent + "  ")
        if len(obj) > 5:
            print(f"{indent}  ... and {len(obj) - 5} more items")
        return
    print(f"{indent}{type(obj).__name__}: {repr(obj)[:max_str_len]}")


def export_dataset_to_csv(pkl_path: Path, out_dir: Path | None = None):
    """데이터셋 pkl 안의 DataFrame들을 CSV로 저장."""
    if pd is None:
        print("pandas 필요: pip install pandas")
        return
    out_dir = out_dir or pkl_path.parent / (pkl_path.stem + "_export")
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(pkl_path, "rb") as f:
        data = pickle.load(f)

    if not isinstance(data, dict):
        print("dict 형태의 데이터셋이 아닙니다.")
        return

    for key, val in data.items():
        if is_dataframe_like(val):
            path = out_dir / f"{key}.csv"
            val.to_csv(path, index=False)
            print(f"  {path}")
        elif isinstance(val, dict):
            for subkey, subval in val.items():
                if is_dataframe_like(subval):
                    path = out_dir / f"{key}_{subkey}.csv"
                    subval.to_csv(path, index=False)
                    print(f"  {path}")

    print(f"CSV 저장 경로: {out_dir}")


def main():
    parser = argparse.ArgumentParser(description="pkl 내용을 사람이 보기 쉽게 출력")
    parser.add_argument("pkl_file", type=Path, help="확인할 .pkl 파일 경로")
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="DataFrame들을 CSV로 저장 (엑셀/스프레드시트에서 열기)",
    )
    parser.add_argument(
        "--no-summary",
        action="store_true",
        help="요약 출력 생략 (--export-csv 할 때만 사용)",
    )
    args = parser.parse_args()

    if not args.pkl_file.exists():
        print(f"파일 없음: {args.pkl_file}", file=sys.stderr)
        sys.exit(1)

    if args.export_csv:
        export_dataset_to_csv(args.pkl_file)
        if args.no_summary:
            return

    print("=" * 60)
    print(f"파일: {args.pkl_file}")
    print("=" * 60)

    with open(args.pkl_file, "rb") as f:
        data = pickle.load(f)

    summarize(data)


if __name__ == "__main__":
    main()
