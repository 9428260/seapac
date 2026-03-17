"""
Common helpers for ALFP deepagents orchestration.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


def extract_structured_response(result: dict[str, Any], *, error_message: str) -> dict[str, Any]:
    """Normalize deepagents structured responses into plain dictionaries."""
    structured = result.get("structured_response")
    if structured is None:
        raise RuntimeError(error_message)
    if isinstance(structured, BaseModel):
        return structured.model_dump(by_alias=True)
    if hasattr(structured, "dict"):
        return structured.dict(by_alias=True)
    if isinstance(structured, dict):
        return structured
    raise TypeError(f"Unsupported structured_response type: {type(structured).__name__}")
