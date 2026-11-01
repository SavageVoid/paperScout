"""Encoders.

Two implementations of one interface:

* ``SentenceTransformerEncoder`` - the real thing, pinned by revision, CPU only.
* ``HashEncoder`` - deterministic, dependency-free stand-in used by the unit
  tests and by `EMBED_ENCODER=hash` plumbing checks. It is NOT semantically
  meaningful: never leave it enabled for anything you intend to query.

Both produce L2-normalised float32 vectors of the configured dimension, so the
rest of the pipeline cannot tell them apart.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Encoder(Protocol):
    dim: int
    normalize: bool

    @property
    def model_id(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    def info(self) -> dict: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


def _fingerprint(model_id: str, revision: str, dim: int, normalize: bool, template: str) -> str:
    """Identity of the vector space.

    Everything that changes the meaning of a stored vector must be in here: a
    different model, a different revision of the same model, a different
    dimension, no/yes normalisation, or a different way of composing the text.
    """
    raw = f"{model_id}|{revision}|{dim}|{int(normalize)}|{template}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _l2_normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0 or not math.isfinite(norm):
        # Degenerate vector: return a stable unit basis vector rather than NaN,
        # so a stored row can never poison cosine distance.
        out = [0.0] * len(vec)
        if out:
            out[0] = 1.0
        return out
    return [v / norm for v in vec]


@dataclass
class HashEncoder:
    """Deterministic content hash expanded to `dim` floats. Tests only."""

    dim: int = 384
    normalize: bool = True
    template: str = "title_abstract_v1"
    _model_id: str = "docpulse/hash-encoder"
    _model_revision: str = "1"

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_revision(self) -> str:
        return self._model_revision

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_id, self.model_revision, self.dim, self.normalize, self.template)

    def info(self) -> dict:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dim": self.dim,
            "normalize": self.normalize,
            "template": self.template,
            "fingerprint": self.fingerprint,
            "kind": "hash",
        }

    def _one(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self.dim:
            digest = hashlib.sha256(
                text.encode("utf-8") + b"|" + str(counter).encode("ascii")
            ).digest()  # 32 bytes -> 8 float32
            out.extend(struct.unpack(">8f", digest))
            counter += 1
        vec = out[: self.dim]
        # map to a stable, bounded range
        vec = [math.tanh(v) for v in vec]
        return _l2_normalise(vec) if self.normalize else vec

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]


class SentenceTransformerEncoder:
    """all-MiniLM-L6-v2 (or whatever EMBED_MODEL_ID says), pinned by revision."""

    def __init__(
        self,
        model_id: str,
        revision: str,
        dim: int,
        normalize: bool = True,
        device: str = "cpu",
        template: str = "title_abstract_v1",
        batch_size: int = 32,
    ) -> None:
        self._model_id = model_id
        self._revision = revision
        self.dim = dim
        self.normalize = normalize
        self.device = device
        self.template = template
        self.batch_size = batch_size
        self._model = None  # loaded lazily: keeps imports cheap everywhere else

    # -- identity ---------------------------------------------------------
    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_revision(self) -> str:
        return self._revision

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.model_id, self._revision, self.dim, self.normalize, self.template)

    def info(self) -> dict:
        return {
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dim": self.dim,
            "normalize": self.normalize,
            "template": self.template,
            "fingerprint": self.fingerprint,
            "kind": "sentence-transformers",
        }

    # -- loading ----------------------------------------------------------
    def load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer  # lazy

        self._model = SentenceTransformer(
            self._model_id,
            revision=self._revision,
            device=self.device,
        )
        actual = self._model.get_sentence_embedding_dimension()
        if actual is not None and int(actual) != self.dim:
            raise RuntimeError(
                f"model {self._model_id}@{self._revision[:8]} produces {actual}-d vectors "
                f"but EMBED_DIM={self.dim}. Fix EMBED_DIM or the model; do not mix."
            )

    # -- work -------------------------------------------------------------
    def encode(self, texts: list[str]) -> list[list[float]]:
        self.load()
        assert self._model is not None
        arr = self._model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        out = [[float(x) for x in row] for row in arr]
        for row in out:
            if len(row) != self.dim:
                raise RuntimeError(f"encoder returned dim {len(row)}, expected {self.dim}")
            if any((not math.isfinite(v)) for v in row):
                raise RuntimeError("encoder produced a non-finite value; refusing to store it")
        return out


def build_encoder(settings, template: str | None = None) -> Encoder:
    """EMBED_ENCODER = auto | sentence-transformers | hash."""
    template = template or settings.text_template
    choice = settings.encoder
    if choice == "hash":
        return HashEncoder(dim=settings.dim, normalize=settings.normalize, template=template)
    if choice in {"auto", "sentence-transformers", "st"}:
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            if choice == "auto":
                raise RuntimeError(
                    "sentence-transformers is not importable here. Run this inside the "
                    "`embedder` container (make embed), or set EMBED_ENCODER=hash for a "
                    "plumbing-only run. Doing an automatic silent fallback would put "
                    "meaningless vectors in the table, so this is deliberately fatal."
                ) from None
            raise
        return SentenceTransformerEncoder(
            model_id=settings.model_id,
            revision=settings.model_revision,
            dim=settings.dim,
            normalize=settings.normalize,
            device=settings.device,
            template=template,
            batch_size=settings.batch_size,
        )
    raise ValueError(f"unknown EMBED_ENCODER={settings.encoder!r}")
