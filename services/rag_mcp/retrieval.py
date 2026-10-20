"""Vector retrieval. Deterministic hashing is exclusively a development fixture embedding."""

import hashlib
import math
import os
import re
from functools import lru_cache

import httpx
from sqlalchemy import create_engine, text


@lru_cache(maxsize=1)
def engine():
    return create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)


async def embed(content):
    endpoint = os.getenv("EMBEDDING_URL")
    if endpoint:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(endpoint, json={"inputs": content[:20000]})
            response.raise_for_status()
            vector = response.json()[0]
            if not vector or not all(
                isinstance(v, (float, int)) and math.isfinite(v) for v in vector
            ):
                raise ValueError("Invalid embedding result")
            return vector, os.environ["EMBEDDING_MODEL_ID"]
    if os.getenv("DAYZERO_DEVELOPMENT", "false").lower() != "true":
        raise RuntimeError("Production embeddings are not configured")
    vector = [0.0] * 256
    normalized = re.sub(r"\s+", " ", content.lower())
    tokens = normalized.split() + [
        normalized[i : i + 3] for i in range(max(0, len(normalized) - 2))
    ]
    for token in tokens:
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:2], "big") % 256] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1
    return [v / norm for v in vector], "fixture-hash-256-v1"


def store_prepared(item, tenant_id):
    with engine().begin() as db:
        db.execute(
            text(
                "INSERT INTO rag_documents (id, tenant_id, title, content, content_hash, status) VALUES (:id,:tenant,:title,:content,:hash,'prepared') ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": item["id"],
                "tenant": tenant_id,
                "title": item["title"],
                "content": item["content"],
                "hash": item["content_hash"],
            },
        )


async def index(item, tenant_id):
    content = item["content"]
    chunks = [content[i : i + 1200] for i in range(0, len(content), 1000)]
    vectors = [await embed(chunk) for chunk in chunks]
    with engine().begin() as db:
        db.execute(text("DELETE FROM rag_chunks WHERE document_id=:id"), {"id": item["id"]})
        for i, (chunk, pair) in enumerate(zip(chunks, vectors)):
            vector, model = pair
            db.execute(
                text(
                    "INSERT INTO rag_chunks (id, document_id, tenant_id, content, model_id, embedding) VALUES (:id,:doc,:tenant,:content,:model,CAST(:embedding AS vector))"
                ),
                {
                    "id": f"{item['id']}:{i}",
                    "doc": item["id"],
                    "tenant": tenant_id,
                    "content": chunk,
                    "model": model,
                    "embedding": str(vector),
                },
            )
        db.execute(
            text(
                "UPDATE rag_documents SET status='indexed' WHERE id=:id AND tenant_id=:tenant AND content_hash=:hash"
            ),
            {"id": item["id"], "tenant": tenant_id, "hash": item["content_hash"]},
        )
    return len(chunks)


async def search(query, allowed, tenant_id, top_k):
    if not allowed:
        return []
    vector, model = await embed(query)
    scenario_results = []
    for document in allowed:
        if document.get("source") != "scenario":
            continue
        content = document["content"]
        for offset in range(0, len(content), 1000):
            chunk = content[offset : offset + 1200]
            doc_vector, doc_model = await embed(document["title"] + "\n" + chunk)
            if doc_model != model or len(vector) != len(doc_vector):
                continue
            denom = math.sqrt(sum(v * v for v in vector) * sum(v * v for v in doc_vector)) or 1
            score = sum(a * b for a, b in zip(vector, doc_vector)) / denom
            scenario_results.append(
                {
                    "document_id": document["id"],
                    "chunk_id": f"{document['id']}:{offset // 1000}",
                    "title": document["title"],
                    "content": chunk,
                    "score": score,
                }
            )
    ids = [d["id"] for d in allowed if d.get("source") != "scenario"]
    if not ids:
        return sorted(scenario_results, key=lambda r: r["score"], reverse=True)[:top_k]
    # UUID-like IDs are still bound parameters. Filtering happens before ranking.
    params = {
        "tenant": tenant_id,
        "model": model,
        "embedding": str(vector),
        "limit": top_k,
    }
    placeholders = []
    for i, item in enumerate(ids):
        params[f"id{i}"] = item
        placeholders.append(f":id{i}")
    statement = text(
        "SELECT c.id AS chunk_id, c.document_id, d.title, c.content, 1 - (c.embedding <=> CAST(:embedding AS vector)) AS score FROM rag_chunks c JOIN rag_documents d ON d.id=c.document_id WHERE c.tenant_id=:tenant AND d.status='indexed' AND c.model_id=:model AND c.document_id IN ("
        + ",".join(placeholders)
        + ") ORDER BY c.embedding <=> CAST(:embedding AS vector) LIMIT :limit"
    )
    with engine().connect() as db:
        results = [dict(r) for r in db.execute(statement, params).mappings()]
    return sorted(results + scenario_results, key=lambda r: r["score"], reverse=True)[:top_k]
