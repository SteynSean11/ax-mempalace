from unittest.mock import MagicMock, patch

from mempalace.searcher import search_memories


def test_vector_disabled_pgvector_avoids_sqlite():
    drawers_col = MagicMock()
    drawers_col.get.return_value = MagicMock(
        documents=["policy says never apologize for policy breaches"],
        metadatas=[
            {
                "wing": "shop",
                "room": "guides",
                "source_file": "brand_voice_D4.md",
                "filed_at": "2026-05-12T22:00:00",
                "chunk_index": 0,
            }
        ],
    )

    with patch("mempalace.searcher.get_active_backend_name", return_value="pgvector"), patch(
        "mempalace.searcher.get_collection", return_value=drawers_col
    ), patch("mempalace.searcher.sqlite3.connect", side_effect=AssertionError("sqlite should not be touched")):
        result = search_memories("never apologize policy", "/fake/palace", vector_disabled=True)

    assert result["fallback"] == "bm25_only_via_pgvector"
    assert result["results"][0]["source_file"] == "brand_voice_D4.md"


def test_union_pgvector_avoids_sqlite():
    drawers_col = MagicMock()
    drawers_col.query.return_value = {
        "ids": [["D1"]],
        "documents": [["narrative support reply about shipped order"]],
        "metadatas": [[{"wing": "shop", "room": "support", "source_file": "ticket_D1.md"}]],
        "distances": [[0.2]],
    }
    drawers_col.get.side_effect = [
        MagicMock(
            documents=["Brand voice rules: never say Absolutely and do not apologize for policy."],
            metadatas=[
                {
                    "wing": "shop",
                    "room": "guides",
                    "source_file": "brand_voice_D4.md",
                    "filed_at": "2026-05-12T22:00:00",
                    "chunk_index": 0,
                }
            ],
        )
    ]

    with patch("mempalace.searcher.get_active_backend_name", return_value="pgvector"), patch(
        "mempalace.searcher.get_collection", return_value=drawers_col
    ), patch("mempalace.searcher.get_closets_collection", side_effect=RuntimeError("no closets")), patch(
        "mempalace.searcher.sqlite3.connect", side_effect=AssertionError("sqlite should not be touched")
    ):
        result = search_memories(
            "never say Absolutely and do not apologize for policy",
            "/fake/palace",
            candidate_strategy="union",
            n_results=5,
        )

    assert any(hit["source_file"] == "brand_voice_D4.md" for hit in result["results"])
