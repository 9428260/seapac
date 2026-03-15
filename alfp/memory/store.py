"""
영구 메모리 저장소.
프로슈머 ID별로 이전 런의 요약(계획, 검증 지표, 의사결정)을 JSON 파일로 저장/로드합니다.
"""

import json
from pathlib import Path
from typing import Any

_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory_store"


def _memory_path(prosumer_id: str) -> Path:
    """프로슈머별 메모리 파일 경로. prosumer_id는 파일명에 안전하게 사용."""
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in prosumer_id)
    _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return _MEMORY_DIR / f"{safe_id}.json"


def load_memory(prosumer_id: str) -> dict:
    """
    해당 프로슈머의 이전 런 메모리를 로드합니다.
    없거나 실패 시 빈 dict 반환.
    """
    path = _memory_path(prosumer_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_memory(prosumer_id: str, data: dict) -> None:
    """
    해당 프로슈머의 런 요약을 저장합니다.
    data 예: last_plan, last_validation_metrics, last_decisions, last_run_at
    """
    path = _memory_path(prosumer_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
