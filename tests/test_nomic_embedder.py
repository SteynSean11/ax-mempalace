"""Offline tests for the in-process nomic embedder.

No test in this file downloads the model. The ONNX session and the tokenizer
are fakes, so the suite runs without the 547 MB model file and without a
network. The one test that exercises the real tokenizer file only builds a
tiny ``tokenizer.json`` in a temporary directory.
"""

from __future__ import annotations

import json

import pytest

from mempalace import nomic_embedder as ne
from mempalace.nomic_embedder import (
    DOCUMENT_PREFIX,
    MODEL_FILE_FP32,
    MODEL_FILE_QUANTIZED,
    NOMIC_DIMENSION,
    NOMIC_MODEL_ID,
    NOMIC_REVISION,
    QUERY_PREFIX,
    NomicEmbeddingError,
    NomicEmbedder,
    NomicModelUnavailable,
    get_nomic_embedder,
    resolve_model_dir,
)


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeEncoding:
    def __init__(self, ids: list[int]):
        self.ids = list(ids)
        self.attention_mask = [1] * len(self.ids)


class FakeTokenizer:
    """Minimal stand-in for ``tokenizers.Tokenizer``."""

    def __init__(self):
        self.seen: list[str] = []
        self.truncation: dict | None = None
        self.padding: dict | None = None

    def enable_truncation(self, max_length: int, direction: str = "right"):
        self.truncation = {"max_length": max_length, "direction": direction}

    def enable_padding(self, **kwargs):
        self.padding = kwargs

    def token_to_id(self, token: str):
        return {"[PAD]": 0, "[UNK]": 100}.get(token)

    def encode_batch(self, texts):
        self.seen.extend(texts)
        encodings = [
            FakeEncoding([101 + index] * len(text.split())) for index, text in enumerate(texts)
        ]
        width = max((len(encoding.ids) for encoding in encodings), default=0)
        for encoding in encodings:
            padding = width - len(encoding.ids)
            encoding.ids.extend([0] * padding)
            encoding.attention_mask.extend([0] * padding)
        return encodings


class FakeNode:
    def __init__(self, name: str):
        self.name = name


class FakeSession:
    """Deterministic stand-in for an ONNX Runtime inference session.

    ``hidden[b, t, j]`` is ``(t + 1) * (j + 1)``, so the pooled value for a
    row with ``k`` real tokens is ``(j + 1) * (k + 1) / 2``.
    """

    def __init__(self, width: int = NOMIC_DIMENSION):
        self.width = width
        self.calls: list[dict] = []

    def get_inputs(self):
        return [FakeNode("input_ids"), FakeNode("attention_mask"), FakeNode("token_type_ids")]

    def run(self, output_names, inputs):
        import numpy as np

        self.calls.append(inputs)
        batch = inputs["input_ids"].shape[0]
        seq = inputs["input_ids"].shape[1]
        hidden = np.zeros((batch, seq, self.width), dtype=np.float32)
        for position in range(seq):
            for column in range(self.width):
                hidden[:, position, column] = (position + 1) * (column + 1)
        return [hidden]


@pytest.fixture
def fake_model(monkeypatch):
    """Install a fake tokenizer and session; return the fake tokenizer."""
    tokenizer = FakeTokenizer()
    session = FakeSession()

    monkeypatch.setattr(
        ne, "resolve_model_dir", lambda **kwargs: __import__("pathlib").Path("/fake/model")
    )
    monkeypatch.setattr(NomicEmbedder, "_load_tokenizer", lambda self, model_dir: tokenizer)
    monkeypatch.setattr(NomicEmbedder, "_load_session", lambda self, model_dir: session)
    return tokenizer


# ── prefixes ─────────────────────────────────────────────────────────────────


def test_document_prefix_is_applied(fake_model):
    embedder = NomicEmbedder()
    embedder.embed_documents(["hello world"])
    assert fake_model.seen == [DOCUMENT_PREFIX + "hello world"]


def test_query_prefix_is_applied(fake_model):
    embedder = NomicEmbedder()
    embedder.embed_queries(["hello world"])
    assert fake_model.seen == [QUERY_PREFIX + "hello world"]


def test_document_and_query_prefixes_differ():
    assert DOCUMENT_PREFIX == "search_document: "
    assert QUERY_PREFIX == "search_query: "


