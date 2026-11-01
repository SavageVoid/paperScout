"""Airflow wrappers (Option B only — see README_DAY3.md).

These are here so the optional DAG has something to call, and so the signatures
match the Day 1/2 convention. They do NOT make the embedding run work inside the
airflow image: that image has no torch, and adding it would roughly triple its
size on a partition with 8 GB free. The supported default is `make embed`
(Option A, a one-off container). If you run these inside Airflow without torch
installed you get one clear error, not a mystery traceback.
"""

from __future__ import annotations

from . import compat
from .config import EmbeddingSettings
from .encoder import build_encoder


def _deps():
    from .db import open_db

    settings = EmbeddingSettings.from_env()
    store = compat.get_store()
    encoder = build_encoder(settings)  # raises a clear message if torch is absent
    return settings, store, encoder, open_db


def run_build_embeddings(**_: object) -> dict:
    from .build import run as build_run

    settings, store, encoder, open_db = _deps()
    with open_db(settings) as db:
        db.ensure_schema(settings.dim)
        return build_run(
            store=store,
            db=db,
            encoder=encoder,
            settings=settings,
            silver_loader=compat.load_silver,
        )


def run_verify_embeddings(**_: object) -> dict:
    from .verify import run as verify_run

    settings, store, encoder, open_db = _deps()
    with open_db(settings) as db:
        result = verify_run(
            store=store,
            db=db,
            encoder=encoder,
            settings=settings,
            silver_loader=compat.load_silver,
        )
    if not result["ok"]:
        raise ValueError("embedding verification failed: " + "; ".join(result["errors"]))
    return result
