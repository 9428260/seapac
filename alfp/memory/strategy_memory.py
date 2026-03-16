"""
Strategy Memory + Evaluation Loop (PRD §4.5 — langchain_deepagent_architecture_prd.md).

전략 성과 저장·학습: context, strategy, result, performance_score.
Evaluation: expected_result vs actual_result.
Learning: 성공 전략 가중치 증가, 실패 전략 가중치 감소.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STRATEGY_DIR = Path(__file__).resolve().parent.parent.parent / "strategy_memory"


def _strategy_path(prosumer_id: str) -> Path:
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in prosumer_id)
    _STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    return _STRATEGY_DIR / f"{safe_id}_strategies.jsonl"


def _load_entries(prosumer_id: str) -> list[dict]:
    path = _strategy_path(prosumer_id)
    if not path.exists():
        return []
    entries = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        pass
    return entries


def append_strategy_memory(
    prosumer_id: str,
    context: dict[str, Any],
    strategy: dict[str, Any],
    result: dict[str, Any],
    performance_score: float,
    expected_result: dict[str, Any] | None = None,
    actual_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    전략 한 건을 Strategy Memory에 추가 (PRD §4.5 저장 데이터).

    Args:
        prosumer_id: 프로슈머 ID
        context: context (plan, validation 요약 등)
        strategy: chosen strategy (ess_summary, trading, dr 등)
        result: result (실행/검증 결과 요약)
        performance_score: 0.0~1.0 (성공 높을수록 높음)
        expected_result: 시뮬레이션/예상 결과 (선택)
        actual_result: 실제 결과 (선택, 나중에 실행 피드백으로 채움)

    Returns:
        저장된 entry 요약
    """
    entry = {
        "context": _sanitize(context),
        "strategy": _sanitize(strategy),
        "result": _sanitize(result),
        "performance_score": performance_score,
        "expected_result": _sanitize(expected_result) if expected_result else None,
        "actual_result": _sanitize(actual_result) if actual_result else None,
        "weight": 1.0,  # 학습용 가중치 (성공 시 증가, 실패 시 감소)
    }
    path = _strategy_path(prosumer_id)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return {"performance_score": performance_score, "weight": entry["weight"]}


def _sanitize(obj: Any) -> Any:
    """JSON 직렬 가능한 값만 유지."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return str(obj)


def evaluate_and_update_weights(
    prosumer_id: str,
    run_id: str | None = None,
    last_n: int = 10,
    success_threshold: float = 0.7,
    weight_delta: float = 0.1,
) -> list[dict]:
    """
    Evaluation Loop: expected vs actual로 최근 N건 평가 후 가중치 갱신 (PRD §4.5 Learning).

    성공(performance_score >= success_threshold) → weight += weight_delta
    실패 → weight -= weight_delta (최소 0.1)

    Args:
        prosumer_id: 프로슈머 ID
        run_id: 특정 run만 갱신 시 (미사용 시 last_n 기준)
        last_n: 최근 N건만 갱신
        success_threshold: 성공 기준 점수
        weight_delta: 가중치 증감량

    Returns:
        갱신된 entry 목록 (요약)
    """
    path = _strategy_path(prosumer_id)
    if not path.exists():
        return []
    lines = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not entries:
        return []
    # 마지막 N건만 가중치 갱신
    to_update = entries[-last_n:] if len(entries) >= last_n else entries
    updated = []
    for i, e in enumerate(to_update):
        score = float(e.get("performance_score", 0.5))
        w = float(e.get("weight", 1.0))
        if score >= success_threshold:
            w = min(2.0, w + weight_delta)
        else:
            w = max(0.1, w - weight_delta)
        e["weight"] = w
        updated.append({"performance_score": score, "weight": w})
    # 파일 전체를 다시 쓰기 (갱신 반영)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for j, e in enumerate(entries):
                if j >= len(entries) - len(to_update):
                    idx = j - (len(entries) - len(to_update))
                    e["weight"] = to_update[idx].get("weight", e.get("weight", 1.0))
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return updated


def get_strategy_memory(
    prosumer_id: str,
    last_n: int = 20,
    min_weight: float = 0.0,
) -> list[dict]:
    """
    Strategy Memory 조회 (전략 재사용·Planner 참고용).

    Args:
        prosumer_id: 프로슈머 ID
        last_n: 최근 N건
        min_weight: 최소 가중치 필터

    Returns:
        entry 목록 (context, strategy, performance_score, weight)
    """
    entries = _load_entries(prosumer_id)
    filtered = [e for e in entries if float(e.get("weight", 0)) >= min_weight]
    return filtered[-last_n:]


def update_latest_strategy_actual_result(
    prosumer_id: str,
    actual_result: dict[str, Any],
    performance_score: float | None = None,
) -> dict[str, Any] | None:
    """
    최신 strategy memory entry에 실제 실행 결과를 반영한다.

    run_full_pipeline의 Step4/Step5 결과를 다음 라운드 전략 업데이트 입력으로 저장할 때 사용한다.
    """
    path = _strategy_path(prosumer_id)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = [json.loads(line) for line in f if line.strip()]
    except (OSError, json.JSONDecodeError):
        return None
    if not entries:
        return None
    latest = entries[-1]
    latest["actual_result"] = _sanitize(actual_result)
    if performance_score is not None:
        latest["performance_score"] = float(performance_score)
    try:
        with open(path, "w", encoding="utf-8") as f:
            for e in entries[:-1]:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.write(json.dumps(latest, ensure_ascii=False) + "\n")
    except OSError:
        return None
    return {
        "performance_score": latest.get("performance_score"),
        "weight": latest.get("weight", 1.0),
        "actual_result": latest.get("actual_result"),
    }
