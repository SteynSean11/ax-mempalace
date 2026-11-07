"""
PGVector-backed MemPalace storage backend for ApexSigma vault.
Implements BaseBackend + BaseCollection (RFC 001 contract).

Vault connection:
  Host: 127.0.0.1:5432
  Database: omega
  User/password: from ~/.config/containers/secrets/apexsigma-vault.env
  (APEXSIGMA_VAULT_POSTGRES_USER / APEXSIGMA_VAULT_POSTGRES_PASSWORD)

Schema (already exists — do NOT recreate):
  mempalace_drawers(id TEXT PK, document TEXT, metadata_ JSONB, embedding VECTOR(768), created_at TIMESTAMPTZ)
  mempalace_closets(id TEXT PK, document TEXT, metadata_ JSONB, embedding VECTOR(768), created_at TIMESTAMPTZ)

Embedder:
  D6 (RUNBOOK-INFRA-700-001) fixes the embedder to ``nomic-embed-text-v1.5``
  running in-process through ONNX Runtime (768 dimensions). nomic is
  asymmetric: documents are embedded with the ``search_document:`` prefix and
  queries with ``search_query:``. The legacy Ollama HTTP path survives only
  as an explicit opt-in (``MEMPALACE_EMBEDDING_PROVIDER=ollama``).

SQL construction:
  Every statement is composed with ``psycopg2.sql`` so identifiers (table and
  column names) are quoted by psycopg2 rather than interpolated, and every
  user-supplied value (metadata keys and values) is bound as a query
  parameter rather than interpolated into the statement text.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import psycopg2
import requests
from psycopg2 import sql

from .base import (
    BaseBackend,
    BaseCollection,
    DimensionMismatchError,
    GetResult,
    HealthStatus,
    PalaceNotFoundError,
    PalaceRef,
    QueryResult,
    UnsupportedFilterError,
)

# ── Embedding function ────────────────────────────────────────────────────────


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    """Embed texts via Ollama nomic-embed-text:v1.5 (768d).

    Legacy path. It is not the default: D6 runs the model in-process. Kept
    for operators who explicitly select it through
    ``MEMPALACE_EMBEDDING_PROVIDER=ollama``.
    """
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:v1.5")
    vectors = []
    for text in texts:
        r = requests.post(
            f"{base}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=60,
        )
        r.raise_for_status()
        vectors.append(r.json()["embedding"])
    return vectors


def _embedding_provider() -> str:
    """Return the configured embedding provider (``nomic`` or ``ollama``)."""
    from ..config import MempalaceConfig

    provider = MempalaceConfig().embedding_provider
    if provider not in {"nomic", "ollama"}:
        raise ValueError(f"unknown embedding provider {provider!r}; expected 'nomic' or 'ollama'")
    return provider


def _embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed documents for storage (nomic's ``search_document:`` prefix)."""
    if _embedding_provider() == "ollama":
        return _ollama_embed(texts)
    from ..nomic_embedder import get_nomic_embedder

    return get_nomic_embedder().embed_documents(texts)


def _embed_queries(texts: list[str]) -> list[list[float]]:
    """Embed queries for retrieval (nomic's ``search_query:`` prefix)."""
    if _embedding_provider() == "ollama":
        return _ollama_embed(texts)
    from ..nomic_embedder import get_nomic_embedder

    return get_nomic_embedder().embed_queries(texts)


# ── Connection factory ─────────────────────────────────────────────────────────


def _get_conn() -> psycopg2.extensions.connection:
    """Create a psycopg2 connection to the ApexSigma vault postgres."""
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
        database=os.environ.get("PGDATABASE", "omega"),
        user=os.environ.get("PGUSER", os.environ.get("APEXSIGMA_VAULT_POSTGRES_USER", "omega")),
        password=os.environ.get(
            "PGPASSWORD", os.environ.get("APEXSIGMA_VAULT_POSTGRES_PASSWORD", "")
        ),
    )
    conn.autocommit = False
    return conn


# ── Batch helper ──────────────────────────────────────────────────────────────


