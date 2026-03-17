"""
영구 메모리 저장소.
프로슈머 ID별로 이전 런의 요약(계획, 검증 지표, 의사결정)을 SQLite에 저장/로드합니다.
기존 JSON 파일이 있으면 최초 접근 시 DB로 마이그레이션합니다.
"""

from datetime import datetime, timezone
from pathlib import Path

from alfp.storage.db import get_connection, json_dumps, json_loads

_LEGACY_MEMORY_DIR = Path(__file__).resolve().parent.parent.parent / "memory_store"


def _memory_path(prosumer_id: str) -> Path:
    """기존 JSON 기반 메모리 파일 경로."""
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in prosumer_id)
    _LEGACY_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return _LEGACY_MEMORY_DIR / f"{safe_id}.json"


def load_memory(prosumer_id: str) -> dict:
    """
    해당 프로슈머의 이전 런 메모리를 로드합니다.
    없거나 실패 시 빈 dict 반환.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT data_json FROM persistent_memory WHERE prosumer_id = ?",
            (prosumer_id,),
        ).fetchone()
        if row:
            data = json_loads(row["data_json"])
            return data if isinstance(data, dict) else {}

    migrated = _migrate_legacy_memory(prosumer_id)
    return migrated if isinstance(migrated, dict) else {}


def save_memory(prosumer_id: str, data: dict) -> None:
    """
    해당 프로슈머의 런 요약을 저장합니다.
    data 예: last_plan, last_validation_metrics, last_decisions, last_run_at
    """
    updated_at = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO persistent_memory (prosumer_id, data_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(prosumer_id) DO UPDATE SET
                data_json = excluded.data_json,
                updated_at = excluded.updated_at
            """,
            (prosumer_id, json_dumps(data), updated_at),
        )
        conn.commit()


def _migrate_legacy_memory(prosumer_id: str) -> dict:
    path = _memory_path(prosumer_id)
    if not path.exists():
        return {}
    try:
        import json

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}

    if isinstance(data, dict):
        save_memory(prosumer_id, data)
        return data
    return {}
