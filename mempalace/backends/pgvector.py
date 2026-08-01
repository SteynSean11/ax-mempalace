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
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import psycopg2
import requests

from ..config import MempalaceConfig
from .base import (
    BackendError,
    BaseBackend,
    BaseCollection,
    GetResult,
    HealthStatus,
    PalaceNotFoundError,
    PalaceRef,
    QueryResult,
)

# ── Embedding function ────────────────────────────────────────────────────────


def _ollama_embed(texts: list[str]) -> list[list[float]]:
    """Embed texts via Ollama nomic-embed-text:v1.5 (768d)."""
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


# ── Connection factory ─────────────────────────────────────────────────────────


def _load_vault_env_defaults() -> None:
    """Load ApexSigma vault secrets into process env when unset."""
    env_path = Path.home() / ".config" / "containers" / "secrets" / "apexsigma-vault.env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _get_conn() -> psycopg2.extensions.connection:
    """Create a psycopg2 connection to the ApexSigma vault postgres."""
    _load_vault_env_defaults()
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "127.0.0.1"),
        port=os.environ.get("PGPORT", "5432"),
        database=os.environ.get(
            "PGDATABASE", os.environ.get("APEXSIGMA_VAULT_POSTGRES_DB", "omega")
        ),
        user=os.environ.get(
            "PGUSER", os.environ.get("APEXSIGMA_VAULT_POSTGRES_USER", "omega")
        ),
        password=os.environ.get(
            "PGPASSWORD", os.environ.get("APEXSIGMA_VAULT_POSTGRES_PASSWORD", "")
        ),
    )
    conn.autocommit = False
    return conn


