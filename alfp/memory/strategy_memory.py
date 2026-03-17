"""
Strategy Memory + Evaluation Loop (PRD §4.5 — langchain_deepagent_architecture_prd.md).

전략 성과 저장·학습: context, strategy, result, performance_score.
Evaluation: expected_result vs actual_result.
Learning: 성공 전략 가중치 증가, 실패 전략 가중치 감소.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alfp.memory.vector_store import (
    get_vector_store_config,
    query_strategy_memory_vectors,
    upsert_strategy_memory_vector,
)
from alfp.storage.db import get_connection, json_dumps, json_loads

_LEGACY_STRATEGY_DIR = Path(__file__).resolve().parent.parent.parent / "strategy_memory"


def _strategy_path(prosumer_id: str) -> Path:
    """Legacy JSONL path 계산만 수행. 디렉토리 생성은 하지 않는다."""
    safe_id = "".join(c if c.isalnum() or c in "._-" else "_" for c in prosumer_id)
    return _LEGACY_STRATEGY_DIR / f"{safe_id}_strategies.jsonl"


def _load_entries(prosumer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                e.id,
                e.prosumer_id,
                e.created_at,
                e.performance_score,
                e.weight,
                d.context_json,
                d.strategy_json,
                d.result_json,
                d.expected_result_json,
                d.actual_result_json,
                d.retrieval_text,
                m.season,
                m.weather,
                m.tariff,
                m.prosumer_type,
                m.operating_mode,
                m.forecast_horizon_bucket,
                m.tags_json,
                vi.vector_store,
                vi.collection_name,
                vi.chroma_id,
                vi.embedding_provider,
                vi.embedding_model,
                vi.content_sha,
                vi.indexed_at
            FROM strategy_memory_entries e
            JOIN strategy_memory_documents d ON d.entry_id = e.id
            LEFT JOIN strategy_memory_metadata m ON m.entry_id = e.id
            LEFT JOIN strategy_memory_vector_index vi ON vi.entry_id = e.id
            WHERE e.prosumer_id = ?
            ORDER BY e.created_at ASC, e.id ASC
            """,
            (prosumer_id,),
        ).fetchall()
    if rows:
        return [_row_to_entry(row) for row in rows]

    migrated = _migrate_denormalized_entries(prosumer_id)
    if migrated:
        return migrated
    return _migrate_legacy_entries(prosumer_id)


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
    """
    sanitized_context = _sanitize(context)
    sanitized_strategy = _sanitize(strategy)
    sanitized_result = _sanitize(result)
    entry = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": sanitized_context,
        "strategy": sanitized_strategy,
        "result": sanitized_result,
        "performance_score": float(performance_score),
        "expected_result": _sanitize(expected_result) if expected_result else None,
        "actual_result": _sanitize(actual_result) if actual_result else None,
        "weight": 1.0,
    }
    inserted = _insert_entry(prosumer_id, entry)
    return {
        "memory_id": inserted["id"],
        "created_at": inserted["created_at"],
        "performance_score": inserted["performance_score"],
        "weight": inserted["weight"],
        "tags": inserted.get("tags", {}),
    }


def _sanitize(obj: Any) -> Any:
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
    entries = _load_entries(prosumer_id)
    if not entries:
        return []
    to_update = entries[-last_n:] if len(entries) >= last_n else entries
    updated = []
    with get_connection() as conn:
        for entry in to_update:
            score = float(entry.get("performance_score", 0.5))
            weight = float(entry.get("weight", 1.0))
            if score >= success_threshold:
                weight = min(2.0, weight + weight_delta)
            else:
                weight = max(0.1, weight - weight_delta)
            conn.execute(
                "UPDATE strategy_memory_entries SET weight = ? WHERE id = ?",
                (weight, int(entry["id"])),
            )
            updated.append({"performance_score": score, "weight": weight})
        conn.commit()
    return updated


def get_strategy_memory(
    prosumer_id: str,
    last_n: int = 20,
    min_weight: float = 0.0,
) -> list[dict]:
    entries = _load_entries(prosumer_id)
    filtered = [entry for entry in entries if float(entry.get("weight", 0.0)) >= min_weight]
    return filtered[-last_n:]


def retrieve_similar_cases(
    prosumer_id: str,
    current_context: dict[str, Any],
    top_k: int = 3,
    min_weight: float = 0.0,
) -> list[dict[str, Any]]:
    """
    현재 컨텍스트와 유사한 과거 사례를 검색한다.

    점수는 metadata similarity와 embedding cosine similarity를 혼합한다.
    """
    entries = get_strategy_memory(prosumer_id, last_n=300, min_weight=min_weight)
    if not entries:
        return []
    entry_by_id = {int(entry["id"]): entry for entry in entries}
    vector_hits = _query_vector_candidates(prosumer_id, current_context, top_k=max(top_k * 5, 10))
    vector_score_by_id = {hit["entry_id"]: hit["vector_score"] for hit in vector_hits}
    scored: list[dict[str, Any]] = []
    for entry_id, entry in entry_by_id.items():
        metadata_score, matched = _similarity_score(current_context, entry.get("context") or {})
        embedding_score = vector_score_by_id.get(entry_id, 0.0)
        combined = metadata_score * 0.55 + embedding_score * 0.45
        if combined <= 0:
            continue
        scored.append({
            "similarity_score": round(combined, 3),
            "metadata_score": round(metadata_score, 3),
            "embedding_score": round(embedding_score, 3),
            "matched_features": matched,
            "entry": entry,
            "strategy": entry.get("strategy") or {},
            "result": entry.get("result") or {},
        })
    scored.sort(
        key=lambda item: (
            item["similarity_score"],
            float((item["entry"] or {}).get("weight", 0.0)),
            float((item["entry"] or {}).get("performance_score", 0.0)),
        ),
        reverse=True,
    )
    return scored[:top_k]


def retrieve_best_practices(
    prosumer_id: str,
    current_context: dict[str, Any],
    top_k: int = 5,
    success_threshold: float = 0.7,
) -> dict[str, list[dict[str, Any]]]:
    entries = get_strategy_memory(prosumer_id, last_n=400, min_weight=0.0)
    best: dict[str, list[dict[str, Any]]] = {
        "season": [],
        "weather": [],
        "tariff": [],
        "prosumer_type": [],
    }
    if not entries:
        return best

    current_tags = _extract_tags(current_context)
    vector_hits = _query_vector_candidates(prosumer_id, current_context, top_k=50)
    vector_score_by_id = {hit["entry_id"]: hit["vector_score"] for hit in vector_hits}
    for facet in best:
        matched: list[dict[str, Any]] = []
        for entry in entries:
            score = float(entry.get("performance_score", 0.0))
            if score < success_threshold:
                continue
            entry_tags = _extract_tags(entry.get("context") or {})
            if current_tags.get(facet) and current_tags.get(facet) == entry_tags.get(facet):
                matched.append({
                    "facet": facet,
                    "tag": entry_tags.get(facet),
                    "performance_score": score,
                    "weight": float(entry.get("weight", 0.0)),
                    "embedding_score": round(vector_score_by_id.get(int(entry["id"]), 0.0), 3),
                    "strategy": entry.get("strategy") or {},
                    "result": entry.get("result") or {},
                    "entry": entry,
                })
        matched.sort(
            key=lambda item: (item["performance_score"], item["weight"], item["embedding_score"]),
            reverse=True,
        )
        best[facet] = matched[:top_k]
    return best


def retrieve_similar_failures(
    prosumer_id: str,
    current_context: dict[str, Any],
    top_k: int = 3,
    failure_threshold: float = 0.5,
) -> list[dict[str, Any]]:
    entries = get_strategy_memory(prosumer_id, last_n=300, min_weight=0.0)
    failures: list[dict[str, Any]] = []
    current_failure_tags = _extract_failure_tags(current_context)
    vector_hits = _query_vector_candidates(prosumer_id, current_context, top_k=max(top_k * 5, 10))
    vector_score_by_id = {hit["entry_id"]: hit["vector_score"] for hit in vector_hits}
    for entry in entries:
        if float(entry.get("performance_score", 1.0)) > failure_threshold:
            continue
        metadata_score, matched_features = _similarity_score(current_context, entry.get("context") or {})
        failure_similarity = _failure_similarity_score(current_failure_tags, _extract_failure_tags(entry.get("context") or {}))
        embedding_score = vector_score_by_id.get(int(entry["id"]), 0.0)
        total = metadata_score * 0.35 + failure_similarity * 0.35 + embedding_score * 0.30
        if total <= 0:
            continue
        failures.append({
            "similarity_score": round(total, 3),
            "metadata_score": round(metadata_score, 3),
            "embedding_score": round(embedding_score, 3),
            "matched_features": matched_features,
            "failure_pattern": _extract_failure_tags(entry.get("context") or {}),
            "entry": entry,
            "strategy": entry.get("strategy") or {},
            "result": entry.get("result") or {},
        })
    failures.sort(
        key=lambda item: (
            item["similarity_score"],
            -float((item["entry"] or {}).get("performance_score", 0.0)),
            float((item["entry"] or {}).get("weight", 0.0)),
        ),
        reverse=True,
    )
    return failures[:top_k]


def update_latest_strategy_actual_result(
    prosumer_id: str,
    actual_result: dict[str, Any],
    performance_score: float | None = None,
) -> dict[str, Any] | None:
    entries = _load_entries(prosumer_id)
    if not entries:
        return None
    latest = entries[-1]
    latest["actual_result"] = _sanitize(actual_result)
    if performance_score is not None:
        latest["performance_score"] = float(performance_score)
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE strategy_memory_documents
            SET actual_result_json = ?
            WHERE entry_id = ?
            """,
            (json_dumps(latest["actual_result"]), int(latest["id"])),
        )
        conn.execute(
            "UPDATE strategy_memory_entries SET performance_score = ? WHERE id = ?",
            (float(latest.get("performance_score", 0.0)), int(latest["id"])),
        )
        conn.commit()
    return {
        "performance_score": latest.get("performance_score"),
        "weight": latest.get("weight", 1.0),
        "actual_result": latest.get("actual_result"),
    }


