"""
SQLite DB for pipeline run and stage results.

Schema:
  - pipeline_run: one row per full pipeline execution
  - pipeline_stage: one row per architecture step (ALFP, MESA, Step2, ...)
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_DEFAULT_DB_DIR = Path(__file__).resolve().parent.parent / "output"
_DB_FILENAME = "pipeline_runs.db"


def get_db_path(db_dir: Path | str | None = None) -> Path:
    """Return path to SQLite DB file."""
    if db_dir is None:
        db_dir = _DEFAULT_DB_DIR
    return Path(db_dir) / _DB_FILENAME


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create tables if they do not exist."""
    path = db_path or get_db_path()
    conn = _connect(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_run (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                total_elapsed_sec REAL,
                args_json TEXT,
                error_message TEXT
            );
            CREATE TABLE IF NOT EXISTS pipeline_stage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                stage_order INTEGER NOT NULL,
                stage_name TEXT NOT NULL,
                ok INTEGER NOT NULL,
                elapsed_sec REAL NOT NULL DEFAULT 0,
                summary_json TEXT,
                error_text TEXT,
                FOREIGN KEY (run_id) REFERENCES pipeline_run(id)
            );
            CREATE INDEX IF NOT EXISTS ix_stage_run_id ON pipeline_stage(run_id);
            CREATE INDEX IF NOT EXISTS ix_run_created ON pipeline_run(created_at DESC);
        """)
        try:
            conn.execute("ALTER TABLE pipeline_run ADD COLUMN measure_date TEXT")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


@dataclass
class RunRecord:
    id: int
    created_at: str
    finished_at: str | None
    status: str
    total_elapsed_sec: float | None
    args_json: str | None
    error_message: str | None


@dataclass
class StageRecord:
    id: int
    run_id: int
    stage_order: int
    stage_name: str
    ok: bool
    elapsed_sec: float
    summary_json: str | None
    error_text: str | None


def create_run(args: Any, db_path: Path | None = None) -> int:
    """Create a new pipeline run; return run_id. args may include measure_date (YYYY-MM-DD)."""
    path = db_path or get_db_path()
    init_db(path)
    conn = _connect(path)
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        args_dict = None
        if args is not None:
            try:
                args_dict = vars(args) if hasattr(args, "__dict__") else args
            except Exception:
                args_dict = {}
        else:
            args_dict = {}
        args_json = json.dumps(args_dict, default=str, ensure_ascii=False) if args_dict else "{}"
        measure_date = args_dict.get("measure_date") if isinstance(args_dict, dict) else None
        cur = conn.execute(
            """INSERT INTO pipeline_run (created_at, status, args_json, measure_date)
               VALUES (?, 'running', ?, ?)""",
            (now, args_json, measure_date),
        )
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def add_stage(
    run_id: int,
    stage_order: int,
    stage_name: str,
    ok: bool,
    elapsed_sec: float,
    summary: dict[str, Any] | None = None,
    error_text: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Append one stage result to a run."""
    path = db_path or get_db_path()
    conn = _connect(path)
    try:
        summary_json = json.dumps(summary or {}, ensure_ascii=False) if summary else None
        conn.execute(
            """INSERT INTO pipeline_stage
               (run_id, stage_order, stage_name, ok, elapsed_sec, summary_json, error_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, stage_order, stage_name, 1 if ok else 0, elapsed_sec, summary_json, error_text),
        )
        conn.commit()
    finally:
        conn.close()


def finish_run(
    run_id: int,
    total_elapsed_sec: float,
    ok: bool = True,
    error_message: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Mark run as finished and set total time and status."""
    path = db_path or get_db_path()
    conn = _connect(path)
    try:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        status = "success" if ok else "failure"
        conn.execute(
            """UPDATE pipeline_run
               SET finished_at = ?, status = ?, total_elapsed_sec = ?, error_message = ?
               WHERE id = ?""",
            (now, status, total_elapsed_sec, error_message, run_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_runs(
    limit: int = 50,
    prosumer: str | None = None,
    run_date: str | None = None,
    measure_date: str | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    """List recent runs (newest first). Filter by prosumer (from args), run_date (created_at date), measure_date."""
    path = db_path or get_db_path()
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        fetch_limit = 500 if (prosumer or run_date or measure_date) else limit
        rows = conn.execute(
            """SELECT id, created_at, finished_at, status, total_elapsed_sec, args_json, error_message, measure_date
               FROM pipeline_run ORDER BY id DESC LIMIT ?""",
            (fetch_limit,),
        ).fetchall()
        out = []
        for r in rows:
            args = json.loads(r["args_json"]) if r["args_json"] else {}
            if prosumer and (args.get("prosumer") or "").strip() != (prosumer or "").strip():
                continue
            created = (r["created_at"] or "")[:10]
            if run_date and created != run_date:
                continue
            try:
                row_measure = r["measure_date"]
            except (KeyError, TypeError):
                row_measure = None
            if row_measure is None and isinstance(args, dict):
                row_measure = args.get("measure_date")
            if measure_date and row_measure != measure_date:
                continue
            out.append({
                "id": r["id"],
                "created_at": r["created_at"],
                "finished_at": r["finished_at"],
                "status": r["status"],
                "total_elapsed_sec": r["total_elapsed_sec"],
                "args": args,
                "error_message": r["error_message"],
                "measure_date": row_measure,
            })
            if len(out) >= limit:
                break
        return out
    finally:
        conn.close()


def get_run_with_stages(run_id: int, db_path: Path | None = None) -> dict[str, Any] | None:
    """Get one run and its stages in order."""
    path = db_path or get_db_path()
    if not path.exists():
        return None
    conn = _connect(path)
    try:
        run_row = conn.execute(
            "SELECT id, created_at, finished_at, status, total_elapsed_sec, args_json, error_message, measure_date FROM pipeline_run WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not run_row:
            return None
        args = json.loads(run_row["args_json"]) if run_row["args_json"] else {}
        try:
            measure_date_val = run_row["measure_date"]
        except (KeyError, TypeError):
            measure_date_val = args.get("measure_date")
        stages = conn.execute(
            """SELECT id, run_id, stage_order, stage_name, ok, elapsed_sec, summary_json, error_text
               FROM pipeline_stage WHERE run_id = ? ORDER BY stage_order, id""",
            (run_id,),
        ).fetchall()
        return {
            "id": run_row["id"],
            "created_at": run_row["created_at"],
            "finished_at": run_row["finished_at"],
            "status": run_row["status"],
            "total_elapsed_sec": run_row["total_elapsed_sec"],
            "args": args,
            "error_message": run_row["error_message"],
            "measure_date": measure_date_val,
            "stages": [
                {
                    "id": s["id"],
                    "stage_order": s["stage_order"],
                    "stage_name": s["stage_name"],
                    "ok": bool(s["ok"]),
                    "elapsed_sec": s["elapsed_sec"],
                    "summary": json.loads(s["summary_json"]) if s["summary_json"] else {},
                    "error_text": s["error_text"],
                }
                for s in stages
            ],
        }
    finally:
        conn.close()