def test_prefix_is_applied_once_per_text(fake_model):
    embedder = NomicEmbedder()
    embedder.embed_documents(["alpha", "beta"])
    assert fake_model.seen == [
        DOCUMENT_PREFIX + "alpha",
        DOCUMENT_PREFIX + "beta",
    ]


# ── dimensions ───────────────────────────────────────────────────────────────


def test_embeddings_are_768_dimensions(fake_model):
    vectors = NomicEmbedder().embed_documents(["hello"])
    assert len(vectors) == 1
    assert len(vectors[0]) == NOMIC_DIMENSION == 768


def test_wrong_model_width_is_rejected(monkeypatch):
    monkeypatch.setattr(
        ne, "resolve_model_dir", lambda **kwargs: __import__("pathlib").Path("/fake/model")
    )
    monkeypatch.setattr(NomicEmbedder, "_load_tokenizer", lambda self, model_dir: FakeTokenizer())
    monkeypatch.setattr(
        NomicEmbedder, "_load_session", lambda self, model_dir: FakeSession(width=384)
    )
    with pytest.raises(NomicEmbeddingError):
        NomicEmbedder().embed_documents(["hello"])


def test_empty_input_returns_no_vectors(fake_model):
    assert NomicEmbedder().embed_documents([]) == []
    assert NomicEmbedder().embed_queries([]) == []


# ── pooling ──────────────────────────────────────────────────────────────────


def test_mean_pooling_ignores_padding(fake_model):
    vectors = NomicEmbedder().embed_documents(["one two three"])
    # The prefixed text carries four real tokens, so the mean of the
    # per-position weights 1, 2, 3, 4 is 2.5 -> column j contributes
    # (j + 1) * 2.5.
    real_tokens = len(fake_model.seen[0].split())
    assert real_tokens == 4
    expected = (1 + 2 + 3 + 4) / 4
    assert vectors[0][0] == pytest.approx(expected * 1.0)
    assert vectors[0][1] == pytest.approx(expected * 2.0)


def test_batches_do_not_change_results(monkeypatch):
    monkeypatch.setattr(
        ne, "resolve_model_dir", lambda **kwargs: __import__("pathlib").Path("/fake/model")
    )
    monkeypatch.setattr(NomicEmbedder, "_load_tokenizer", lambda self, model_dir: FakeTokenizer())
    monkeypatch.setattr(NomicEmbedder, "_load_session", lambda self, model_dir: FakeSession())

    single = NomicEmbedder(batch_size=1).embed_documents(["one two", "three"])
    batched = NomicEmbedder(batch_size=8).embed_documents(["one two", "three"])
    assert single == batched


# ── model resolution ─────────────────────────────────────────────────────────


def test_preprovisioned_directory_is_accepted(tmp_path):
    for name in (MODEL_FILE_FP32, *ne.TOKENIZER_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")
    resolved = resolve_model_dir(model_dir=str(tmp_path))
    assert resolved == tmp_path


def test_preprovisioned_directory_requires_every_file(tmp_path):
    with pytest.raises(NomicModelUnavailable) as error:
        resolve_model_dir(model_dir=str(tmp_path))
    assert "missing" in str(error.value)


def test_missing_preprovisioned_directory_is_reported(tmp_path):
    with pytest.raises(NomicModelUnavailable):
        resolve_model_dir(model_dir=str(tmp_path / "absent"))


def test_unsupported_model_file_is_rejected():
    with pytest.raises(ValueError):
        resolve_model_dir(model_file="onnx/model_bnb4.onnx")


def test_quantized_model_file_is_supported(tmp_path):
    for name in (MODEL_FILE_QUANTIZED, *ne.TOKENIZER_FILES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("stub", encoding="utf-8")
    assert resolve_model_dir(model_dir=str(tmp_path), model_file=MODEL_FILE_QUANTIZED) == tmp_path


def test_missing_model_never_downloads_implicitly(monkeypatch):
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        raise OSError("cache miss")

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    with pytest.raises(NomicModelUnavailable) as error:
        resolve_model_dir()
    assert calls and calls[0]["local_files_only"] is True
    assert calls[0]["revision"] == NOMIC_REVISION
    assert "MEMPALACE_NOMIC_MODEL_DIR" in str(error.value)


def test_download_is_opt_in(monkeypatch):
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        return "/tmp/fake-snapshot"

    monkeypatch.setattr("huggingface_hub.snapshot_download", fake_snapshot_download)
    resolve_model_dir(allow_download=True)
    assert calls[0]["local_files_only"] is False
    assert calls[0]["repo_id"] == NOMIC_MODEL_ID


# ── tokenizer wiring ─────────────────────────────────────────────────────────


def test_tokenizer_is_capped_and_padded(monkeypatch, tmp_path):
    tokenizer = FakeTokenizer()
    monkeypatch.setattr("tokenizers.Tokenizer.from_file", staticmethod(lambda path: tokenizer))
    (tmp_path / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "pad_token": "[PAD]",
                "padding_side": "right",
                "added_tokens_decoder": {"0": {"content": "[PAD]"}},
            }
        ),
        encoding="utf-8",
    )
    embedder = NomicEmbedder(max_length=512)
    loaded = embedder._load_tokenizer(tmp_path)
    assert loaded is tokenizer
    assert tokenizer.truncation == {"max_length": 512, "direction": "right"}
    assert tokenizer.padding["pad_id"] == 0
    assert tokenizer.padding["pad_token"] == "[PAD]"


