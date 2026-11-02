#!/usr/bin/env python3
"""Create the DocPulse database, the pgvector extension and the Day 3 tables.

Idempotent: safe to run any number of times. It connects to the maintenance
database first, creates the target database if it is missing, then switches to it
and runs the schema from docpulse.embed.db.

Why a separate database rather than the Airflow metadata DB: Airflow owns its
schema and migrates it on upgrade. Putting pipeline tables in there works until
an Airflow upgrade runs `airflow db migrate` and you have two things to be afraid
of at once.
"""

from __future__ import annotations

import sys

import os

from docpulse.embed.config import EmbeddingSettings


def main() -> int:
    settings = EmbeddingSettings.from_env()
    allow_shared = "--allow-shared-db" in sys.argv
    try:
        import psycopg
        from psycopg import conninfo
    except ImportError:
        print("psycopg is not installed here. Run this inside the embedder container:")
        print("  make embed-db-init")
        return 2

    params = conninfo.conninfo_to_dict(settings.dsn)
    target_db = params.get("dbname") or "docpulse_embed"

    # The postgres image defaults the database name to POSTGRES_USER when
    # POSTGRES_DB is unset, so this is how the Airflow metadata database got its
    # name. Writing pipeline tables in there is what A5 in CONFLICTS says not to do.
    airflow_db_candidates = {
        os.environ.get("POSTGRES_DB", "").strip(),
        os.environ.get("POSTGRES_USER", "").strip(),
    } - {""}
    if target_db in airflow_db_candidates and not allow_shared:
        print(f"REFUSING: the target database {target_db!r} is also the one Airflow")
        print("uses (POSTGRES_DB or POSTGRES_USER). Day 3 should keep its own tables")
        print("out of Airflow's schema, which Airflow migrates on upgrade.")
        print("")
        print("Fix: unset DOCPULSE_DB (the default is docpulse_embed), or set")
        print("     DOCPULSE_DB=docpulse_embed in .env, or set a full DOCPULSE_PG_DSN.")
        print("Override anyway with: --allow-shared-db")
        return 3

    admin_params = dict(params, dbname="postgres")

    print(f"admin connect -> {params.get('host')}:{params.get('port')} as {params.get('user')}")
    with psycopg.connect(conninfo.make_conninfo(**admin_params), autocommit=True) as admin:
        with admin.cursor() as cur:
            cur.execute("select 1 from pg_database where datname = %s", (target_db,))
            if cur.fetchone():
                print(f"database {target_db!r} already exists")
            else:
                # identifiers cannot be parameterised; quote properly
                cur.execute(f'create database "{target_db}"')
                print(f"database {target_db!r} created")

    from docpulse.embed.db import PgEmbeddingDb

    db = PgEmbeddingDb(settings.dsn, table=settings.table, lock_key=settings.lock_key)
    try:
        info = db.ensure_schema(settings.dim)
    finally:
        db.close()

    print(f"schema ready: {info}")
    print(f"table={settings.table} dim={settings.dim}")
    print("next: make embed-dry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
