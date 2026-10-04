from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import psycopg
from sentence_transformers import SentenceTransformer

from contracts.schemas import Chunk


# =========================================================
# Retrieval result
# =========================================================

@dataclass
class Hit:
    chunk_id: str
    score: float
    section_path: str
    page: int
    text: str


# =========================================================
# PostgreSQL configuration
# =========================================================

DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = int(os.getenv("POSTGRES_PORT", "5433"))
DB_NAME = os.getenv("POSTGRES_DB", "claimlens")
DB_USER = os.getenv("POSTGRES_USER", "claimlens")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")


# =========================================================
# Embedding model
# =========================================================

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

_model: Optional[SentenceTransformer] = None


def get_embedding_model() -> SentenceTransformer:
    """
    Load the embedding model once and reuse it.
    """

    global _model

    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)

    return _model


# =========================================================
# PostgreSQL connection
# =========================================================

def get_connection():
    """
    Create a PostgreSQL connection.
    """

    return psycopg.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


# =========================================================
# PostgreSQL Retriever
# =========================================================

class PostgresRetriever:

    def __init__(self, case_id: str):
        self.case_id = case_id

    # -----------------------------------------------------
    # Compatibility property
    # -----------------------------------------------------

    @property
    def chunks(self) -> list[Chunk]:
        """
        Return all chunks for this case.

        Existing verifier/tools code expects:
            retriever.chunks
        """

        return self.get_chunks()

    # -----------------------------------------------------
    # Get chunks
    # -----------------------------------------------------

    def get_chunks(self) -> list[Chunk]:
        """
        Load indexed chunks from PostgreSQL.
        """

        with get_connection() as conn:

            with conn.cursor() as cur:

                cur.execute(
                    """
                    SELECT
                        chunk_id,
                        doc_id,
                        section_path,
                        page,
                        text,
                        tags
                    FROM retrieval_chunks
                    WHERE case_id = %s
                    ORDER BY id
                    """,
                    (self.case_id,),
                )

                rows = cur.fetchall()

        chunks: list[Chunk] = []

        for row in rows:

            tags = row[5] or []

            if isinstance(tags, str):
                tags = json.loads(tags)

            chunks.append(
                Chunk(
                    chunk_id=row[0],
                    doc_id=row[1],
                    section_path=row[2],
                    page=row[3],
                    text=row[4],
                    bbox=[],
                    tags=tags,
                )
            )

        return chunks

    # -----------------------------------------------------
    # Hybrid search
    # -----------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> list[Hit]:
        """
        Hybrid retrieval:

        1. PostgreSQL Full-Text Search
        2. pgvector similarity search
        3. Reciprocal Rank Fusion
        """

        model = get_embedding_model()

        query_embedding = model.encode(
            query,
            normalize_embeddings=True,
        ).tolist()

        with get_connection() as conn:

            with conn.cursor() as cur:

                # =================================================
                # 1. PostgreSQL Full-Text Search
                # =================================================

                cur.execute(
                    """
                    SELECT
                        chunk_id,
                        section_path,
                        page,
                        text,
                        ts_rank_cd(
                            search_vector,
                            websearch_to_tsquery('english', %s)
                        ) AS fts_score
                    FROM retrieval_chunks
                    WHERE case_id = %s
                      AND search_vector @@
                          websearch_to_tsquery('english', %s)
                    ORDER BY fts_score DESC
                    LIMIT 20
                    """,
                    (
                        query,
                        self.case_id,
                        query,
                    ),
                )

                fts_rows = cur.fetchall()

                # =================================================
                # 2. pgvector similarity search
                # =================================================

                cur.execute(
                    """
                    SELECT
                        chunk_id,
                        section_path,
                        page,
                        text,
                        1 - (embedding <=> %s::vector)
                            AS vector_score
                    FROM retrieval_chunks
                    WHERE case_id = %s
                    ORDER BY embedding <=> %s::vector
                    LIMIT 20
                    """,
                    (
                        query_embedding,
                        self.case_id,
                        query_embedding,
                    ),
                )

                vector_rows = cur.fetchall()

        # =========================================================
        # 3. Create ranking maps
        # =========================================================

        fts_rank = {
            row[0]: rank
            for rank, row in enumerate(
                fts_rows,
                start=1,
            )
        }

        vector_rank = {
            row[0]: rank
            for rank, row in enumerate(
                vector_rows,
                start=1,
            )
        }

        # =========================================================
        # 4. Store chunk metadata
        # =========================================================

        chunk_data: dict[str, dict] = {}

        for row in fts_rows:

            chunk_data[row[0]] = {
                "section_path": row[1],
                "page": row[2],
                "text": row[3],
            }

        for row in vector_rows:

            if row[0] not in chunk_data:

                chunk_data[row[0]] = {
                    "section_path": row[1],
                    "page": row[2],
                    "text": row[3],
                }

        # =========================================================
        # 5. Reciprocal Rank Fusion
        # =========================================================

        RRF_K = 60

        scores: dict[str, float] = {}

        for chunk_id in chunk_data:

            score = 0.0

            # FTS contribution
            if chunk_id in fts_rank:

                score += 1.0 / (
                    RRF_K + fts_rank[chunk_id]
                )

            # Vector contribution
            if chunk_id in vector_rank:

                score += 1.0 / (
                    RRF_K + vector_rank[chunk_id]
                )

            scores[chunk_id] = score

        # =========================================================
        # 6. Sort by RRF score
        # =========================================================

        ranked = sorted(
            scores.items(),
            key=lambda item: item[1],
            reverse=True,
        )

        # =========================================================
        # 7. Convert to Hit objects
        # =========================================================

        results: list[Hit] = []

        for chunk_id, score in ranked[:top_k]:

            data = chunk_data[chunk_id]

            results.append(
                Hit(
                    chunk_id=chunk_id,
                    score=score,
                    section_path=data["section_path"],
                    page=data["page"],
                    text=data["text"],
                )
            )

        return results


