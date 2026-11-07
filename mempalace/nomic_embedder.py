"""In-process ``nomic-embed-text-v1.5`` embedder (ONNX Runtime, 768 dimensions).

D6 (RUNBOOK-INFRA-700-001) fixes the MemPalace embedder to
``nomic-ai/nomic-embed-text-v1.5`` running **in-process** through ONNX
Runtime. There is no Ollama service on the VPS and no hosted embedding API.

Design constraints
------------------

**Pinned revision.** The model is loaded from one fixed Hugging Face
revision (:data:`NOMIC_REVISION`). A re-published repository therefore cannot
change the vectors under an unchanged configuration.

**No implicit download.** The loader resolves a *local* model directory only.
A missing model raises :class:`NomicModelUnavailable` with the remediation
steps; bytes are fetched only when the operator asks for it explicitly
(``MEMPALACE_NOMIC_ALLOW_DOWNLOAD=1``, or ``--allow-model-download`` on the
G3b copier). The VPS receives the model through Ansible at G4 and must never
download it at runtime.

**Bounded work.** :data:`DEFAULT_MAX_LENGTH` caps the tokenized sequence at
2048 tokens rather than the model's 8192, and :data:`DEFAULT_BATCH_SIZE` caps
how many sequences are encoded at once. The VPS has 3.9 GB of RAM and D6
requires the memory footprint to be measured at G3 before the model is
committed, so the default is deliberately conservative. Both are configurable.

**Two model files.** ``onnx/model.onnx`` (fp32, ~547 MB) is the default.
``onnx/model_quantized.onnx`` (~137 MB) is an opt-in for a tighter memory
budget. **Vectors produced by different model files are not
interchangeable** — a palace re-embedded with the quantized file cannot be
compared against vectors written by the fp32 file.

**Prefixes.** nomic is an asymmetric model: documents are embedded with
``search_document: `` and queries with ``search_query: ``. The prefixes are
applied here, not by the tokenizer, and not by the caller.

Numerics
--------

The pooling mirrors the reference ONNX implementation of this model: mean
pooling over the attention mask, accumulated in float64 and cast back to the
model dtype. No L2 normalisation is applied — pgvector's ``<=>`` operator is
cosine distance and normalises both operands itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

logger = logging.getLogger(__name__)

#: Hugging Face repository that carries the model.
NOMIC_MODEL_ID = "nomic-ai/nomic-embed-text-v1.5"

#: Pinned revision. ``main`` resolved to this commit on 2026-11-07.
NOMIC_REVISION = "e9b6763023c676ca8431644204f50c2b100d9aab"

#: Dimensionality of every vector this module produces.
NOMIC_DIMENSION = 768

#: Asymmetric prefixes required by nomic-embed-text-v1.5.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

#: Model file variants. fp32 is the default; the quantized file is opt-in.
MODEL_FILE_FP32 = "onnx/model.onnx"
MODEL_FILE_QUANTIZED = "onnx/model_quantized.onnx"
SUPPORTED_MODEL_FILES = (MODEL_FILE_FP32, MODEL_FILE_QUANTIZED)

#: Files that must sit next to the model file in a pre-provisioned directory.
TOKENIZER_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "config.json",
)

#: Token cap. The model accepts 8192; 2048 keeps peak memory bounded.
DEFAULT_MAX_LENGTH = 2048

#: Sequences encoded per ONNX run.
DEFAULT_BATCH_SIZE = 8


class NomicError(RuntimeError):
    """Base class for every nomic embedder failure."""


class NomicModelUnavailable(NomicError):
    """The pinned nomic model is not available as a local directory."""


class NomicEmbeddingError(NomicError):
    """The nomic model produced an embedding that cannot be used."""


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, tolerating an absent file."""
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise NomicModelUnavailable(f"cannot read {path}: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def _snapshot_dir(model_file: str, allow_download: bool) -> Path:
    """Resolve the pinned revision from the local Hugging Face cache.

    ``allow_download`` is the only path that reaches the network. It is
    opt-in because the VPS must never fetch model bytes at runtime.
    """
    from huggingface_hub import snapshot_download

    try:
        path = snapshot_download(
            repo_id=NOMIC_MODEL_ID,
            revision=NOMIC_REVISION,
            allow_patterns=[model_file, *TOKENIZER_FILES],
            local_files_only=not allow_download,
        )
    except Exception as exc:  # noqa: BLE001 — huggingface_hub raises many types
        raise NomicModelUnavailable(
            f"{NOMIC_MODEL_ID}@{NOMIC_REVISION} is not available locally "
            f"(missing {model_file}). Pre-provision the model directory and "
            f"point MEMPALACE_NOMIC_MODEL_DIR at it, or allow a one-off "
            f"download with MEMPALACE_NOMIC_ALLOW_DOWNLOAD=1. Cause: {exc}"
        ) from exc
    return Path(path)


def resolve_model_dir(
    *,
    model_dir: Optional[str] = None,
    model_file: str = MODEL_FILE_FP32,
    allow_download: bool = False,
) -> Path:
    """Return a directory holding the model file and the tokenizer files.

    ``model_dir`` is a pre-provisioned directory (the VPS layout). When it is
    not given, the pinned revision is looked up in the local Hugging Face
    cache; the network is only consulted with ``allow_download``.
    """
    if model_file not in SUPPORTED_MODEL_FILES:
        raise ValueError(
            f"unsupported nomic model file {model_file!r}; "
            f"expected one of {', '.join(SUPPORTED_MODEL_FILES)}"
        )
    if model_dir:
        candidate = Path(model_dir).expanduser()
        if not candidate.is_dir():
            raise NomicModelUnavailable(f"nomic model directory does not exist: {candidate}")
        missing = [
            name for name in (model_file, *TOKENIZER_FILES) if not (candidate / name).is_file()
        ]
        if missing:
            raise NomicModelUnavailable(
                f"nomic model directory {candidate} is missing: {', '.join(missing)}"
            )
        return candidate
    return _snapshot_dir(model_file, allow_download)


def _resolve_pad_id(tokenizer: Any, config: dict[str, Any]) -> int:
    """Resolve the padding token id the way transformers would.

    ``tokenizer_config.json`` names the pad token; ``added_tokens_decoder``
    gives its id. The vocabulary lookup is the fallback, and ``0`` is the last
    resort so a missing pad token never silently disables padding.
    """
    decoder = config.get("added_tokens_decoder")
    pad_token = config.get("pad_token")
    if isinstance(decoder, dict) and pad_token:
        for raw_id, spec in decoder.items():
            if isinstance(spec, dict) and spec.get("content") == pad_token:
                try:
                    return int(raw_id)
                except (TypeError, ValueError):
                    break
    if pad_token:
        pad_id = tokenizer.token_to_id(str(pad_token))
        if pad_id is not None:
            return int(pad_id)
    fallback = tokenizer.token_to_id("[PAD]")
    return 0 if fallback is None else int(fallback)


def _mean_pool(hidden: Any, attention_mask: Any) -> Any:
    """Mean-pool ``hidden`` over the positions the attention mask marks real.

    The sum is accumulated in float64 (as the reference implementation does)
    and cast back to the model dtype afterwards.
    """
    import numpy as np

    mask = attention_mask[:, :, None].astype(bool)
    summed = np.sum(hidden, axis=1, where=mask, dtype=np.float64)
    counts = np.sum(attention_mask, axis=1, keepdims=True)
    pooled = summed / np.maximum(counts, 1e-9)
    return pooled.astype(hidden.dtype, copy=False)


def _batches(items: list[str], size: int):
    """Yield successive batches without materialising the whole list twice."""
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


class NomicEmbedder:
    """Embed text with the pinned nomic ONNX model, in-process.

    The model is loaded lazily on the first embed call, so constructing an
    embedder never touches the network or the disk.

    Args:
        model_dir: Pre-provisioned directory holding the model and tokenizer
            files. When omitted the pinned revision is resolved from the
            local Hugging Face cache.
        model_file: Which ONNX file to load. ``onnx/model.onnx`` (fp32) by
            default, ``onnx/model_quantized.onnx`` as an opt-in.
        max_length: Token cap applied to every input.
        batch_size: How many sequences are encoded per ONNX run.
        device: Execution provider hint (``auto``, ``cpu``, ``cuda``,
            ``coreml``, ``dml``). ``None`` uses the configured device.
        allow_download: Permit a one-off download of the pinned revision.
    """

    def __init__(
        self,
        *,
        model_dir: Optional[str] = None,
        model_file: str = MODEL_FILE_FP32,
        max_length: int = DEFAULT_MAX_LENGTH,
        batch_size: int = DEFAULT_BATCH_SIZE,
        device: Optional[str] = None,
        allow_download: bool = False,
    ) -> None:
        if max_length < 1:
            raise ValueError("max_length must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self._model_dir_option = model_dir
        self._model_file = model_file
        self._max_length = int(max_length)
        self._batch_size = int(batch_size)
        self._device = device
        self._allow_download = allow_download
        self._session: Any = None
        self._tokenizer: Any = None
        self._model_dir: Optional[Path] = None
        self._input_names: frozenset[str] = frozenset()

    # ── identity ────────────────────────────────────────────────────────────

    def describe(self) -> dict[str, Any]:
        """Return the embedder identity. Performs no I/O."""
        return {
            "model_id": NOMIC_MODEL_ID,
            "revision": NOMIC_REVISION,
            "model_file": self._model_file,
            "dimension": NOMIC_DIMENSION,
            "max_length": self._max_length,
            "batch_size": self._batch_size,
            "document_prefix": DOCUMENT_PREFIX,
            "query_prefix": QUERY_PREFIX,
        }

    @property
    def max_length(self) -> int:
        """Token cap applied to every input."""
        return self._max_length

    @property
    def batch_size(self) -> int:
        """How many sequences are encoded per ONNX run."""
        return self._batch_size

    # ── loading ─────────────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        model_dir = resolve_model_dir(
            model_dir=self._model_dir_option,
            model_file=self._model_file,
            allow_download=self._allow_download,
        )
        self._model_dir = model_dir
        self._tokenizer = self._load_tokenizer(model_dir)
        self._session = self._load_session(model_dir)
        logger.info(
            "nomic embedder ready (model=%s revision=%s max_length=%d batch_size=%d)",
            self._model_file,
            NOMIC_REVISION,
            self._max_length,
            self._batch_size,
        )

    def _load_tokenizer(self, model_dir: Path) -> Any:
        from tokenizers import Tokenizer

        config = _read_json(model_dir / "tokenizer_config.json")
        tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        tokenizer.enable_truncation(max_length=self._max_length, direction="right")
        tokenizer.enable_padding(
            direction=str(config.get("padding_side") or "right"),
            pad_id=_resolve_pad_id(tokenizer, config),
            pad_token=str(config.get("pad_token") or "[PAD]"),
        )
        return tokenizer

    def _load_session(self, model_dir: Path) -> Any:
        import onnxruntime as ort

        from .embedding import _resolve_providers

        providers, effective = _resolve_providers(self._device)
        session = ort.InferenceSession(str(model_dir / self._model_file), providers=providers)
        self._input_names = frozenset(node.name for node in session.get_inputs())
        logger.info("nomic ONNX session loaded (device=%s providers=%s)", effective, providers)
        return session

    # ── embedding ───────────────────────────────────────────────────────────

    def embed_documents(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed documents with nomic's ``search_document:`` prefix."""
        return self._embed([DOCUMENT_PREFIX + text for text in texts])

    def embed_queries(self, texts: Iterable[str]) -> list[list[float]]:
        """Embed queries with nomic's ``search_query:`` prefix."""
        return self._embed([QUERY_PREFIX + text for text in texts])

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        vectors: list[list[float]] = []
        for batch in _batches(list(texts), self._batch_size):
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        import numpy as np

        encodings = self._tokenizer.encode_batch(batch)
        input_ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.array(
            [encoding.attention_mask for encoding in encodings], dtype=np.int64
        )
        inputs: dict[str, Any] = {"input_ids": input_ids, "attention_mask": attention_mask}
        if "token_type_ids" in self._input_names:
            inputs["token_type_ids"] = np.zeros_like(input_ids)

        hidden = self._session.run(None, inputs)[0]
        pooled = _mean_pool(hidden, attention_mask)
        if pooled.shape[-1] != NOMIC_DIMENSION:
            raise NomicEmbeddingError(
                f"expected {NOMIC_DIMENSION}-dimension vectors, got {pooled.shape[-1]}"
            )
        return [[float(value) for value in row] for row in pooled]


_EMBEDDER_CACHE: dict[tuple[Any, ...], NomicEmbedder] = {}


def get_nomic_embedder(
    *,
    model_dir: Optional[str] = None,
    model_file: Optional[str] = None,
    max_length: Optional[int] = None,
    batch_size: Optional[int] = None,
    device: Optional[str] = None,
    allow_download: Optional[bool] = None,
) -> NomicEmbedder:
    """Return a cached embedder, configured from :class:`MempalaceConfig`.

    Every argument defaults to ``None``, meaning "use the configured value".
    An explicit value overrides the configuration for this embedder only.
    """
    from .config import MempalaceConfig

    config = MempalaceConfig()
    resolved = {
        "model_dir": model_dir if model_dir is not None else config.nomic_model_dir,
        "model_file": model_file if model_file is not None else config.nomic_model_file,
        "max_length": max_length if max_length is not None else config.nomic_max_length,
        "batch_size": batch_size if batch_size is not None else config.nomic_batch_size,
        "device": device if device is not None else config.embedding_device,
        "allow_download": (
            allow_download if allow_download is not None else config.nomic_allow_download
        ),
    }
    key = (
        resolved["model_dir"],
        resolved["model_file"],
        resolved["max_length"],
        resolved["batch_size"],
        resolved["device"],
        resolved["allow_download"],
    )
    cached = _EMBEDDER_CACHE.get(key)
    if cached is None:
        cached = NomicEmbedder(
            model_dir=resolved["model_dir"] or None,
            model_file=resolved["model_file"],
            max_length=resolved["max_length"],
            batch_size=resolved["batch_size"],
            device=resolved["device"],
            allow_download=bool(resolved["allow_download"]),
        )
        _EMBEDDER_CACHE[key] = cached
    return cached