def refresh_strategy_memory_embeddings(prosumer_id: str) -> int:
    """
    저장된 entry의 retrieval text를 OpenAI embeddings + Chroma 기준으로 재색인한다.
    """
    entries = _load_entries(prosumer_id)
    if not entries:
        return 0
    with get_connection() as conn:
        for entry in entries:
            try:
                _upsert_vector_record(
                    conn,
                    int(entry["id"]),
                    prosumer_id,
                    entry.get("context") or {},
                    entry.get("strategy") or {},
                    entry.get("result") or {},
                )
            except Exception:
                continue
        conn.commit()
    return len(entries)


def _insert_entry(prosumer_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    tags = _extract_tags(entry["context"])
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategy_memory_entries (
                prosumer_id, created_at, performance_score, weight
            ) VALUES (?, ?, ?, ?)
            """,
            (
                prosumer_id,
                entry["created_at"],
                float(entry["performance_score"]),
                float(entry["weight"]),
            ),
        )
        entry_id = int(cursor.lastrowid)
        retrieval_text = _build_retrieval_text(entry["context"], entry["strategy"], entry["result"])
        conn.execute(
            """
            INSERT INTO strategy_memory_documents (
                entry_id, context_json, strategy_json, result_json,
                expected_result_json, actual_result_json, retrieval_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                json_dumps(entry["context"]),
                json_dumps(entry["strategy"]),
                json_dumps(entry["result"]),
                json_dumps(entry["expected_result"]) if entry["expected_result"] is not None else None,
                json_dumps(entry["actual_result"]) if entry["actual_result"] is not None else None,
                retrieval_text,
            ),
        )
        conn.execute(
            """
            INSERT INTO strategy_memory_metadata (
                entry_id, season, weather, tariff, prosumer_type,
                operating_mode, forecast_horizon_bucket, tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                tags.get("season"),
                tags.get("weather"),
                tags.get("tariff"),
                tags.get("prosumer_type"),
                tags.get("operating_mode"),
                tags.get("forecast_horizon_bucket"),
                json_dumps(tags),
            ),
        )
        try:
            _upsert_vector_record(conn, entry_id, prosumer_id, entry["context"], entry["strategy"], entry["result"])
        except Exception:
            pass
        conn.commit()

    stored = dict(entry)
    stored["id"] = entry_id
    stored["tags"] = tags
    stored["retrieval_text"] = retrieval_text
    vector_config = get_vector_store_config()
    stored["vector_store"] = "chroma"
    stored["collection_name"] = vector_config["collection"]
    stored["embedding_provider"] = vector_config["provider"]
    stored["embedding_model"] = vector_config["model"]
    return stored


def _upsert_vector_record(
    conn: Any,
    entry_id: int,
    prosumer_id: str,
    context: dict[str, Any],
    strategy: dict[str, Any],
    result: dict[str, Any],
) -> None:
    retrieval_text = _build_retrieval_text(context, strategy, result)
    tags = _extract_tags(context)
    vector_info = upsert_strategy_memory_vector(
        entry_id=entry_id,
        prosumer_id=prosumer_id,
        document=retrieval_text,
        metadata=tags,
    )
    conn.execute(
        """
        INSERT INTO strategy_memory_vector_index (
            entry_id, vector_store, collection_name, chroma_id,
            embedding_provider, embedding_model, content_sha, indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(entry_id) DO UPDATE SET
            vector_store = excluded.vector_store,
            collection_name = excluded.collection_name,
            chroma_id = excluded.chroma_id,
            embedding_provider = excluded.embedding_provider,
            embedding_model = excluded.embedding_model,
            content_sha = excluded.content_sha,
            indexed_at = excluded.indexed_at
        """,
        (
            entry_id,
            "chroma",
            vector_info["collection_name"],
            vector_info["chroma_id"],
            vector_info["embedding_provider"],
            vector_info["embedding_model"],
            vector_info["content_sha"],
            datetime.now(timezone.utc).isoformat(),
        ),
    )


def _row_to_entry(row: Any) -> dict[str, Any]:
    context = json_loads(row["context_json"]) or {}
    tags = json_loads(row["tags_json"]) if row["tags_json"] else _extract_tags(context)
    return {
        "id": int(row["id"]),
        "prosumer_id": row["prosumer_id"],
        "created_at": row["created_at"],
        "context": context,
        "strategy": json_loads(row["strategy_json"]) or {},
        "result": json_loads(row["result_json"]) or {},
        "performance_score": float(row["performance_score"]),
        "expected_result": json_loads(row["expected_result_json"]) if row["expected_result_json"] else None,
        "actual_result": json_loads(row["actual_result_json"]) if row["actual_result_json"] else None,
        "weight": float(row["weight"]),
        "tags": tags or {},
        "retrieval_text": row["retrieval_text"],
        "vector_store": row["vector_store"],
        "collection_name": row["collection_name"],
        "chroma_id": row["chroma_id"],
        "embedding_provider": row["embedding_provider"],
        "embedding_model": row["embedding_model"],
        "content_sha": row["content_sha"],
        "indexed_at": row["indexed_at"],
    }


def _migrate_denormalized_entries(prosumer_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM strategy_memory
            WHERE prosumer_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (prosumer_id,),
        ).fetchall()
    if not rows:
        return []
    for row in rows:
        entry = {
            "created_at": row["created_at"],
            "context": json_loads(row["context_json"]) or {},
            "strategy": json_loads(row["strategy_json"]) or {},
            "result": json_loads(row["result_json"]) or {},
            "performance_score": float(row["performance_score"]),
            "expected_result": json_loads(row["expected_result_json"]) if row["expected_result_json"] else None,
            "actual_result": json_loads(row["actual_result_json"]) if row["actual_result_json"] else None,
            "weight": float(row["weight"]),
        }
        _insert_entry(prosumer_id, entry)
    return _load_entries(prosumer_id)


def _migrate_legacy_entries(prosumer_id: str) -> list[dict]:
    """기존 JSONL 파일이 실제로 있을 때만 1회성 마이그레이션을 시도한다."""
    path = _strategy_path(prosumer_id)
    if not path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
    except (OSError, json.JSONDecodeError):
        return []
    for index, entry in enumerate(entries):
        created_at = entry.get("created_at") or f"{datetime.now(timezone.utc).isoformat()}#{index}"
        normalized = {
            "created_at": created_at,
            "context": _sanitize(entry.get("context") or {}),
            "strategy": _sanitize(entry.get("strategy") or {}),
            "result": _sanitize(entry.get("result") or {}),
            "performance_score": float(entry.get("performance_score", 0.0)),
            "expected_result": _sanitize(entry.get("expected_result")) if entry.get("expected_result") is not None else None,
            "actual_result": _sanitize(entry.get("actual_result")) if entry.get("actual_result") is not None else None,
            "weight": float(entry.get("weight", 1.0)),
        }
        _insert_entry(prosumer_id, normalized)
    return _load_entries(prosumer_id)


def _extract_tags(context: dict[str, Any]) -> dict[str, Any]:
    tags = dict((context or {}).get("tags") or {})
    plan = (context or {}).get("plan") or {}
    stats = (context or {}).get("stats") or {}
    tags.setdefault("prosumer_type", plan.get("prosumer_type") or stats.get("prosumer_type"))
    tags.setdefault("season", stats.get("season"))
    tags.setdefault("weather", stats.get("weather_label"))
    tags.setdefault("tariff", stats.get("tariff_profile"))
    tags.setdefault("operating_mode", context.get("operating_mode"))
    tags.setdefault("forecast_horizon_bucket", context.get("forecast_horizon_bucket") or stats.get("forecast_horizon_bucket"))
    return {key: value for key, value in tags.items() if value not in (None, "", "Unknown")}


def _extract_failure_tags(context: dict[str, Any]) -> dict[str, Any]:
    validation_kpi = (context or {}).get("validation_kpi") or {}
    return {
        "mape_pass": validation_kpi.get("MAPE_pass"),
        "peak_acc_pass": validation_kpi.get("peak_acc_pass"),
        "mape_achieved": validation_kpi.get("MAPE_achieved"),
        "peak_acc_achieved": validation_kpi.get("peak_acc_achieved"),
        "selected_model": ((context or {}).get("plan") or {}).get("selected_model"),
    }


def _similarity_score(current_context: dict[str, Any], past_context: dict[str, Any]) -> tuple[float, list[str]]:
    current_tags = _extract_tags(current_context)
    past_tags = _extract_tags(past_context)
    weights = {
        "prosumer_type": 0.25,
        "season": 0.2,
        "weather": 0.2,
        "tariff": 0.15,
        "operating_mode": 0.1,
        "forecast_horizon_bucket": 0.1,
    }
    score = 0.0
    matched: list[str] = []

    for key, weight in weights.items():
        current_value = current_tags.get(key) or current_context.get(key) or ((current_context.get("stats") or {}).get(key))
        past_value = past_tags.get(key) or past_context.get(key) or ((past_context.get("stats") or {}).get(key))
        if current_value and current_value == past_value:
            score += weight
            matched.append(f"{key}={current_value}")

    current_stats = current_context.get("stats") or {}
    past_stats = past_context.get("stats") or {}
    for key, weight in [("load_cv_bucket", 0.08), ("pv_ratio_bucket", 0.07)]:
        if current_stats.get(key) and current_stats.get(key) == past_stats.get(key):
            score += weight
            matched.append(f"{key}={current_stats.get(key)}")
    return min(score, 1.0), matched


def _failure_similarity_score(current_failure: dict[str, Any], past_failure: dict[str, Any]) -> float:
    score = 0.0
    if current_failure.get("mape_pass") is False and past_failure.get("mape_pass") is False:
        score += 0.4
    if current_failure.get("peak_acc_pass") is False and past_failure.get("peak_acc_pass") is False:
        score += 0.4
    if current_failure.get("selected_model") and current_failure.get("selected_model") == past_failure.get("selected_model"):
        score += 0.2
    return min(score, 1.0)


def _build_retrieval_text(context: dict[str, Any], strategy: dict[str, Any], result: dict[str, Any]) -> str:
    tags = _extract_tags(context)
    failure = _extract_failure_tags(context)
    parts = [
        f"prosumer_type={tags.get('prosumer_type', '')}",
        f"season={tags.get('season', '')}",
        f"weather={tags.get('weather', '')}",
        f"tariff={tags.get('tariff', '')}",
        f"operating_mode={tags.get('operating_mode', '')}",
        f"horizon={tags.get('forecast_horizon_bucket', '')}",
        f"context={json_dumps(context)}",
        f"strategy={json_dumps(strategy)}",
        f"result={json_dumps(result)}",
        f"failure={json_dumps(failure)}",
    ]
    return "\n".join(parts)


def _query_vector_candidates(
    prosumer_id: str,
    current_context: dict[str, Any],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    query_text = _build_retrieval_text(current_context, {}, {})
    try:
        hits = query_strategy_memory_vectors(
            prosumer_id=prosumer_id,
            query_text=query_text,
            top_k=top_k,
        )
    except Exception:
        return []
    return [
        {
            **hit,
            "vector_score": _distance_to_similarity(hit.get("distance", 1.0)),
        }
        for hit in hits
        if hit.get("entry_id") is not None
    ]


def _distance_to_similarity(distance: float) -> float:
    normalized = 1.0 - max(0.0, min(float(distance), 2.0)) / 2.0
    return round(max(0.0, min(normalized, 1.0)), 6)