# =========================================================
# Retriever registry
# =========================================================

_RETRIEVERS: dict[str, PostgresRetriever] = {}


# =========================================================
# Build retrieval index
# =========================================================

def build_index(
    case_id: str,
    chunks: list[Chunk],
) -> None:
    """
    Generate embeddings and store chunks in PostgreSQL.
    """

    model = get_embedding_model()

    with get_connection() as conn:

        with conn.cursor() as cur:

            for chunk in chunks:

                # ---------------------------------------------
                # Generate embedding
                # ---------------------------------------------

                embedding = model.encode(
                    chunk.text,
                    normalize_embeddings=True,
                ).tolist()

                # ---------------------------------------------
                # Store chunk
                # ---------------------------------------------

                cur.execute(
                    """
                    INSERT INTO retrieval_chunks (
                        case_id,
                        chunk_id,
                        doc_id,
                        section_path,
                        page,
                        text,
                        tags,
                        embedding
                    )
                    VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s::vector
                    )

                    ON CONFLICT (case_id, chunk_id)

                    DO UPDATE SET
                        doc_id = EXCLUDED.doc_id,
                        section_path = EXCLUDED.section_path,
                        page = EXCLUDED.page,
                        text = EXCLUDED.text,
                        tags = EXCLUDED.tags,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        case_id,
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.section_path,
                        chunk.page,
                        chunk.text,
                        json.dumps(chunk.tags),
                        embedding,
                    ),
                )

        conn.commit()

    # Cache retriever object
    _RETRIEVERS[case_id] = PostgresRetriever(
        case_id
    )


# =========================================================
# Get retriever
# =========================================================

def get_retriever(
    case_id: str,
) -> PostgresRetriever:

    if case_id not in _RETRIEVERS:

        _RETRIEVERS[case_id] = PostgresRetriever(
            case_id
        )

    return _RETRIEVERS[case_id]


# =========================================================
# Public search API
# =========================================================

def search(
    case_id: str,
    query: str,
    k: int = 5,
    filters: Optional[dict] = None,
) -> list[Hit]:

    retriever = get_retriever(case_id)

    return retriever.search(
        query=query,
        top_k=k,
        filters=filters,
    )


# =========================================================
# Backward compatibility
# =========================================================

# Existing tools.py imports this name.
# Keep it so older code does not break.
InMemoryRetriever = PostgresRetriever