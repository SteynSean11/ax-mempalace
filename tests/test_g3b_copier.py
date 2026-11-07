"""Synthetic, offline checks for the G3b drawer copier."""

import json
import sqlite3

import psycopg2.sql
import pytest

from mempalace.backends.base import GetResult
from mempalace.backends.pgvector import PgVectorCollection
from mempalace.g3b_copier import (
    _PgServiceConnection,
    canonical_hash,
    copy_drawers,
    copy_kg_snapshot,
    main,
)


def test_cli_refuses_implicit_postgres_target(monkeypatch, capsys):
    monkeypatch.setenv("PGHOST", "127.0.0.1")
    monkeypatch.setenv("PGDATABASE", "omega")
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--source-snapshot",
                "/unused",
                "--target-model",
                "nomic",
                "--expected-dimension",
                "768",
            ]
        )
    assert error.value.code == 2
    assert "--target-service" in capsys.readouterr().err


def test_service_identity_reports_only_allowlisted_fields():
    class Connection:
        closed = False

        def get_dsn_parameters(self):
            return {
                "host": "scratch.local",
                "port": "15432",
                "dbname": "scratch",
                "password": "redacted",
            }

        def close(self):
            self.closed = True

    connection = Connection()
    service = _PgServiceConnection("g3b-scratch", connect=lambda *, service: connection)
    assert service.identity() == {"host": "scratch.local", "port": "15432", "database": "scratch"}
    service.close()
    assert connection.closed


class FakeCollection:
    def __init__(self, rows):
        self.rows = dict(rows)
        self.writes = []

    def count(self):
        return len(self.rows)

    def get(self, *, ids=None, limit=None, offset=None, include=None):
        del include
        selected = sorted(self.rows) if ids is None else [item for item in ids if item in self.rows]
        if limit is not None:
            selected = selected[offset : offset + limit]
        return GetResult(
            ids=selected,
            documents=[self.rows[item][0] for item in selected],
            metadatas=[self.rows[item][1] for item in selected],
        )

    def upsert_migrated(self, *, ids, documents, metadatas, embeddings, created_at):
        self.writes.append((tuple(ids), tuple(created_at), tuple(tuple(v) for v in embeddings)))
        for index, drawer_id in enumerate(ids):
            assert drawer_id not in self.rows
            self.rows[drawer_id] = documents[index], metadatas[index]


def _rows():
    return {
        "a": (
            "verbatim A",
            {"wing": "technical", "room": "day", "filed_at": "2026-01-01T00:00:00Z"},
        ),
        "b": (
            "verbatim B",
            {"wing": "technical", "room": "day", "created_at": "2026-01-02T00:00:00Z"},
        ),
        "c": ("verbatim C", {"wing": "family", "room": "day"}),
    }


def test_dry_run_is_read_only_and_reports_hashes():
    source = FakeCollection(_rows())
    target = FakeCollection({"a": _rows()["a"]})

    def forbidden_embed(_documents):
        raise AssertionError("dry run must not embed")

    report = copy_drawers(source, target, forbidden_embed, expected_dimension=3, page_size=2)
    assert report.existing_ids == ("a",)
    assert report.planned_ids == ("b", "c")
    assert report.copied_ids == ()
    assert report.sampled_hashes[0] == ("a", canonical_hash("a", *_rows()["a"]))
    assert not target.writes


def test_execute_preserves_ids_metadata_timestamps_and_resumes():
    source = FakeCollection(_rows())
    target = FakeCollection({"a": _rows()["a"]})
    calls = []

    def embed(documents):
        calls.extend(documents)
        return [[1.0, 2.0, 3.0] for _ in documents]

    first = copy_drawers(source, target, embed, expected_dimension=3, page_size=2, execute=True)
    assert first.copied_ids == ("b", "c")
    assert target.rows == source.rows
    assert target.writes == [
        (("b",), ("2026-01-02T00:00:00Z",), ((1.0, 2.0, 3.0),)),
        (("c",), (None,), ((1.0, 2.0, 3.0),)),
    ]
    assert calls == ["verbatim B", "verbatim C"]
    second = copy_drawers(source, target, embed, expected_dimension=3, execute=True)
    assert second.copied_ids == ()
    assert second.existing_ids == ("a", "b", "c")
    assert calls == ["verbatim B", "verbatim C"]


def test_conflict_stops_without_writing():
    source = FakeCollection(_rows())
    target = FakeCollection({"a": ("different", _rows()["a"][1])})
    with pytest.raises(ValueError, match="target conflict"):
        copy_drawers(
            source,
            target,
            lambda docs: [[0.0] * 3 for _ in docs],
            expected_dimension=3,
            execute=True,
        )
    assert not target.writes


def test_dimension_mismatch_stops_before_write():
    source = FakeCollection(_rows())
    target = FakeCollection({})
    with pytest.raises(ValueError, match="dimension"):
        copy_drawers(
            source,
            target,
            lambda docs: [[0.0] * 2 for _ in docs],
            expected_dimension=3,
            execute=True,
        )
    assert not target.writes


