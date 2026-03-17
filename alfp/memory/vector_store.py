"""
OpenAI embeddings + Chroma 기반 strategy memory vector store.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CHROMA_DIR = _PROJECT_ROOT / "alfp_store" / "chroma"
_COLLECTION_NAME = "alfp_strategy_memory"
_ENV_PATH = _PROJECT_ROOT / ".env"
load_dotenv(_ENV_PATH, override=False)

_EMBEDDING_MODEL = os.environ.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
_AZURE_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "").strip()


def get_vector_store_config() -> dict[str, str]:
    backend = _get_embedding_backend()
    return {
        "provider": backend["provider"],
        "model": backend["model"],
        "collection": _COLLECTION_NAME,
        "path": str(_CHROMA_DIR),
    }


def upsert_strategy_memory_vector(
    *,
    entry_id: int,
    prosumer_id: str,
    document: str,
    metadata: dict[str, Any],
) -> dict[str, str]:
    collection = _get_collection()
    embedding = _embed_texts([document])[0]
    chroma_id = _vector_id(entry_id)
    sanitized = _sanitize_metadata({**metadata, "prosumer_id": prosumer_id, "entry_id": entry_id})
    collection.upsert(
        ids=[chroma_id],
        documents=[document],
        embeddings=[embedding],
        metadatas=[sanitized],
    )
    return {
        "collection_name": _COLLECTION_NAME,
        "chroma_id": chroma_id,
        "embedding_provider": get_vector_store_config()["provider"],
        "embedding_model": get_vector_store_config()["model"],
        "content_sha": hashlib.sha256(document.encode("utf-8")).hexdigest(),
    }


def query_strategy_memory_vectors(
    *,
    prosumer_id: str,
    query_text: str,
    top_k: int,
    metadata_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    collection = _get_collection()
    query_embedding = _embed_texts([query_text])[0]
    where = _sanitize_metadata({"prosumer_id": prosumer_id, **(metadata_filter or {})})
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
        include=["metadatas", "documents", "distances"],
    )
    ids = (result.get("ids") or [[]])[0]
    metadatas = (result.get("metadatas") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    distances = (result.get("distances") or [[]])[0]
    rows: list[dict[str, Any]] = []
    for idx, chroma_id in enumerate(ids):
        rows.append({
            "chroma_id": chroma_id,
            "entry_id": int((metadatas[idx] or {}).get("entry_id")),
            "metadata": metadatas[idx] or {},
            "document": documents[idx] if idx < len(documents) else "",
            "distance": float(distances[idx]) if idx < len(distances) else 1.0,
        })
    return rows


def delete_strategy_memory_vector(entry_id: int) -> None:
    collection = _get_collection()
    collection.delete(ids=[_vector_id(entry_id)])


def _get_collection():
    import chromadb

    _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    return client.get_or_create_collection(
        name=_COLLECTION_NAME,
        configuration={"hnsw": {"space": "cosine"}},
    )


def _embed_texts(texts: list[str]) -> list[list[float]]:
    backend = _get_embedding_backend()
    client = backend["client"]
    response = client.embeddings.create(
        model=backend["model"],
        input=texts,
        encoding_format="float",
    )
    return [item.embedding for item in response.data]


def _vector_id(entry_id: int) -> str:
    return f"strategy-memory-{entry_id}"


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            sanitized[key] = value
        else:
            sanitized[key] = str(value)
    return sanitized


def _get_embedding_backend() -> dict[str, Any]:
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if openai_api_key:
        return {
            "provider": "openai",
            "model": _EMBEDDING_MODEL,
            "client": OpenAI(
                api_key=openai_api_key,
                base_url=os.environ.get("OPENAI_BASE_URL") or None,
            ),
        }

    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
    azure_api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    azure_api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "").strip() or "2024-08-01-preview"
    azure_embedding_deployment = (
        _AZURE_EMBEDDING_DEPLOYMENT
        or os.environ.get("AZURE_OPENAI_EMBEDDING_MODEL", "").strip()
        or os.environ.get("OPENAI_EMBEDDING_MODEL", "").strip()
    )
    if azure_endpoint and azure_api_key and azure_embedding_deployment:
        return {
            "provider": "azure_openai",
            "model": azure_embedding_deployment,
            "client": AzureOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_api_key,
                api_version=azure_api_version,
            ),
        }

    raise EnvironmentError(
        "Embedding backend is not configured. Set OPENAI_API_KEY for OpenAI, "
        "or set AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_API_VERSION, "
        "and AZURE_OPENAI_EMBEDDING_DEPLOYMENT for Azure OpenAI embeddings."
    )
