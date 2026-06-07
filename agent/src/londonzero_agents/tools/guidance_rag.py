"""
Guidance RAG tool — retrieves relevant planning/compliance guidance for the
feasibility agent.

Retrieval is NVIDIA-powered but lightweight: chunks of the mock TfL / London
Cycling Design Standards / City of London guidance docs in ``data/rag`` are
embedded with an NVIDIA API Catalog embedding model (NIM), the query is embedded
per request, and we rank by in-memory cosine similarity. NO vector database or
self-hosted retriever microservice is involved — the corpus is tiny and curated.

If the embedding endpoint is unavailable, retrieval transparently falls back to a
dependency-free TF-IDF keyword score so the demo never hard-fails.

Returns the top-k most relevant chunks with their source so the feasibility
brief can be grounded in (and cite) actual guidance.
"""

import glob
import logging
import math
import os
import re
from collections import Counter
from collections.abc import AsyncGenerator
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Repo root = .../agent/src/londonzero_agents/tools/guidance_rag.py → parents[4]
_REPO_ROOT = Path(__file__).resolve().parents[4]

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "is", "are", "be", "as", "by", "that", "this", "should", "must", "where",
    "from", "not", "but", "if", "it", "their", "they", "which", "than", "such",
}


def _resolve_data_dir(data_dir: str) -> Path:
    p = Path(data_dir)
    if p.is_absolute() and p.exists():
        return p
    for base in (Path.cwd(), _REPO_ROOT):
        cand = base / data_dir
        if cand.exists():
            return cand
    return _REPO_ROOT / data_dir


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class _Chunk:
    __slots__ = ("source", "heading", "text", "tf", "embedding")

    def __init__(self, source: str, heading: str, text: str):
        self.source = source
        self.heading = heading
        self.text = text
        self.tf = Counter(_tokenize(f"{heading} {text}"))
        self.embedding: list[float] | None = None


def _load_chunks(data_dir: Path) -> list[_Chunk]:
    """Split each guidance doc into heading-scoped paragraph chunks."""
    chunks: list[_Chunk] = []
    for path in sorted(glob.glob(str(data_dir / "*.md")) + glob.glob(str(data_dir / "*.txt"))):
        source = Path(path).stem
        heading = source.replace("_", " ").title()
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning("guidance_rag: could not read %s: %s", path, exc)
            continue
        for block in re.split(r"\n\s*\n", raw):
            block = block.strip()
            if not block:
                continue
            if block.startswith("#"):
                heading = block.lstrip("#").strip()
                # A heading line on its own is context, not a retrievable chunk.
                if "\n" not in block:
                    continue
            chunks.append(_Chunk(source=source, heading=heading, text=block.lstrip("#").strip()))
    return chunks