def _batches(iterable, size):
    """Yield successive batches from iterable without loading all into memory."""
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


# ── PgVectorCollection ───────────────────────────────────────────────────────


class PgVectorCollection(BaseCollection):
    """One collection = one Postgres table with a vector column.

    Args:
        conn_factory: callable that returns an open psycopg2 connection
        table_name:  "mempalace_drawers" or "mempalace_closets"
        embedding_dim: dimensionality of embedding vectors (default 768)
    """

    def __init__(
        self,
        conn_factory,
        table_name: str,
        embedding_dim: int = 768,
    ):
        self._conn_factory = conn_factory
        self._table = table_name
        self._dim = embedding_dim

    def _conn(self) -> psycopg2.extensions.connection:
        """Return an open connection, reconnecting if closed."""
        conn = self._conn_factory()
        if conn.closed:
            conn = _get_conn()
        return conn

    # ── write ops ────────────────────────────────────────────────────────────

    def add(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        if embeddings is None:
            if not documents:
                return
            embeddings = _embed_documents(documents)
        self._upsert_internal(documents, ids, metadatas, embeddings, upsert=False)

    def upsert(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        if embeddings is None:
            if not documents:
                return
            embeddings = _embed_documents(documents)
        self._upsert_internal(documents, ids, metadatas, embeddings, upsert=True)

    def _upsert_internal(
        self,
        documents: list[str],
        ids: list[str],
        metadatas,
        embeddings: list[list[float]],
        upsert: bool = True,
    ) -> None:
        meta = metadatas or [{}] * len(documents)
        if len(documents) != len(ids) or len(meta) != len(ids) or len(embeddings) != len(ids):
            raise ValueError("ids, documents, metadatas and embeddings must have equal lengths")
        for vector in embeddings:
            if len(vector) != self._dim:
                raise DimensionMismatchError(
                    f"expected {self._dim}-dimension vectors, got {len(vector)}"
                )
        rows = list(zip(ids, documents, meta, embeddings))

        for batch in _batches(rows, 500):
            flat = []
            for rid, doc, m, emb in batch:
                flat.extend([rid, doc, json.dumps(m), emb])

            placeholders = sql.SQL(", ").join(sql.SQL("(%s,%s,%s,%s::vector)") for _ in batch)
            op = (
                "ON CONFLICT (id) DO UPDATE SET "
                "document=EXCLUDED.document,"
                "metadata_=EXCLUDED.metadata_,"
                "embedding=EXCLUDED.embedding"
                if upsert
                else ""
            )
            statement = sql.SQL(
                "INSERT INTO {} (id,document,metadata_,embedding) VALUES {} {}"
            ).format(sql.Identifier(self._table), placeholders, sql.SQL(op))

            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(statement, flat)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def upsert_migrated(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
        created_at: list[str | None],
    ) -> None:
        """Insert copied drawers with source timestamps; never replace a collision.

        The G3b copier checks destination hashes before calling this method.
        ``ON CONFLICT DO NOTHING`` is a second guard against overwriting an
        unrelated row if another writer appears between the read and insert.
        """
        if self._table not in {"mempalace_drawers", "mempalace_closets"}:
            raise ValueError("unsupported migration table")
        if not all(
            len(values) == len(ids) for values in (documents, metadatas, embeddings, created_at)
        ):
            raise ValueError("migration batch lengths differ")
        if any(len(vector) != self._dim for vector in embeddings):
            raise ValueError("migration embedding dimension differs from target")
        if not ids:
            return

        statement = sql.SQL(
            "INSERT INTO {} "
            "(id, document, metadata_, embedding, created_at) "
            "VALUES (%s, %s, %s::jsonb, %s::vector, COALESCE(%s::timestamptz, now())) "
            "ON CONFLICT (id) DO NOTHING"
        ).format(sql.Identifier(self._table))

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                for drawer_id, document, metadata, vector, timestamp in zip(
                    ids, documents, metadatas, embeddings, created_at
                ):
                    cur.execute(
                        statement,
                        (
                            drawer_id,
                            document,
                            json.dumps(metadata),
                            f"[{','.join(str(value) for value in vector)}]",
                            timestamp,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise ValueError(f"target ID appeared during copy: {drawer_id}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    # ── read ops ─────────────────────────────────────────────────────────────

    def query(
        self,
        *,
        query_texts: Optional[list[str]] = None,
        query_embeddings: Optional[list[list[float]]] = None,
        n_results: int = 10,
        where: Optional[dict] = None,
        where_document: Optional[dict] = None,  # noqa: ARG002 (ChromaDB compat)
        include: Optional[list[str]] = None,  # noqa: ARG002 (always returns docs+meta+dist)
    ) -> QueryResult:
        del where_document, include  # not yet implemented

        if query_embeddings is None:
            if not query_texts:
                return QueryResult.empty(num_queries=1)
            query_embeddings = _embed_queries(query_texts)

        n = len(query_embeddings)
        ids_out: list[list[str]] = [[] for _ in range(n)]
        docs: list[list[str]] = [[] for _ in range(n)]
        metas: list[list[dict]] = [[] for _ in range(n)]
        distances: list[list[float]] = [[] for _ in range(n)]

        conn = self._conn()
        with conn.cursor() as cur:
            where_clause, where_params = self._where(where, cur)
            statement = sql.SQL(
                "SELECT id, document, metadata_, "
                "embedding <=> %(vector)s::vector AS dist "
                "FROM {} {} "
                "ORDER BY embedding <=> %(vector)s::vector "
                "LIMIT %(limit)s"
            ).format(sql.Identifier(self._table), sql.SQL(where_clause))

            for qi, emb in enumerate(query_embeddings):
                emb_str = f"[{','.join(str(x) for x in emb)}]"
                params = {**where_params, "vector": emb_str, "limit": n_results}
                cur.execute(statement, params)
                for row in cur.fetchall():
                    ids_out[qi].append(row[0])
                    docs[qi].append(row[1])
                    metas[qi].append(row[2] if row[2] else {})
                    distances[qi].append(row[3])

        return QueryResult(
            ids=ids_out,
            documents=docs,
            metadatas=metas,
            distances=distances,
            embeddings=None,
        )

    def get(
        self,
        *,
        ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
        where_document: Optional[dict] = None,  # noqa: ARG002
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        include: Optional[list[str]] = None,  # noqa: ARG002
    ) -> GetResult:
        del where_document, include  # not yet implemented

        conn = self._conn()
        with conn.cursor() as cur:
            if ids:
                statement = sql.SQL(
                    "SELECT id,document,metadata_ FROM {} WHERE id = ANY(%(ids)s)"
                ).format(sql.Identifier(self._table))
                params: dict = {"ids": ids}
            else:
                where_clause, params = self._where(where, cur) if where else ("", {})
                parts = [
                    sql.SQL("SELECT id,document,metadata_ FROM {}").format(
                        sql.Identifier(self._table)
                    )
                ]
                if where_clause:
                    parts.append(sql.SQL(where_clause))
                if limit is not None:
                    parts.append(sql.SQL("LIMIT %(limit)s"))
                    params["limit"] = limit
                if offset is not None:
                    parts.append(sql.SQL("OFFSET %(offset)s"))
                    params["offset"] = offset
                statement = sql.SQL(" ").join(parts)

            cur.execute(statement, params)
            rows = cur.fetchall()

        return GetResult(
            ids=[r[0] for r in rows],
            documents=[r[1] for r in rows],
            metadatas=[r[2] if r[2] else {} for r in rows],
            embeddings=None,
        )

    def delete(
        self,
        *,
        ids: Optional[list[str]] = None,
        where: Optional[dict] = None,
    ) -> None:
        conn = self._conn()
        with conn.cursor() as cur:
            if ids:
                statement = sql.SQL("DELETE FROM {} WHERE id = ANY(%(ids)s)").format(
                    sql.Identifier(self._table)
                )
                cur.execute(statement, {"ids": ids})
            elif where:
                where_clause, params = self._where(where, cur)
                statement = sql.SQL("DELETE FROM {} {}").format(
                    sql.Identifier(self._table), sql.SQL(where_clause)
                )
                cur.execute(statement, params)
            else:
                return
        conn.commit()

    def count(self) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(self._table)))
            return cur.fetchone()[0]

    # ── helpers ──────────────────────────────────────────────────────────────

    def _where(self, where: Optional[dict], cursor) -> tuple[str, dict]:
        """Build the WHERE clause and its bound parameters for ``cursor``.

        The metadata column is quoted by psycopg2 (``sql.Identifier``), so the
        caller needs a cursor only for the connection encoding.
        """
        clause, params = _build_where(where)
        if clause:
            clause = clause.format(metadata_column=sql.Identifier("metadata_").as_string(cursor))
        return clause, params


# ── Where-clause compiler ─────────────────────────────────────────────────────

#: Operators this backend implements. Anything else raises
#: ``UnsupportedFilterError`` — RFC 001 §1.4 forbids silently dropping an
#: operator the caller asked for.
_ALLOWED_OPERATORS = frozenset({"$eq", "$ne", "$in", "$nin", "$and", "$or", "$contains"})

#: Placeholder for the quoted metadata column. The clause is built without a
#: connection so it stays unit-testable; the caller substitutes the
#: psycopg2-quoted identifier.
_METADATA_COLUMN_TOKEN = "{metadata_column}"


def _jsonb_text(value) -> str:
    """Render a metadata value the way ``->>`` renders it in JSONB.

    ``metadata_->>'key'`` returns text, so every comparison has to be against
    text. Booleans are lower-cased the way JSONB stores them; numbers use
    their JSON spelling.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return str(value)


class _WhereCompiler:
    """Compile a Chroma-style where clause into a parameterised SQL template.

    The template carries named placeholders (``%(w0)s``) and one
    ``{metadata_column}`` token. Every metadata key and value is bound as a
    parameter, so no caller-supplied text ever reaches the statement text.
    """

    def __init__(self, column: str = _METADATA_COLUMN_TOKEN) -> None:
        self._column = column
        self._params: dict[str, object] = {}
        self._counter = 0

    def _bind(self, value) -> str:
        name = f"w{self._counter}"
        self._counter += 1
        self._params[name] = value
        return f"%({name})s"

    def build(self, where: Optional[dict]) -> tuple[str, dict]:
        """Return ``(clause, params)``; the clause is empty when there is no filter."""
        if not where:
            return "", {}
        clause = self._node(where)
        if not clause:
            return "", {}
        return "WHERE " + clause, dict(self._params)

    def _node(self, node) -> str:
        if not isinstance(node, dict):
            raise UnsupportedFilterError("where-clause entries must be mappings")
        parts: list[str] = []
        for key, value in node.items():
            if key in {"$and", "$or"}:
                parts.append(self._group(key, value))
            elif key.startswith("$"):
                raise UnsupportedFilterError(
                    f"operator {key!r} is not supported by the pgvector backend"
                )
            else:
                parts.append(self._condition(key, value))
        parts = [part for part in parts if part]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return "(" + " AND ".join(parts) + ")"

    def _group(self, operator: str, value) -> str:
        if not isinstance(value, (list, tuple)) or not value:
            raise UnsupportedFilterError(f"{operator} requires a non-empty list of where clauses")
        joiner = " AND " if operator == "$and" else " OR "
        clauses = [self._node(sub) for sub in value]
        clauses = [clause for clause in clauses if clause]
        if not clauses:
            return ""
        if len(clauses) == 1:
            return clauses[0]
        return "(" + joiner.join(clauses) + ")"

    def _condition(self, key: str, value) -> str:
        if isinstance(value, dict):
            clauses = [self._operator(key, op, operand) for op, operand in value.items()]
            clauses = [clause for clause in clauses if clause]
            if not clauses:
                return ""
            if len(clauses) == 1:
                return clauses[0]
            return "(" + " AND ".join(clauses) + ")"
        return self._operator(key, "$eq", value)

    def _operator(self, key: str, operator: str, operand) -> str:
        if operator not in _ALLOWED_OPERATORS:
            raise UnsupportedFilterError(
                f"operator {operator!r} is not supported by the pgvector backend"
            )
        if operator in {"$and", "$or"}:
            raise UnsupportedFilterError(
                f"{operator!r} may only appear at the top level of a where clause"
            )
        key_ref = self._bind(key)
        column = self._column
        if operator == "$eq":
            return f"{column}->>{key_ref} = {self._bind(_jsonb_text(operand))}"
        if operator == "$ne":
            return f"{column}->>{key_ref} != {self._bind(_jsonb_text(operand))}"
        if operator in {"$in", "$nin"}:
            values = operand if isinstance(operand, (list, tuple)) else [operand]
            if not values:
                raise UnsupportedFilterError(f"{operator} requires a non-empty list of values")
            refs = ", ".join(self._bind(_jsonb_text(item)) for item in values)
            keyword = "IN" if operator == "$in" else "NOT IN"
            return f"{column}->>{key_ref} {keyword} ({refs})"
        if operator == "$contains":
            escaped = str(operand).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            return f"{column}->>{key_ref} LIKE {self._bind('%' + escaped + '%')}"
        raise UnsupportedFilterError(  # pragma: no cover — allow-list is exhaustive
            f"operator {operator!r} is not supported by the pgvector backend"
        )


def _build_where(where: Optional[dict]) -> tuple[str, dict]:
    """Compile a Chroma-style where clause into ``(template, params)``.

    Supported forms:

    * ``{"wing": "emotions"}`` — simple equality on a metadata key
    * ``{"wing": {"$eq": "emotions"}}`` — explicit equality
    * ``{"wing": {"$ne": "emotions"}}`` — inequality (the key must exist)
    * ``{"wing": {"$in": ["a", "b"]}}`` — membership
    * ``{"$and": [{...}, {...}]}`` / ``{"$or": [{...}, {...}]}`` — groups

    The returned template contains a ``{metadata_column}`` token and named
    ``%(wN)s`` placeholders; the caller substitutes the quoted identifier and
    hands the parameters to psycopg2.

    Raises:
        UnsupportedFilterError: an operator outside the allow-list was used.
    """
    return _WhereCompiler().build(where)


# ── PgVectorBackend ──────────────────────────────────────────────────────────


class PgVectorBackend(BaseBackend):
    """Backend that routes to the ApexSigma vault postgres.

    Does NOT create tables — ``mempalace_drawers`` and ``mempalace_closets``
    must already exist in the ``omega`` database.
    """

    name = "pgvector"
    capabilities = frozenset({"add", "upsert", "query", "get", "delete", "count"})

    def __init__(self):
        self._instances: dict[str, PgVectorCollection] = {}
        self._conn: Optional[psycopg2.extensions.connection] = None

    def get_collection(
        self,
        *,
        palace: PalaceRef,
        collection_name: str,
        create: bool = False,  # noqa: ARG002 (tables already exist in vault)
        options: Optional[dict] = None,
    ) -> PgVectorCollection:
        # The vault postgres holds two fixed collections:
        # - mempalace_drawers  (verbatim content)
        # - mempalace_closets  (AAAK pointer index)
        # palace.local_path is used only as a cache key disambiguator.
        if palace.local_path is None:
            raise PalaceNotFoundError("PgVectorBackend requires palace.local_path on PalaceRef")
        cache_key = f"{palace.local_path}:{collection_name}"
        if cache_key not in self._instances:
            self._instances[cache_key] = PgVectorCollection(
                self._get_conn,
                collection_name,
                embedding_dim=options.get("embedding_dim", 768) if options else 768,
            )
        return self._instances[cache_key]

    def _get_conn(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = _get_conn()
        return self._conn

    def close(self) -> None:
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None

    def health(self, palace: Optional[PalaceRef] = None) -> HealthStatus:
        del palace  # unused — vault postgres is single-tenant
        try:
            conn = self._get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return HealthStatus.healthy()
        except Exception as e:
            return HealthStatus.unhealthy(str(e))