def _normalize_metadata(metadata: Optional[dict], table_name: str) -> dict:
    """Fill backend-scoping metadata required by palace-aware API filters."""
    normalized = dict(metadata or {})
    normalized.setdefault("collection_name", table_name)

    palace_id = MempalaceConfig().palace_id
    if palace_id:
        normalized.setdefault("palace_id", palace_id)

    return normalized


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
            embeddings = _ollama_embed(documents)
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
            embeddings = _ollama_embed(documents)
        self._upsert_internal(documents, ids, metadatas, embeddings, upsert=True)

    def _upsert_internal(
        self,
        documents: list[str],
        ids: list[str],
        metadatas,
        embeddings: list[list[float]],
        upsert: bool = True,
    ) -> None:
        meta = [_normalize_metadata(m, self._table) for m in (metadatas or [{}] * len(documents))]
        rows = list(zip(ids, documents, meta, embeddings))

        for batch in _batches(rows, 500):
            flat = []
            for rid, doc, m, emb in batch:
                flat.extend([rid, doc, json.dumps(m), emb])

            placeholders = ",".join(["(%s,%s,%s,%s::vector)"] * len(batch))
            cols = "id,document,metadata_,embedding"
            op = (
                "ON CONFLICT (id) DO UPDATE SET "
                "document=EXCLUDED.document,"
                "metadata_=EXCLUDED.metadata_,"
                "embedding=EXCLUDED.embedding"
                if upsert
                else ""
            )
            sql = f"INSERT INTO {self._table} ({cols}) VALUES {placeholders} {op}"

            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql, flat)
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
            query_embeddings = _ollama_embed(query_texts)

        n = len(query_embeddings)
        ids_out: list[list[str]] = [[] for _ in range(n)]
        docs: list[list[str]] = [[] for _ in range(n)]
        metas: list[list[dict]] = [[] for _ in range(n)]
        distances: list[list[float]] = [[] for _ in range(n)]

        where_clause = self._build_where(where)

        for qi, emb in enumerate(query_embeddings):
            emb_str = f"[{','.join(str(x) for x in emb)}]"
            sql = f"""
                SELECT id, document, metadata_,
                       embedding <=> %s::vector AS dist
                FROM {self._table}
                {where_clause}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(sql, (emb_str, emb_str, n_results))
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

        if ids:
            sql = f"SELECT id,document,metadata_ FROM {self._table} WHERE id = ANY(%s)"
            params: list = [ids]
        elif where:
            wc = self._build_where(where)
            sql = f"SELECT id,document,metadata_ FROM {self._table} {wc}"
            params = []
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
            if offset is not None:
                sql += " OFFSET %s"
                params.append(offset)
        else:
            sql = f"SELECT id,document,metadata_ FROM {self._table}"
            params = []
            if limit is not None:
                sql += " LIMIT %s"
                params.append(limit)
            if offset is not None:
                sql += " OFFSET %s"
                params.append(offset)

        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
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
        if ids:
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(
                    f"DELETE FROM {self._table} WHERE id = ANY(%s)", [ids]
                )
            conn.commit()
        elif where:
            wc = self._build_where(where)
            conn = self._conn()
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {self._table} {wc}")
            conn.commit()

    def count(self) -> int:
        conn = self._conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {self._table}")
            return cur.fetchone()[0]

    # ── helpers ──────────────────────────────────────────────────────────────

    def _build_where(self, where: Optional[dict]) -> str:
        """Convert ChromaDB-style where clause to Postgres JSONB WHERE clause.

        Supports only the operators that ChromaDB backends support:
        $eq, $ne, $in, $nin, $and, $or, $contains (and non-$ prefixed simple equality).
        """
        if not where:
            return ""

        parts: list[str] = []
        stack: list[tuple[str, object]] = [("AND", where)]

        while stack:
            conj, node = stack.pop()
            if not isinstance(node, dict):
                continue

            sub_parts: list[str] = []
            for k, v in node.items():
                if k == "$and":
                    stack.append(("AND", v))
                elif k == "$or":
                    stack.append(("OR", v))
                elif k.startswith("$"):
                    # operator
                    if k == "$eq":
                        sub_parts.append(f"metadata_->>'{v}' = '{v}'" if isinstance(v, str) else f"metadata_->>'{list(node.keys())[list(node.values()).index(v)]}' = {v}")
                    elif k == "$ne":
                        sub_parts.append(f"metadata_->>'{v}' != '{v}'" if isinstance(v, str) else f"metadata_->>'{list(node.keys())[list(node.values()).index(v)]}' != {v}")
                    elif k == "$in":
                        col = list(node.keys())[0]
                        vals = ",".join(f"'{x}'" for x in v)
                        sub_parts.append(f"metadata_->>'{col}' IN ({vals})")
                    elif k == "$nin":
                        col = list(node.keys())[0]
                        vals = ",".join(f"'{x}'" for x in v)
                        sub_parts.append(f"metadata_->>'{col}' NOT IN ({vals})")
                    elif k == "$contains":
                        col = list(node.keys())[0]
                        sub_parts.append(f"metadata_->>'{col}' LIKE '%{v}%'")
                else:
                    # Simple equality: {"wing": "emotions"} → metadata_->>'wing' = 'emotions'
                    if isinstance(v, dict):
                        # Nested dict with operator
                        inner_conj = " AND "
                        inner_parts = []
                        for op, val in v.items():
                            if op == "$eq":
                                inner_parts.append(f"metadata_->>'{k}' = '{val}'")
                            elif op == "$in":
                                vals = ",".join(f"'{x}'" for x in val)
                                inner_parts.append(f"metadata_->>'{k}' IN ({vals})")
                            elif op == "$nin":
                                vals = ",".join(f"'{x}'" for x in val)
                                inner_parts.append(f"metadata_->>'{k}' NOT IN ({vals})")
                        if inner_parts:
                            sub_parts.append(f"({inner_conj.join(inner_parts)})")
                    else:
                        sub_parts.append(f"metadata_->>'{k}' = '{v}'")

            if sub_parts:
                combined = " OR ".join(sub_parts) if conj == "OR" else " AND ".join(sub_parts)
                if len(sub_parts) > 1 and conj == "AND":
                    parts.append(f"({combined})")
                else:
                    parts.append(combined)

        return "WHERE " + " AND ".join(parts) if parts else ""


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
            raise PalaceNotFoundError(
                "PgVectorBackend requires palace.local_path on PalaceRef"
            )
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
