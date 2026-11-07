"""Live PostgreSQL checks for the pgvector backend.

These tests are skipped unless ``MEMPALACE_TEST_PG_URI`` points at a
PostgreSQL instance the test may create and drop tables in. They exist so
the compiled where clause can be proven against a real server — the offline
suite in ``test_pgvector_where.py`` only proves the compiled template.
"""

from __future__ import annotations

import os

import pytest

from mempalace.backends.base import UnsupportedFilterError
from mempalace.backends.pgvector import PgVectorCollection

URI = os.environ.get("MEMPALACE_TEST_PG_URI", "")
pytestmark = pytest.mark.skipif(
    not URI, reason="MEMPALACE_TEST_PG_URI is not set (live PostgreSQL check)"
)

TABLE = "mempalace_test_drawers"

ROWS = [
    ("d1", "first drawer", {"wing": "emotions", "room": "day1", "count": 5}),
    ("d2", "second drawer", {"wing": "technical", "room": "day1", "count": 7}),
    ("d3", "third drawer", {"wing": "emotions", "room": "day2", "count": 9}),
]


@pytest.fixture
def collection():
    import json

    import psycopg2

    conn = psycopg2.connect(URI)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
        cur.execute(f'CREATE TABLE "{TABLE}" (id TEXT PRIMARY KEY, document TEXT, metadata_ JSONB)')
        for row_id, document, metadata in ROWS:
            cur.execute(
                f'INSERT INTO "{TABLE}" (id, document, metadata_) VALUES (%s, %s, %s::jsonb)',
                (row_id, document, json.dumps(metadata)),
            )

    def factory():
        return conn

    try:
        yield PgVectorCollection(factory, TABLE)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{TABLE}"')
        conn.close()


def test_simple_equality_filters(collection):
    result = collection.get(where={"wing": "emotions"})
    assert sorted(result.ids) == ["d1", "d3"]


def test_and_group_filters(collection):
    result = collection.get(where={"$and": [{"wing": "emotions"}, {"room": "day1"}]})
    assert result.ids == ["d1"]


def test_or_group_filters(collection):
    result = collection.get(where={"$or": [{"wing": "technical"}, {"room": "day2"}]})
    assert sorted(result.ids) == ["d2", "d3"]


def test_inequality_filters(collection):
    result = collection.get(where={"wing": {"$ne": "emotions"}})
    assert result.ids == ["d2"]


def test_membership_filters(collection):
    result = collection.get(where={"wing": {"$in": ["technical"]}})
    assert result.ids == ["d2"]


def test_numeric_values_compare_as_text(collection):
    result = collection.get(where={"count": {"$eq": 7}})
    assert result.ids == ["d2"]


def test_contains_filters(collection):
    result = collection.get(where={"wing": {"$contains": "motion"}})
    assert sorted(result.ids) == ["d1", "d3"]


def test_unknown_operator_is_rejected(collection):
    with pytest.raises(UnsupportedFilterError):
        collection.get(where={"count": {"$gt": 5}})


def test_injection_payload_is_treated_as_a_literal(collection):
    payload = "emotions' OR '1'='1"
    result = collection.get(where={"wing": payload})
    assert result.ids == []


def test_delete_honours_the_where_clause(collection):
    collection.delete(where={"wing": "emotions"})
    remaining = collection.get()
    assert remaining.ids == ["d2"]
