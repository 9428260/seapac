"""
LLM 입출력 로깅.

LangChain 콜백으로 모든 LLM 호출의 입력/출력을 SQLite와 logs/ 파일에 기록합니다.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from alfp.storage.db import get_connection

_LOCK = threading.Lock()
# 입출력 건수 (쓰레드 세이프)
_input_count = 0
_output_count = 0
_count_lock = threading.Lock()
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _PROJECT_ROOT / "logs"


def _llm_log_path(ts: datetime | None = None) -> Path:
    now = ts or datetime.now()
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR / f"llm_io_{now.strftime('%Y%m%d')}.log"


def _next_input_count() -> int:
    with _count_lock:
        global _input_count
        _input_count += 1
        return _input_count


def _next_output_count() -> int:
    with _count_lock:
        global _output_count
        _output_count += 1
        return _output_count


def _message_to_str(msg: BaseMessage) -> str:
    """BaseMessage를 로그용 문자열로 변환."""
    role = getattr(msg, "type", "message")
    content = getattr(msg, "content", str(msg))
    if isinstance(content, str):
        return f"[{role}]\n{content}"
    return f"[{role}]\n{content!r}"


def _write_log(direction: str, run_id: Any, input_count: int | None, output_count: int | None, payload: str) -> None:
    with _LOCK:
        ts = datetime.now()
        log_path = _llm_log_path(ts)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(payload if payload.endswith("\n") else payload + "\n")
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_io_logs (created_at, direction, run_id, input_count, output_count, payload_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    direction,
                    str(run_id) if run_id is not None else None,
                    input_count,
                    output_count,
                    payload,
                ),
            )
            conn.commit()


class LLMIOHandler(BaseCallbackHandler):
    """LLM 호출 시 입출력을 SQLite에 기록하는 콜백."""

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 입력 로깅 (프롬프트 문자열)."""
        n = _next_input_count()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"\n{'='*60}", f"[{ts}] LLM INPUT #{n} (입력 누적 {n}건) run_id={run_id}", "---"]
        for i, p in enumerate(prompts):
            parts.append(f"[prompt_{i}]\n{p}")
        parts.append("")
        _write_log("input", run_id, n, None, "\n".join(parts))

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """채팅 모델 입력 로깅."""
        n = _next_input_count()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"\n{'='*60}", f"[{ts}] LLM INPUT #{n} (입력 누적 {n}건) run_id={run_id}", "---"]
        for batch in messages:
            for msg in batch:
                parts.append(_message_to_str(msg))
                parts.append("")
        parts.append("")
        _write_log("input", run_id, n, None, "\n".join(parts))

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """LLM 출력 로깅."""
        n = _next_output_count()
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        parts = [f"[{ts}] LLM OUTPUT #{n} (출력 누적 {n}건) run_id={run_id}", "---"]
        for gen_list in response.generations:
            for gen in gen_list:
                text = getattr(gen, "text", None) or getattr(gen, "message", None)
                if hasattr(text, "content"):
                    text = text.content
                if text is not None:
                    parts.append(str(text))
        parts.append("")
        _write_log("output", run_id, None, n, "\n".join(parts))

    def on_llm_error(self, error: BaseException, *args: Any, **kwargs: Any) -> None:
        """LLM 오류 시 로깅."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _count_lock:
            in_c, out_c = _input_count, _output_count
        _write_log(
            "error",
            kwargs.get("run_id"),
            in_c,
            out_c,
            f"[{ts}] LLM ERROR (입력 누적 {in_c}건 / 출력 누적 {out_c}건): {type(error).__name__}: {error}\n",
        )


def get_llm_io_handler() -> LLMIOHandler:
    """LLM 입출력 로깅용 콜백 핸들러 인스턴스를 반환합니다."""
    return LLMIOHandler()
