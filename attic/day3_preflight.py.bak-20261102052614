#!/usr/bin/env python3
"""Day 3 preflight: check every assumption the embedding package makes.

Run it twice, in both containers:

  docker compose exec -T airflow-scheduler python - < scripts/day3_preflight.py
  docker compose -f docker-compose.yml -f docker-compose.day3.yml run --rm --entrypoint python embedder - < scripts/day3_preflight.py

It reads only; it writes nothing except one temp read of a silver file. Exit code
1 means a BLOCKER: do not run `make embed` until it is resolved. Paste the whole
output back and any mismatch is a one-line fix in docpulse/embed/compat.py.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import traceback

BLOCKERS: list[str] = []
NOTES: list[str] = []


def say(section: str) -> None:
    print(f"\n=== {section} ===")


def ok(msg: str) -> None:
    print(f"  PASS  {msg}")


def note(msg: str) -> None:
    print(f"  NOTE  {msg}")
    NOTES.append(msg)


def blocker(msg: str) -> None:
    print(f"  BLOCK {msg}")
    BLOCKERS.append(msg)


# --------------------------------------------------------------------------
say("environment")
print(f"  python        {sys.version.split()[0]}")
print(f"  executable    {sys.executable}")
print(f"  cwd           {os.getcwd()}")
print(f"  PYTHONPATH    {os.environ.get('PYTHONPATH', '(unset)')}")
for var in (
    "AIRFLOW_HOME",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "DOCPULSE_PG_DSN",
    "DOCPULSE_DB",
    "S3_ENDPOINT",
    "S3_BUCKET",
    "EMBED_MODEL_ID",
    "EMBED_ENCODER",
    "HF_HOME",
):
    if os.environ.get(var):
        shown = "*" * 6 if "PASSWORD" in var else os.environ[var]
        if var == "DOCPULSE_PG_DSN":
            shown = "(set, redacted)"
        print(f"  env {var:<16} {shown}")
    else:
        print(f"  env {var:<16} (unset)")

# --------------------------------------------------------------------------
say("imports")
for mod in (
    "pyarrow",
    "boto3",
    "psycopg",
    "pgvector",
    "torch",
    "sentence_transformers",
    "docpulse",
    "docpulse.storage",
    "docpulse.silver.build",
    "docpulse.embed",
):
    try:
        m = __import__(mod, fromlist=["*"])
        ver = getattr(m, "__version__", "")
        ok(f"{mod} {ver}".strip())
    except Exception as exc:
        if mod in {"torch", "sentence_transformers"}:
            note(f"{mod} missing ({type(exc).__name__}) — expected in airflow-scheduler, required in embedder")
        elif mod.startswith("docpulse"):
            blocker(f"{mod} missing ({type(exc).__name__}: {exc})")
        else:
            blocker(f"{mod} missing ({type(exc).__name__}: {exc})")

# --------------------------------------------------------------------------
say("storage + silver (the assumptions in embed/compat.py)")
store = None
rows_sample: list[dict] = []
try:
    from docpulse.embed import compat

    store = compat.get_store()
    ok("A1/A2/A3 store constructed")
    keys = store.list_keys("")
    print(f"  total keys under lake root: {len(keys)}")
    silver_keys = [k for k in keys if k.startswith("silver/")]
    print(f"  silver keys: {len(silver_keys)}")
    for k in silver_keys[:6]:
        print(f"    {k}")
    if len(silver_keys) > 6:
        print(f"    ... {len(silver_keys) - 6} more")

    snap = compat.load_silver(store)
    print(f"  A4 manifest key   {snap.manifest_key}")
    print(f"  A5 manifest       n_papers_total={snap.manifest.get('n_papers_total')} "
          f"months={len(snap.manifest.get('months') or [])} "
          f"contract_version={snap.manifest.get('contract_version')}")
    print(f"  A6 decode mode    {snap.decode_mode}")
    print(f"     rows decoded   {snap.n_rows}")
    if snap.n_rows:
        ok("silver decodes end-to-end")
    else:
        blocker("silver decoded to zero rows")

    # Show the ACTUAL column names — this is what tells me whether compat guessed right.
    first_key = (snap.manifest.get("months") or [None])[0]
    if isinstance(first_key, dict):
        first_key = first_key.get("key")
    if first_key:
        from docpulse.embed.compat import _decode_month  # local helper, read-only

        decoded, mode = _decode_month(store, first_key)
        if decoded:
            cols = sorted(decoded[0].keys())
            print(f"  columns in {first_key.split('/')[-1]} ({mode}):")
            print("   ", ", ".join(cols))
            for label, candidates in (
                ("id", compat.ID_KEYS),
                ("version", compat.VERSION_KEYS),
                ("text_hash", compat.HASH_KEYS),
            ):
                hit = [c for c in candidates if c in decoded[0]]
                if hit:
                    print(f"  mapped {label:<10} <- {hit[0]}")
                else:
                    note(f"no {label} column found; candidates were {candidates}")
except Exception as exc:
    blocker(f"storage/silver check failed: {type(exc).__name__}: {exc}")
    traceback.print_exc(limit=2)

# --------------------------------------------------------------------------
say("contract")
try:
    from docpulse.silver.contract import load_contract  # type: ignore
    from docpulse.silver.tasks import default_contract_path  # type: ignore
    import yaml

    path = default_contract_path()
    contract = load_contract(path)
    print(f"  path: {path}")
    data = contract if isinstance(contract, dict) else getattr(contract, "__dict__", {})
    cols = data.get("columns") if isinstance(data, dict) else None
    if isinstance(cols, dict):
        print(f"  columns ({len(cols)}): {', '.join(list(cols)[:12])}"
              + (" ..." if len(cols) > 12 else ""))
    elif isinstance(cols, list):
        names = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        print(f"  columns ({len(names)}): {', '.join(names[:12])}" + (" ..." if len(names) > 12 else ""))
    else:
        print(f"  loaded, keys: {list(data)[:10] if isinstance(data, dict) else type(contract)}")
    ok("contract loads")
except Exception as exc:
    note(f"contract introspection unavailable ({type(exc).__name__}: {exc}) — not required by Day 3")

# --------------------------------------------------------------------------
say("postgres / pgvector")
try:
    from docpulse.embed.config import EmbeddingSettings, dsn_from_env

    settings = EmbeddingSettings.from_env()
    print(f"  dsn (redacted): {settings.public()['dsn_host']}")
    import psycopg

    with psycopg.connect(settings.dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("select current_database(), version()")
            db, ver = cur.fetchone()
            print(f"  connected to    {db}")
            print(f"  server          {ver.split(',')[0]}")
            cur.execute("select extversion from pg_extension where extname = 'vector'")
            r = cur.fetchone()
            if r:
                ok(f"pgvector extension {r[0]}")
            else:
                note("pgvector extension not installed in this database yet (make embed-db-init creates it)")
            cur.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by 1"
            )
            tables = [x[0] for x in cur.fetchall()]
            print(f"  tables          {tables or '(none)'}")
    ok("postgres reachable")
except ImportError:
    note("psycopg not installed here (expected in airflow-scheduler); the embedder will check it")
except Exception as exc:
    blocker(f"postgres unreachable: {type(exc).__name__}: {exc}")

# --------------------------------------------------------------------------
say("disk")
try:
    for path in ("/var/lib/docker", "/", os.getcwd()):
        try:
            du = shutil.disk_usage(path)
            free_gb = du.free / 1e9
            mark = "PASS" if free_gb >= 6 else "BLOCK"
            print(f"  {mark}  {path:<18} {free_gb:.1f} GB free ({du.used / du.total * 100:.0f}% used)")
            if free_gb < 3:
                blocker(f"{path} has only {free_gb:.1f} GB free; a torch image needs ~2.5 GB")
            elif free_gb < 6:
                note(f"{path} has {free_gb:.1f} GB free; tight for the embedder image")
        except Exception as exc:
            note(f"cannot stat {path}: {exc}")
except Exception:
    pass

# --------------------------------------------------------------------------
say("model cache")
hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
print(f"  HF_HOME = {hf_home}")
if os.path.isdir(hf_home):
    for root, dirs, files in os.walk(hf_home):
        depth = root[len(hf_home):].count(os.sep)
        if depth > 3:
            dirs[:] = []
        if "config.json" in files or "model.safetensors" in files:
            print(f"  cached: {root}")
else:
    note("model not cached yet — first `make embed` downloads ~90 MB")

# --------------------------------------------------------------------------
say("verdict")
if BLOCKERS:
    print(f"  {len(BLOCKERS)} blocker(s):")
    for b in BLOCKERS:
        print(f"    - {b}")
    print("\n  Do not run `make embed` yet. Paste this output back.")
    sys.exit(1)
print("  no blockers" + (f"; {len(NOTES)} note(s):" if NOTES else ""))
for n in NOTES:
    print(f"    - {n}")
print("\n  Next: make embed-db-init, then make embed-dry")
