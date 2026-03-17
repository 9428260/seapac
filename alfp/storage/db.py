"""
ALFP 공용 SQLite 저장소.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DB_DIR = _PROJECT_ROOT / "alfp_store"
_DB_PATH = _DB_DIR / "alfp.sqlite3"
_LOCK = threading.Lock()


def get_db_path() -> Path:
    _DB_DIR.mkdir(parents=True, exist_ok=True)
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _initialize(conn)
    return conn


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def _initialize(conn: sqlite3.Connection) -> None:
    with _LOCK:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS persistent_memory (
                prosumer_id TEXT PRIMARY KEY,
                data_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prosumer_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                season TEXT,
                weather TEXT,
                tariff TEXT,
                prosumer_type TEXT,
                operating_mode TEXT,
                forecast_horizon_bucket TEXT,
                performance_score REAL NOT NULL,
                weight REAL NOT NULL,
                context_json TEXT NOT NULL,
                strategy_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                expected_result_json TEXT,
                actual_result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS strategy_memory_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prosumer_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                performance_score REAL NOT NULL,
                weight REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_strategy_memory_entries_prosumer_created
            ON strategy_memory_entries (prosumer_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS strategy_memory_documents (
                entry_id INTEGER PRIMARY KEY,
                context_json TEXT NOT NULL,
                strategy_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                expected_result_json TEXT,
                actual_result_json TEXT,
                retrieval_text TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES strategy_memory_entries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS strategy_memory_metadata (
                entry_id INTEGER PRIMARY KEY,
                season TEXT,
                weather TEXT,
                tariff TEXT,
                prosumer_type TEXT,
                operating_mode TEXT,
                forecast_horizon_bucket TEXT,
                tags_json TEXT,
                FOREIGN KEY(entry_id) REFERENCES strategy_memory_entries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_strategy_memory_metadata_tags
            ON strategy_memory_metadata (
                prosumer_type,
                season,
                weather,
                tariff,
                operating_mode,
                forecast_horizon_bucket
            );

            CREATE TABLE IF NOT EXISTS strategy_memory_embeddings (
                entry_id INTEGER PRIMARY KEY,
                embedding_model TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding_json TEXT NOT NULL,
                content_sha TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES strategy_memory_entries(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS strategy_memory_vector_index (
                entry_id INTEGER PRIMARY KEY,
                vector_store TEXT NOT NULL,
                collection_name TEXT NOT NULL,
                chroma_id TEXT NOT NULL UNIQUE,
                embedding_provider TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                content_sha TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                FOREIGN KEY(entry_id) REFERENCES strategy_memory_entries(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_strategy_memory_prosumer_created
            ON strategy_memory (prosumer_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_strategy_memory_tags
            ON strategy_memory (
                prosumer_id,
                prosumer_type,
                season,
                weather,
                tariff,
                operating_mode,
                forecast_horizon_bucket
            );

            CREATE TABLE IF NOT EXISTS llm_io_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                direction TEXT NOT NULL,
                run_id TEXT,
                input_count INTEGER,
                output_count INTEGER,
                payload_text TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_llm_io_logs_created
            ON llm_io_logs (created_at DESC);
            """
        )
        conn.commit()