def test_kg_copy_uses_synthetic_snapshot_and_refuses_existing_target(tmp_path):
    source = tmp_path / "source.sqlite3"
    target = tmp_path / "target.sqlite3"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE entities (id TEXT PRIMARY KEY, name TEXT, type TEXT, properties TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE triples (id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, valid_from TEXT, valid_to TEXT, confidence REAL, source_closet TEXT, source_file TEXT, source_drawer_id TEXT, adapter_name TEXT, extracted_at TEXT)"
        )
        conn.execute("INSERT INTO entities (id) VALUES ('synthetic')")
    assert copy_kg_snapshot(source, target) == (1, 0)
    assert not target.exists()
    assert copy_kg_snapshot(source, target, execute=True) == (1, 0)
    with pytest.raises(FileExistsError):
        copy_kg_snapshot(source, target, execute=True)


def _statement_text(statement) -> str:
    """Render a psycopg2 composed statement to plain SQL for assertions.

    ``psycopg2.sql.Identifier.as_string`` needs a live connection, so the
    identifier is quoted here with the same rule psycopg2 uses.
    """
    if isinstance(statement, str):
        return statement
    rendered = []
    for part in statement._wrapped:
        if isinstance(part, psycopg2.sql.Identifier):
            rendered.append('"{}"'.format(part._wrapped[0].replace('"', '""')))
        else:
            rendered.append(part.as_string(None))
    return "".join(rendered)


def test_nomic_target_model_uses_the_in_process_embedder(monkeypatch, capsys):
    """``--target-model nomic`` must embed through the in-process ONNX model."""
    embedded: list[str] = []

    class FakeEmbedder:
        def describe(self):
            return {"model_id": "nomic-ai/nomic-embed-text-v1.5", "dimension": 768}

        def embed_documents(self, documents):
            embedded.extend(documents)
            return [[0.0] * 768 for _ in documents]

    def forbidden_ollama(_texts):
        raise AssertionError("the Ollama path must not be used for --target-model nomic")

    monkeypatch.setattr(
        "mempalace.nomic_embedder.get_nomic_embedder", lambda **kwargs: FakeEmbedder()
    )
    monkeypatch.setattr("mempalace.backends.pgvector._ollama_embed", forbidden_ollama)

    source = FakeCollection(_rows())

    class FakeSourceBackend:
        def get_collection(self, **_kwargs):
            return source

        def close(self):
            pass

    class FakeService:
        def __init__(self, service, connect=None):
            self.service = service

        def identity(self):
            return {"host": "scratch", "port": "15432", "database": "scratch"}

        def close(self):
            pass

    monkeypatch.setattr("mempalace.backends.chroma.ChromaBackend", FakeSourceBackend)
    monkeypatch.setattr(
        "mempalace.backends.pgvector.PgVectorCollection",
        lambda *_args, **_kwargs: FakeCollection({}),
    )
    monkeypatch.setattr("mempalace.g3b_copier._PgServiceConnection", FakeService)

    exit_code = main(
        [
            "--source-snapshot",
            "/unused",
            "--target-model",
            "nomic",
            "--target-service",
            "scratch",
            "--expected-dimension",
            "768",
            "--execute",
        ]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["embedder"]["model_id"] == "nomic-ai/nomic-embed-text-v1.5"
    assert report["copied_ids"] == ["a", "b", "c"]
    assert embedded == ["verbatim A", "verbatim B", "verbatim C"]


def test_pgvector_migration_writer_preserves_timestamp_and_rejects_collision():
    class Cursor:
        rowcount = 1

        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            self.calls.append((sql, params))

    class Connection:
        closed = False

        def __init__(self):
            self.cursor_instance = Cursor()
            self.commits = 0
            self.rollbacks = 0

        def cursor(self):
            return self.cursor_instance

        def commit(self):
            self.commits += 1

        def rollback(self):
            self.rollbacks += 1

    conn = Connection()
    collection = PgVectorCollection(lambda: conn, "mempalace_drawers", embedding_dim=3)
    collection.upsert_migrated(
        ids=["synthetic"],
        documents=["verbatim"],
        metadatas=[{"wing": "technical", "room": "day"}],
        embeddings=[[1.0, 2.0, 3.0]],
        created_at=["2026-01-01T00:00:00Z"],
    )
    sql, params = conn.cursor_instance.calls[0]
    statement = _statement_text(sql)
    assert "created_at" in statement and "ON CONFLICT (id) DO NOTHING" in statement
    assert '"mempalace_drawers"' in statement
    assert params[-2:] == ("[1.0,2.0,3.0]", "2026-01-01T00:00:00Z")
    assert conn.commits == 1

    conn.cursor_instance.rowcount = 0
    with pytest.raises(ValueError, match="target ID appeared"):
        collection.upsert_migrated(
            ids=["synthetic"],
            documents=["verbatim"],
            metadatas=[{"wing": "technical", "room": "day"}],
            embeddings=[[1.0, 2.0, 3.0]],
            created_at=["2026-01-01T00:00:00Z"],
        )
    assert conn.rollbacks == 1
