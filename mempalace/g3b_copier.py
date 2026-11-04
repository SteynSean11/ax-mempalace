"""Offline, explicit Chroma to pgvector drawer copy for INFRA-700 G3b.

The source must be a throwaway working copy of an immutable snapshot.
Opening a palace through ChromaBackend may write to that working copy.
Importing this module does no I/O.
The command is a dry run unless ``--execute`` is supplied at an open gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .backends.base import BaseCollection, GetResult


class MigrationTarget(Protocol):
    def get(self, *, ids: list[str], include: list[str]) -> GetResult: ...

    def upsert_migrated(
        self,
        *,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict],
        embeddings: list[list[float]],
        created_at: list[str | None],
    ) -> None: ...


@dataclass(frozen=True)
class CopyReport:
    source_count: int
    existing_ids: tuple[str, ...]
    planned_ids: tuple[str, ...]
    copied_ids: tuple[str, ...]
    sampled_hashes: tuple[tuple[str, str], ...]


class _PgServiceConnection:
    """Connect only through the explicitly named libpq service."""

    def __init__(self, service: str, connect: Callable | None = None):
        if not service.strip():
            raise ValueError("target service name is required")
        self.service = service
        self._connect = connect
        self._connection = None

    def __call__(self):
        if self._connection is None or self._connection.closed:
            if self._connect is None:
                import psycopg2

                self._connect = psycopg2.connect
            try:
                self._connection = self._connect(service=self.service)
            except Exception:
                raise RuntimeError("target service connection failed") from None
        return self._connection

    def identity(self) -> dict[str, str]:
        """Allow-list non-secret target fields from the resolved connection."""
        parameters = self().get_dsn_parameters()
        identity = {
            "host": parameters.get("host", ""),
            "port": parameters.get("port", ""),
            "database": parameters.get("dbname", ""),
        }
        if not all(identity.values()):
            raise ValueError("target service must resolve host, port and database")
        return identity

    def close(self) -> None:
        if self._connection is not None and not self._connection.closed:
            self._connection.close()


def canonical_hash(drawer_id: str, document: str, metadata: dict) -> str:
    """Hash source-preserved fields, excluding model-dependent vectors."""
    payload = json.dumps(
        {"id": drawer_id, "document": document, "metadata": metadata},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _timestamp(metadata: dict) -> str | None:
    value = metadata.get("created_at", metadata.get("filed_at"))
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("source timestamp must be a nonempty string")
    return value


def copy_drawers(
    source: BaseCollection,
    target: MigrationTarget,
    embed: Callable[[list[str]], list[list[float]]],
    *,
    expected_dimension: int,
    page_size: int = 100,
    execute: bool = False,
) -> CopyReport:
    """Page an immutable source, skip identical IDs, and stop on conflicts.

    A resumed run compares every destination ID's canonical fields before
    skipping it. It never overwrites a destination row with different data.
    """
    if page_size < 1 or expected_dimension < 1:
        raise ValueError("page_size and expected_dimension must be positive")
    source_count = source.count()
    seen: set[str] = set()
    existing: list[str] = []
    planned: list[str] = []
    copied: list[str] = []
    samples: dict[str, str] = {}

    for offset in range(0, source_count, page_size):
        page = source.get(limit=page_size, offset=offset, include=["documents", "metadatas"])
        if len(page.ids) != min(page_size, source_count - offset):
            raise ValueError("source page length changed; use an immutable snapshot")
        if len(page.documents) != len(page.ids) or len(page.metadatas) != len(page.ids):
            raise ValueError("source page lacks documents or metadata")
        if any(drawer_id in seen for drawer_id in page.ids):
            raise ValueError("duplicate source ID; use a stable immutable snapshot")
        seen.update(page.ids)
        destination = target.get(ids=page.ids, include=["documents", "metadatas"])
        if len(destination.ids) != len(destination.documents) or len(destination.ids) != len(
            destination.metadatas
        ):
            raise ValueError("target read lacks documents or metadata")
        if len(set(destination.ids)) != len(destination.ids) or not set(destination.ids).issubset(
            page.ids
        ):
            raise ValueError("target returned duplicate or unexpected IDs")
        target_rows = {
            drawer_id: (destination.documents[index], destination.metadatas[index])
            for index, drawer_id in enumerate(destination.ids)
        }
        pending: list[tuple[str, str, dict, str | None]] = []
        for index, drawer_id in enumerate(page.ids):
            document = page.documents[index]
            metadata = page.metadatas[index]
            if not isinstance(document, str) or not document or not isinstance(metadata, dict):
                raise ValueError("invalid source document or metadata")
            if (
                not isinstance(metadata.get("wing"), str)
                or not metadata.get("wing")
                or not isinstance(metadata.get("room"), str)
                or not metadata.get("room")
            ):
                raise ValueError("source drawer lacks wing or room")
            digest = canonical_hash(drawer_id, document, metadata)
            samples[drawer_id] = digest
            if drawer_id in target_rows:
                target_doc, target_meta = target_rows[drawer_id]
                if digest != canonical_hash(drawer_id, target_doc, target_meta):
                    raise ValueError(f"target conflict for drawer ID {drawer_id}")
                existing.append(drawer_id)
            else:
                pending.append((drawer_id, document, metadata, _timestamp(metadata)))
                planned.append(drawer_id)
        if execute and pending:
            documents = [row[1] for row in pending]
            vectors = embed(documents)
            if len(vectors) != len(pending) or any(
                len(vector) != expected_dimension for vector in vectors
            ):
                raise ValueError("target embedder dimension or batch length mismatch")
            target.upsert_migrated(
                ids=[row[0] for row in pending],
                documents=documents,
                metadatas=[row[2] for row in pending],
                embeddings=vectors,
                created_at=[row[3] for row in pending],
            )
            copied.extend(row[0] for row in pending)

    if source.count() != source_count or len(seen) != source_count:
        raise ValueError("source changed during copy; use an immutable snapshot")
    return CopyReport(
        source_count=source_count,
        existing_ids=tuple(sorted(existing)),
        planned_ids=tuple(sorted(planned)),
        copied_ids=tuple(sorted(copied)),
        sampled_hashes=tuple(sorted(samples.items())[:10]),
    )


def kg_inventory(path: Path) -> tuple[int, int]:
    """Return entity and triple counts from a SQLite snapshot, without rows."""
    if not path.is_file():
        raise FileNotFoundError(path)
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
        if quick_check != ("ok",):
            raise ValueError("KG SQLite quick_check failed")
        required = {
            "entities": {"id", "name", "type", "properties", "created_at"},
            "triples": {
                "id",
                "subject",
                "predicate",
                "object",
                "valid_from",
                "valid_to",
                "confidence",
                "source_closet",
                "source_file",
                "source_drawer_id",
                "adapter_name",
                "extracted_at",
            },
        }
        for table, columns in required.items():
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                raise ValueError(f"KG {table} schema needs a separate conversion")
        entities = conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        triples = conn.execute("SELECT count(*) FROM triples").fetchone()[0]
    return entities, triples


def copy_kg_snapshot(source: Path, destination: Path, *, execute: bool = False) -> tuple[int, int]:
    """Copy a standalone KG snapshot; never merge into an existing target."""
    counts = kg_inventory(source)
    if destination.exists():
        raise FileExistsError(destination)
    if execute:
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
        if kg_inventory(destination) != counts:
            raise ValueError("KG copy counts differ from source")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="G3b snapshot drawer copier; dry-run by default")
    parser.add_argument("--source-snapshot", type=Path, required=True)
    parser.add_argument(
        "--collection",
        choices=("mempalace_drawers", "mempalace_closets"),
        default="mempalace_drawers",
    )
    parser.add_argument("--target-model", choices=("minilm", "nomic"), required=True)
    parser.add_argument("--target-service", required=True, help="explicit libpq service name")
    parser.add_argument("--expected-dimension", type=int, required=True)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--kg-source", type=Path)
    parser.add_argument("--kg-target", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if (args.kg_source is None) != (args.kg_target is None):
        parser.error("--kg-source and --kg-target must be supplied together")
    expected = {"minilm": 384, "nomic": 768}[args.target_model]
    if args.expected_dimension != expected:
        parser.error("expected dimension does not match selected model")

    from .backends.base import PalaceRef
    from .backends.chroma import ChromaBackend
    from .backends.pgvector import PgVectorCollection, _ollama_embed
    from .embedding import get_embedding_function

    target_connection = _PgServiceConnection(args.target_service)
    source_backend = None
    try:
        target_identity = target_connection.identity()
        source_backend = ChromaBackend()
        palace = PalaceRef(id="g3b-snapshot", local_path=str(args.source_snapshot.resolve()))
        source = source_backend.get_collection(
            palace=palace, collection_name=args.collection, create=False
        )
        target = PgVectorCollection(
            target_connection,
            args.collection,
            embedding_dim=args.expected_dimension,
        )
        if args.target_model == "minilm":

            def embed(documents: list[str]) -> list[list[float]]:
                embedding_function = get_embedding_function("cpu")
                return [list(vector) for vector in embedding_function(documents)]
        else:
            embed = _ollama_embed
        report = copy_drawers(
            source,
            target,
            embed,
            expected_dimension=args.expected_dimension,
            page_size=args.page_size,
            execute=args.execute,
        )
        result: dict = {
            "mode": "execute" if args.execute else "dry-run",
            "target": {"service": args.target_service, **target_identity},
            "source_count": report.source_count,
            "existing_ids": report.existing_ids,
            "planned_ids": report.planned_ids,
            "copied_ids": report.copied_ids,
            "sampled_hashes": report.sampled_hashes,
        }
        if args.kg_source and args.kg_target:
            result["kg_counts"] = copy_kg_snapshot(
                args.kg_source, args.kg_target, execute=args.execute
            )
        print(json.dumps(result, sort_keys=True))
    finally:
        if source_backend is not None:
            source_backend.close()
        target_connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