def test_read_json_tolerates_absent_file(tmp_path):
    assert ne._read_json(tmp_path / "absent.json") == {}


def test_read_json_reports_broken_file(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(NomicModelUnavailable):
        ne._read_json(broken)


# ── configuration ────────────────────────────────────────────────────────────


def test_embedder_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_NOMIC_MODEL_DIR", "/opt/nomic")
    first = get_nomic_embedder()
    second = get_nomic_embedder()
    assert first is second


def test_embedder_describes_itself(fake_model):
    described = NomicEmbedder(model_file=MODEL_FILE_QUANTIZED).describe()
    assert described["model_id"] == NOMIC_MODEL_ID
    assert described["revision"] == NOMIC_REVISION
    assert described["model_file"] == MODEL_FILE_QUANTIZED
    assert described["dimension"] == 768
    assert described["document_prefix"] == DOCUMENT_PREFIX
    assert described["query_prefix"] == QUERY_PREFIX


def test_invalid_bounds_are_rejected():
    with pytest.raises(ValueError):
        NomicEmbedder(max_length=0)
    with pytest.raises(ValueError):
        NomicEmbedder(batch_size=0)


# ── provider selection in the pgvector backend ───────────────────────────────


class RecordingEmbedder:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []

    def embed_documents(self, texts):
        self.calls.append(("documents", list(texts)))
        return [[0.0] * 768 for _ in texts]

    def embed_queries(self, texts):
        self.calls.append(("queries", list(texts)))
        return [[0.0] * 768 for _ in texts]


def test_pgvector_backend_embeds_in_process_by_default(monkeypatch):
    from mempalace.backends import pgvector

    recorder = RecordingEmbedder()
    monkeypatch.setenv("MEMPALACE_EMBEDDING_PROVIDER", "nomic")
    monkeypatch.setattr("mempalace.nomic_embedder.get_nomic_embedder", lambda **kwargs: recorder)

    def forbidden_ollama(_texts):
        raise AssertionError("the Ollama path must not run unless it is opted into")

    monkeypatch.setattr(pgvector, "_ollama_embed", forbidden_ollama)

    assert pgvector._embed_documents(["hello"]) == [[0.0] * 768]
    assert pgvector._embed_queries(["hello"]) == [[0.0] * 768]
    assert recorder.calls == [("documents", ["hello"]), ("queries", ["hello"])]


def test_pgvector_backend_uses_ollama_when_opted_in(monkeypatch):
    from mempalace.backends import pgvector

    monkeypatch.setenv("MEMPALACE_EMBEDDING_PROVIDER", "ollama")

    class Response:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {"embedding": [0.0] * 768}

    monkeypatch.setattr(pgvector.requests, "post", lambda *args, **kwargs: Response())
    assert pgvector._embed_documents(["hello"]) == [[0.0] * 768]


def test_unknown_provider_is_rejected(monkeypatch):
    from mempalace.backends import pgvector

    monkeypatch.setenv("MEMPALACE_EMBEDDING_PROVIDER", "banana")
    with pytest.raises(ValueError, match="unknown embedding provider"):
        pgvector._embed_documents(["hello"])
