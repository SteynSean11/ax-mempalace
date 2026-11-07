"""Offline tests for the pgvector where-clause compiler.

The compiler is pure: it turns a Chroma-style where clause into a SQL
template plus bound parameters, so every case here runs without a database.
The live behaviour is additionally covered by
``test_pgvector_postgres.py``, which is skipped unless a PostgreSQL URI is
supplied.
"""

from __future__ import annotations

import psycopg2.extensions
import pytest

from mempalace.backends.base import UnsupportedFilterError
from mempalace.backends.pgvector import _build_where


def _render(template: str, params: dict) -> str:
    """Substitute the metadata column and the bound parameters.

    Mirrors what psycopg2 does at execute time: the column token is replaced
    by the quoted identifier and the parameters are interpolated as literals.
    """
    statement = template.format(metadata_column='"metadata_"')
    values = {
        name: psycopg2.extensions.adapt(value).getquoted().decode("utf-8")
        for name, value in params.items()
    }
    return statement % values


def _string_literals(statement: str) -> list[str]:
    """Return the single-quoted literals of a rendered statement, unescaped."""
    literals: list[str] = []
    index = 0
    while index < len(statement):
        if statement[index] != "'":
            index += 1
            continue
        index += 1
        start = index
        while index < len(statement):
            if statement[index] == "'":
                if index + 1 < len(statement) and statement[index + 1] == "'":
                    index += 2
                    continue
                break
            index += 1
        literals.append(statement[start:index].replace("''", "'"))
        index += 1
    return literals


def _strip_literals(statement: str) -> str:
    """Return the statement with every string literal replaced by ``?``."""
    parts: list[str] = []
    index = 0
    while index < len(statement):
        if statement[index] != "'":
            parts.append(statement[index])
            index += 1
            continue
        index += 1
        while index < len(statement):
            if statement[index] == "'":
                if index + 1 < len(statement) and statement[index + 1] == "'":
                    index += 2
                    continue
                break
            index += 1
        index += 1
        parts.append("?")
    return "".join(parts)


# ── supported forms ──────────────────────────────────────────────────────────


def test_empty_where_produces_no_clause():
    assert _build_where(None) == ("", {})
    assert _build_where({}) == ("", {})


def test_simple_equality():
    template, params = _build_where({"wing": "emotions"})
    assert template == "WHERE {metadata_column}->>%(w0)s = %(w1)s"
    assert params == {"w0": "wing", "w1": "emotions"}


def test_explicit_equality():
    template, params = _build_where({"wing": {"$eq": "emotions"}})
    assert template == "WHERE {metadata_column}->>%(w0)s = %(w1)s"
    assert params == {"w0": "wing", "w1": "emotions"}


def test_inequality():
    template, params = _build_where({"wing": {"$ne": "emotions"}})
    assert template == "WHERE {metadata_column}->>%(w0)s != %(w1)s"
    assert params == {"w0": "wing", "w1": "emotions"}


def test_membership():
    template, params = _build_where({"wing": {"$in": ["a", "b"]}})
    assert template == "WHERE {metadata_column}->>%(w0)s IN (%(w1)s, %(w2)s)"
    assert params == {"w0": "wing", "w1": "a", "w2": "b"}


def test_non_membership():
    template, params = _build_where({"wing": {"$nin": ["a"]}})
    assert template == "WHERE {metadata_column}->>%(w0)s NOT IN (%(w1)s)"
    assert params == {"w0": "wing", "w1": "a"}


def test_and_group():
    template, params = _build_where({"$and": [{"wing": "a"}, {"room": "b"}]})
    assert template == (
        "WHERE ({metadata_column}->>%(w0)s = %(w1)s AND {metadata_column}->>%(w2)s = %(w3)s)"
    )
    assert params == {"w0": "wing", "w1": "a", "w2": "room", "w3": "b"}


def test_or_group():
    template, params = _build_where({"$or": [{"wing": "a"}, {"wing": "b"}]})
    assert template == (
        "WHERE ({metadata_column}->>%(w0)s = %(w1)s OR {metadata_column}->>%(w2)s = %(w3)s)"
    )
    assert params == {"w0": "wing", "w1": "a", "w2": "wing", "w3": "b"}


def test_multiple_keys_are_anded():
    template, params = _build_where({"wing": "a", "room": "b"})
    assert template == (
        "WHERE ({metadata_column}->>%(w0)s = %(w1)s AND {metadata_column}->>%(w2)s = %(w3)s)"
    )
    assert params == {"w0": "wing", "w1": "a", "w2": "room", "w3": "b"}


def test_contains_is_a_literal_substring_match():
    template, params = _build_where({"wing": {"$contains": "mo"}})
    assert template == "WHERE {metadata_column}->>%(w0)s LIKE %(w1)s"
    assert params == {"w0": "wing", "w1": "%mo%"}


def test_contains_escapes_wildcards():
    _template, params = _build_where({"wing": {"$contains": "50%"}})
    assert params["w1"] == "%50\\%%"


def test_boolean_values_render_as_json():
    _template, params = _build_where({"flag": {"$eq": True}})
    assert params["w1"] == "true"


def test_numeric_values_render_as_json():
    _template, params = _build_where({"count": {"$eq": 5}})
    assert params["w1"] == "5"


# ── operator allow-list ──────────────────────────────────────────────────────


def test_unknown_operator_is_rejected():
    with pytest.raises(UnsupportedFilterError):
        _build_where({"count": {"$gt": 5}})


def test_unknown_top_level_operator_is_rejected():
    with pytest.raises(UnsupportedFilterError):
        _build_where({"$gt": 5})


def test_nested_group_operator_is_rejected():
    with pytest.raises(UnsupportedFilterError):
        _build_where({"wing": {"$and": [{"a": 1}]}})


def test_empty_group_is_rejected():
    with pytest.raises(UnsupportedFilterError):
        _build_where({"$and": []})


def test_non_mapping_node_is_rejected():
    with pytest.raises(UnsupportedFilterError):
        _build_where({"$and": ["wing"]})


# ── injection attempts ───────────────────────────────────────────────────────


def test_injection_payload_in_a_value_stays_literal():
    payload = "x' OR '1'='1"
    template, params = _build_where({"wing": payload})
    assert payload not in template
    assert params["w1"] == payload
    rendered = _render(template, params)
    assert _string_literals(rendered) == ["wing", payload]
    assert _strip_literals(rendered) == 'WHERE "metadata_"->>? = ?'


def test_injection_payload_in_a_key_stays_literal():
    payload = "wing'; DROP TABLE mempalace_drawers; --"
    template, params = _build_where({payload: "value"})
    assert payload not in template
    assert params["w0"] == payload
    rendered = _render(template, params)
    # The payload is one complete string literal; nothing escaped it.
    assert _string_literals(rendered) == [payload, "value"]
    assert _strip_literals(rendered) == 'WHERE "metadata_"->>? = ?'


def test_injection_payload_cannot_close_the_statement():
    payload = "'); DELETE FROM mempalace_drawers; --"
    template, params = _build_where({"wing": {"$in": [payload]}})
    rendered = _render(template, params)
    assert _string_literals(rendered) == ["wing", payload]
    assert _strip_literals(rendered) == 'WHERE "metadata_"->>? IN (?)'


def test_statement_terminator_in_a_value_cannot_add_clauses():
    payload = "emotions' OR metadata_->>'wing' = 'other"
    template, params = _build_where({"wing": payload})
    rendered = _render(template, params)
    assert _string_literals(rendered) == ["wing", payload]
    assert _strip_literals(rendered) == 'WHERE "metadata_"->>? = ?'
