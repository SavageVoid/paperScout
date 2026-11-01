"""Configuration for the embedding layer.

Everything is read from the environment so that the same code runs in three
places: the ``embedder`` container (real run), the ``airflow-scheduler``
container (preflight / dry run), and your host venv (unit tests).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import quote

# Pinned 2026-11-02 from https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2
# (`sha` field, lastModified 2026-06-01). Pin by commit: a floating `main` means
# your vectors change under you without a code change.
DEFAULT_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DEFAULT_DIM = 384

# v1: title + "\n\n" + abstract, stripped, truncated to EMBED_MAX_CHARS.
# Bump this if you ever change how text is composed, so old vectors are invalidated.
TEXT_TEMPLATE_VERSION = "title_abstract_v1"


def _bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


DEFAULT_DB = "docpulse_embed"  # NOT "docpulse": with POSTGRES_DB unset the postgres
# image defaults the database name to POSTGRES_USER, so on this stack "docpulse" is
# very likely Airflow's own metadata database. Day 3 must not live there.


def dsn_from_env(env: Mapping[str, str]) -> str:
    """Build the Postgres DSN.

    Deliberate choice: inside a container the database port is always 5432.
    ``POSTGRES_PORT`` in your .env is the *host* port mapping (you set it to 5433
    if 5432 was taken) and must never be used here, or the connect will fail with
    a confusing timeout. ``DOCPULSE_PG_PORT`` is separate for that reason.
    """
    explicit = (env.get("DOCPULSE_PG_DSN") or "").strip()
    if explicit:
        return explicit
    user = env.get("POSTGRES_USER", "postgres")
    password = env.get("POSTGRES_PASSWORD", "postgres")
    host = env.get("DOCPULSE_PG_HOST", "postgres")
    port = env.get("DOCPULSE_PG_PORT", "5432")
    name = env.get("DOCPULSE_DB", DEFAULT_DB)
    return f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{name}"


@dataclass(frozen=True)
class EmbeddingSettings:
    # --- storage / db -------------------------------------------------------
    dsn: str
    table: str = "paper_embeddings"
    manifest_key: str = "gold/embeddings/_manifest.json"

    # --- model --------------------------------------------------------------
    model_id: str = DEFAULT_MODEL_ID
    model_revision: str = DEFAULT_MODEL_REVISION
    dim: int = DEFAULT_DIM
    normalize: bool = True
    encoder: str = "auto"  # auto | sentence-transformers | hash
    device: str = "cpu"

    # --- batching / cost ----------------------------------------------------
    batch_size: int = 32
    max_chars: int = 4000
    limit: int | None = None

    # --- safety -------------------------------------------------------------
    allow_model_change: bool = False
    prune_orphans: bool = False
    lock_key: int = 811_2026  # arbitrary, stable advisory-lock id

    extras: dict[str, str] = field(default_factory=dict)

    @property
    def text_template(self) -> str:
        return TEXT_TEMPLATE_VERSION

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "EmbeddingSettings":
        env = dict(os.environ if env is None else env)
        return cls(
            dsn=dsn_from_env(env),
            table=(env.get("DOCPULSE_EMBED_TABLE") or "paper_embeddings").strip(),
            manifest_key=(
                env.get("DOCPULSE_EMBED_MANIFEST_KEY") or "gold/embeddings/_manifest.json"
            ).strip(),
            model_id=(env.get("EMBED_MODEL_ID") or DEFAULT_MODEL_ID).strip(),
            model_revision=(
                env.get("EMBED_MODEL_REVISION") or DEFAULT_MODEL_REVISION
            ).strip(),
            dim=_int(env.get("EMBED_DIM"), DEFAULT_DIM),
            normalize=_bool(env.get("EMBED_NORMALIZE"), True),
            encoder=(env.get("EMBED_ENCODER") or "auto").strip().lower(),
            device=(env.get("EMBED_DEVICE") or "cpu").strip(),
            batch_size=max(1, _int(env.get("EMBED_BATCH_SIZE"), 32)),
            max_chars=max(64, _int(env.get("EMBED_MAX_CHARS"), 4000)),
            limit=(_int(env["EMBED_LIMIT"], 0) or None) if env.get("EMBED_LIMIT") else None,
            allow_model_change=_bool(env.get("EMBED_ALLOW_MODEL_CHANGE"), False),
            prune_orphans=_bool(env.get("EMBED_PRUNE_ORPHANS"), False),
        )

    def public(self) -> dict:
        """Safe to print: no DSN password."""
        return {
            "table": self.table,
            "manifest_key": self.manifest_key,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "dim": self.dim,
            "normalize": self.normalize,
            "encoder": self.encoder,
            "batch_size": self.batch_size,
            "max_chars": self.max_chars,
            "text_template": self.text_template,
            "allow_model_change": self.allow_model_change,
            "prune_orphans": self.prune_orphans,
            "dsn_host": _redact(self.dsn),
        }


def _redact(dsn: str) -> str:
    """postgresql://user:secret@host:5432/db -> postgresql://user:***@host:5432/db"""
    try:
        head, tail = dsn.split("://", 1)
        creds, hostpart = tail.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{head}://{user}:***@{hostpart}"
    except (ValueError, IndexError):
        return "REDACTED"