def _idf(chunks: list[_Chunk]) -> dict[str, float]:
    n = len(chunks) or 1
    df: Counter = Counter()
    for c in chunks:
        df.update(set(c.tf))
    return {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class GuidanceRagConfig(FunctionBaseConfig, name="guidance_rag"):
    rag_data_dir: str = Field(
        default="data/rag",
        description="Directory of mock TfL / London planning guidance documents (.md/.txt)",
    )
    top_k: int = Field(default=4, description="Number of guidance chunks to return")
    embedding_model: str = Field(
        default="nvidia/llama-nemotron-embed-1b-v2",
        description="NVIDIA API Catalog embedding model (NIM) used for retrieval",
    )
    base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    api_key: str = Field(default="", description="NVIDIA API key; falls back to $NVIDIA_API_KEY")


class GuidanceSnippet(BaseModel):
    source: str = Field(description="Document the snippet came from")
    heading: str = Field(description="Section heading for context")
    text: str = Field(description="Retrieved guidance text")
    score: float = Field(description="Relevance score (cosine similarity, or TF-IDF if fallback)")


class GuidanceRagInput(BaseModel):
    query: str = Field(description="Free-text query describing the junction risks/intervention")
    top_k: int | None = Field(default=None, description="Override the configured number of results")


class GuidanceRagOutput(BaseModel):
    snippets: list[GuidanceSnippet] = Field(default_factory=list)
    method: str = Field(default="embedding", description="'embedding' (NVIDIA NIM) or 'tfidf' (fallback)")


@register_function(config_type=GuidanceRagConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def guidance_rag(config: GuidanceRagConfig, builder: Builder) -> AsyncGenerator[FunctionInfo]:
    # Load + index once; the corpus is tiny and static. Chunk embeddings are
    # computed lazily on first use so server startup never blocks on the network.
    data_dir = _resolve_data_dir(config.rag_data_dir)
    chunks = _load_chunks(data_dir)
    idf = _idf(chunks)
    if not chunks:
        logger.warning("guidance_rag: no guidance documents found in %s", data_dir)

    api_key = config.api_key or os.environ.get("NVIDIA_API_KEY", "")
    _embedder: dict = {"client": None, "ready": False, "failed": False}

    def _get_embedder():
        if _embedder["client"] is None and not _embedder["failed"]:
            try:
                from langchain_nvidia_ai_endpoints import NVIDIAEmbeddings

                _embedder["client"] = NVIDIAEmbeddings(
                    model=config.embedding_model,
                    api_key=api_key,
                    base_url=config.base_url,
                    truncate="END",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("guidance_rag: embedder init failed, will use TF-IDF: %s", exc)
                _embedder["failed"] = True
        return _embedder["client"]

    async def _ensure_chunk_embeddings(client) -> bool:
        """Embed all chunks once; returns False if embedding is unavailable."""
        if _embedder["ready"]:
            return True
        try:
            vectors = await client.aembed_documents([f"{c.heading}. {c.text}" for c in chunks])
            for c, v in zip(chunks, vectors):
                c.embedding = v
            _embedder["ready"] = True
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("guidance_rag: chunk embedding failed, falling back to TF-IDF: %s", exc)
            _embedder["failed"] = True
            return False

    def _tfidf_rank(q_terms: list[str], top_k: int) -> list[tuple[float, _Chunk]]:
        scored: list[tuple[float, _Chunk]] = []
        for c in chunks:
            length = sum(c.tf.values()) or 1
            score = sum((c.tf[t] / length) * idf.get(t, 1.0) for t in q_terms)
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:top_k]

    async def _run(input: GuidanceRagInput) -> GuidanceRagOutput:
        top_k = input.top_k or config.top_k
        if not chunks or not input.query.strip():
            return GuidanceRagOutput(snippets=[], method="tfidf")

        method = "embedding"
        ranked: list[tuple[float, _Chunk]] = []

        client = _get_embedder() if not _embedder["failed"] else None
        if client is not None and await _ensure_chunk_embeddings(client):
            try:
                q_vec = await client.aembed_query(input.query)
                ranked = sorted(
                    ((_cosine(q_vec, c.embedding), c) for c in chunks if c.embedding),
                    key=lambda x: x[0],
                    reverse=True,
                )[:top_k]
            except Exception as exc:  # noqa: BLE001
                logger.warning("guidance_rag: query embedding failed, TF-IDF fallback: %s", exc)
                ranked = []

        if not ranked:
            method = "tfidf"
            ranked = _tfidf_rank(_tokenize(input.query), top_k)

        snippets = [
            GuidanceSnippet(source=c.source, heading=c.heading, text=c.text, score=round(float(s), 4))
            for s, c in ranked
        ]
        logger.info("guidance_rag: %d/%d chunks via %s", len(snippets), len(chunks), method)
        return GuidanceRagOutput(snippets=snippets, method=method)

    yield FunctionInfo.create(
        single_fn=_run,
        description=(
            "Retrieve relevant TfL / London Cycling Design Standards / City of London "
            "planning guidance snippets for a junction redesign query, ranked by NVIDIA "
            "embedding similarity."
        ),
        input_schema=GuidanceRagInput,
        single_output_schema=GuidanceRagOutput,
    )
