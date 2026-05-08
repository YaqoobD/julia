"""Chunk + embed the scraped corpus into data/corpus_index.npz + corpus_index.json.

Reads every .md file in data/corpus/ (skipping sources.txt), strips the source_url
and source_title header comments, splits each body into 500-token chunks with
50-token overlap using cl100k_base, embeds each chunk via OpenAI's
text-embedding-3-large, L2-normalises the resulting matrix, and saves to:

  data/corpus_index.npz   — float32 (N, 3072), L2-normalised, key 'embeddings'
  data/corpus_index.json  — parallel list of {chunk_id, source_page_id,
                            source_url, source_title, text}

Run from project root (with .env containing OPENAI_API_KEY):

    .venv/bin/python scripts/index_corpus.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import tiktoken
from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS_DIR = PROJECT_ROOT / "data" / "corpus"
INDEX_NPZ = PROJECT_ROOT / "data" / "corpus_index.npz"
INDEX_JSON = PROJECT_ROOT / "data" / "corpus_index.json"

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMS = 3072
CHUNK_TOKENS = 500
CHUNK_OVERLAP = 50
BATCH_SIZE = 100
# text-embedding-3-large list price (USD per 1M input tokens) as of 2026.
COST_PER_MILLION_TOKENS = 0.13

HEADER_URL_RE = re.compile(r"<!--\s*source_url:\s*(.*?)\s*-->")
HEADER_TITLE_RE = re.compile(r"<!--\s*source_title:\s*(.*?)\s*-->")
HEADER_BLOCK_RE = re.compile(r"^(?:<!--.*?-->\s*)+", re.DOTALL)


def parse_corpus_file(path: Path) -> tuple[str, str, str]:
    """Return (source_url, source_title, body) for a corpus markdown file."""
    text = path.read_text(encoding="utf-8")
    url_m = HEADER_URL_RE.search(text)
    title_m = HEADER_TITLE_RE.search(text)
    source_url = url_m.group(1) if url_m else ""
    source_title = title_m.group(1) if title_m else path.stem
    body = HEADER_BLOCK_RE.sub("", text).strip()
    return source_url, source_title, body


def chunk_text(text: str, encoding: tiktoken.Encoding) -> list[str]:
    """Split text into CHUNK_TOKENS-token chunks with CHUNK_OVERLAP overlap."""
    tokens = encoding.encode(text)
    if not tokens:
        return []
    chunks: list[str] = []
    start = 0
    step = CHUNK_TOKENS - CHUNK_OVERLAP
    while start < len(tokens):
        end = start + CHUNK_TOKENS
        chunks.append(encoding.decode(tokens[start:end]))
        if end >= len(tokens):
            break
        start += step
    return chunks


def embed_batch(client: OpenAI, texts: list[str]) -> list[list[float]]:
    resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in resp.data]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip().strip('"')
    if not api_key or api_key.upper().startswith("PLACEHOLDER"):
        sys.exit("OPENAI_API_KEY missing or placeholder. Fill it in .env.")

    client = OpenAI(api_key=api_key)
    encoding = tiktoken.get_encoding("cl100k_base")

    md_files = sorted(p for p in CORPUS_DIR.glob("*.md"))
    if not md_files:
        sys.exit(f"No .md files in {CORPUS_DIR.relative_to(PROJECT_ROOT)}. Run fetch_corpus.py first.")

    print(f"reading {len(md_files)} corpus file(s) from {CORPUS_DIR.relative_to(PROJECT_ROOT)}")

    all_chunks: list[str] = []
    metadata: list[dict] = []

    for path in md_files:
        page_id = path.stem
        source_url, source_title, body = parse_corpus_file(path)
        if not body:
            print(f"  ! {path.name}: empty body, skipping")
            continue
        chunks = chunk_text(body, encoding)
        for i, chunk in enumerate(chunks):
            metadata.append({
                "chunk_id": f"{page_id}::chunk-{i:03d}",
                "source_page_id": page_id,
                "source_url": source_url,
                "source_title": source_title,
                "text": chunk,
            })
            all_chunks.append(chunk)
        print(f"  {path.name}: {len(chunks)} chunk(s)")

    if not all_chunks:
        sys.exit("No chunks produced. Check that corpus files have non-empty bodies.")

    total_tokens = sum(len(encoding.encode(c)) for c in all_chunks)
    cost_est = total_tokens / 1_000_000 * COST_PER_MILLION_TOKENS
    print()
    print(f"chunked: {len(all_chunks)} chunk(s), {total_tokens:,} tokens (est. cost ${cost_est:.4f})")

    embeddings: list[list[float]] = []
    api_calls = 0
    for batch_start in range(0, len(all_chunks), BATCH_SIZE):
        batch = all_chunks[batch_start:batch_start + BATCH_SIZE]
        print(f"  embedding {batch_start + 1}-{batch_start + len(batch)} / {len(all_chunks)}...")
        embeddings.extend(embed_batch(client, batch))
        api_calls += 1

    arr = np.array(embeddings, dtype=np.float32)
    if arr.shape != (len(all_chunks), EMBEDDING_DIMS):
        sys.exit(f"unexpected embedding shape {arr.shape}, expected ({len(all_chunks)}, {EMBEDDING_DIMS})")

    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms

    np.savez_compressed(INDEX_NPZ, embeddings=arr)
    INDEX_JSON.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print(f"done: {len(md_files)} pages → {len(all_chunks)} chunks → ({arr.shape[0]}, {arr.shape[1]}) matrix")
    print(f"  api calls: {api_calls}")
    print(f"  est. cost: ${cost_est:.4f}")
    print(f"  saved: {INDEX_NPZ.relative_to(PROJECT_ROOT)} ({INDEX_NPZ.stat().st_size:,} bytes)")
    print(f"  saved: {INDEX_JSON.relative_to(PROJECT_ROOT)} ({INDEX_JSON.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
