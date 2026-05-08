"""Corpus retrieval — embed the query, dot product against the prebuilt index.

The index (`data/corpus_index.npz` + `data/corpus_index.json`) is built by
`scripts/index_corpus.py`. Embeddings are L2-normalised at build time, so
cosine similarity is just a dot product after we normalise the query vector.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INDEX_NPZ = PROJECT_ROOT / "data" / "corpus_index.npz"
INDEX_JSON = PROJECT_ROOT / "data" / "corpus_index.json"

EMBEDDING_MODEL = "text-embedding-3-large"


@dataclass
class Chunk:
    chunk_id: str
    source_page_id: str
    source_url: str
    source_title: str
    text: str
    score: float


def _load_index() -> tuple[np.ndarray, list[dict]]:
    if not INDEX_NPZ.exists() or not INDEX_JSON.exists():
        raise FileNotFoundError(
            "Corpus index not found. Run scripts/index_corpus.py before starting the server."
        )
    with np.load(INDEX_NPZ) as npz:
        embeddings = npz["embeddings"].astype(np.float32)
    metadata = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
    if embeddings.shape[0] != len(metadata):
        raise ValueError(
            f"Index mismatch: {embeddings.shape[0]} embeddings vs {len(metadata)} metadata entries"
        )
    return embeddings, metadata


_EMBEDDINGS, _METADATA = _load_index()
_PAGE_META: dict[str, dict[str, str]] = {}
for _m in _METADATA:
    _pid = _m["source_page_id"]
    if _pid not in _PAGE_META:
        _PAGE_META[_pid] = {"title": _m["source_title"], "url": _m["source_url"]}
_CLIENT: OpenAI | None = None


def get_page_meta(page_id: str) -> dict[str, str] | None:
    return _PAGE_META.get(page_id)


def _client() -> OpenAI:
    global _CLIENT
    if _CLIENT is None:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip().strip('"')
        if not api_key or api_key.upper().startswith("PLACEHOLDER"):
            raise RuntimeError("OPENAI_API_KEY missing or placeholder.")
        _CLIENT = OpenAI(api_key=api_key)
    return _CLIENT


def _embed_query(query: str) -> np.ndarray:
    resp = _client().embeddings.create(model=EMBEDDING_MODEL, input=query)
    vec = np.array(resp.data[0].embedding, dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def retrieve(query: str, k: int = 5) -> list[Chunk]:
    """Top-K most similar chunks to `query` by cosine similarity."""
    if not query.strip():
        return []
    q = _embed_query(query)
    scores = _EMBEDDINGS @ q
    top_idx = np.argsort(-scores)[:k]
    out: list[Chunk] = []
    for i in top_idx:
        meta = _METADATA[int(i)]
        out.append(
            Chunk(
                chunk_id=meta["chunk_id"],
                source_page_id=meta["source_page_id"],
                source_url=meta["source_url"],
                source_title=meta["source_title"],
                text=meta["text"],
                score=float(scores[int(i)]),
            )
        )
    return out
