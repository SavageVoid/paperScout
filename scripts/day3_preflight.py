#!/usr/bin/env python3
"""Day 3 preflight: check every assumption the embedding package makes.

Run it in both places:

  docker compose exec -T airflow-scheduler python - < scripts/day3_preflight.py
  docker compose -f docker-compose.yml -f docker-compose.day3.yml run --rm --entrypoint python embedder - < scripts/day3_preflight.py

It detects which container it is in and only demands the packages that container
is supposed to have. Exit code 1 means a BLOCKER; a NOTE never fails the run.

  --role=core      force the airflow-scheduler expectation (no torch, no psycopg)
  --role=embedder  force the embedder expectation (torch, psycopg, pgvector required)
"""

from __future__ import annotations

import os
import shutil
import sys
import traceback

ROLE_ARG = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--role=")), "auto")
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


def can_import(mod: str) -> bool:
    try:
        __import__(mod, fromlist=["*"])
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# which container am I in?
role = ROLE_ARG
if role == "auto":
    role = "embedder" if can_import("sentence_transformers") or can_import("torch") else "core"

say("environment")
print(f"  role          {role}" + ("  (detected)" if ROLE_ARG == "auto" else "  (forced)"))
print(f"  python        {sys.version.split()[0]}")
print(f"  cwd           {os.getcwd()}")
print(f"  PYTHONPATH    {os.environ.get('PYTHONPATH', '(unset)')}")
for var in (
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PORT",
    "POSTGRES_PASSWORD",
    "DOCPULSE_PG_DSN",
    "DOCPULSE_DB",
    "DOCPULSE_PG_HOST",
    "DOCPULSE_PG_PORT",
    "S3_ENDPOINT_URL",
    "S3_ENDPOINT",
    "S3_REGION",
    "S3_BUCKET",
    "EMBED_MODEL_ID",
    "EMBED_ENCODER",
    "HF_HOME",
):
    raw = os.environ.get(var)
    if not raw:
        print(f"  env {var:<18} (unset)")
    elif "PASSWORD" in var or var == "DOCPULSE_PG_DSN":
        print(f"  env {var:<18} (set, value hidden)")
    else:
        print(f"  env {var:<18} {raw}")

# --------------------------------------------------------------------------
say("imports")
REQUIRED = {
    "core": ["pyarrow", "boto3", "docpulse", "docpulse.storage", "docpulse.silver.build", "docpulse.embed"],
    "embedder": [
        "pyarrow", "boto3", "psycopg", "pgvector", "torch", "sentence_transformers",
        "transformers", "docpulse", "docpulse.storage", "docpulse.silver.build", "docpulse.embed",
    ],
}[role]
OPTIONAL = {
    "core": {"torch": "required in the embedder", "sentence_transformers": "required in the embedder",
             "psycopg": "required in the embedder", "pgvector": "required in the embedder"},
    "embedder": {},
}[role]

for mod in REQUIRED + list(OPTIONAL):
    try:
        m = __import__(mod, fromlist=["*"])
        ver = getattr(m, "__version__", "")
        ok(f"{mod} {ver}".strip())
    except Exception as exc:
        if mod in OPTIONAL:
            note(f"{mod} missing ({type(exc).__name__}) — {OPTIONAL[mod]}, not a problem here")
        else:
            blocker(f"{mod} missing ({type(exc).__name__}: {exc})")

# --------------------------------------------------------------------------
creds = {
    k: ("set" if os.environ.get(k) else "MISSING")
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "S3_REGION")
}
print("  credentials   " + "  ".join(f"{k}={v}" for k, v in creds.items()))
say("storage + silver (the assumptions in embed/compat.py)")
store = None
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

    first_key = (snap.manifest.get("months") or [None])[0]
    if isinstance(first_key, dict):
        first_key = first_key.get("key")
    if first_key:
        from docpulse.embed.compat import _decode_month

        decoded, mode = _decode_month(store, first_key)
        if decoded:
            cols = sorted(decoded[0].keys())
            print(f"  columns in {first_key.split('/')[-1]} ({mode}):")
            print("   ", ", ".join(cols))
            mapped = {}
            for label, candidates in (
                ("id", compat.ID_KEYS),
                ("version", compat.VERSION_KEYS),
                ("text_hash", compat.HASH_KEYS),
            ):
                hit = [c for c in candidates if c in decoded[0]]
                mapped[label] = hit[0] if hit else None
                if hit:
                    print(f"  mapped {label:<10} <- {hit[0]}")
                else:
                    note(f"no {label} column found; candidates were {candidates}")
            for label in ("id", "text_hash"):
                if mapped.get(label) is None:
                    blocker(f"cannot identify the {label} column — compat.py needs the real name")
except Exception as exc:
    blocker(f"storage/silver check failed: {type(exc).__name__}: {exc}")
    traceback.print_exc(limit=2)

# --------------------------------------------------------------------------
say("contract")
try:
    from docpulse.silver.contract import load_contract  # type: ignore
    from docpulse.silver.tasks import default_contract_path  # type: ignore

    path = default_contract_path()
    contract = load_contract(path)
    print(f"  path: {path}")
    data = contract if isinstance(contract, dict) else getattr(contract, "__dict__", {})
    cols = data.get("columns") if isinstance(data, dict) else None
    if isinstance(cols, dict):
        print(f"  columns ({len(cols)}): {', '.join(list(cols)[:12])}" + (" ..." if len(cols) > 12 else ""))
    elif isinstance(cols, list):
        names = [c.get("name") if isinstance(c, dict) else str(c) for c in cols]
        print(f"  columns ({len(names)}): {', '.join(names[:12])}" + (" ..." if len(names) > 12 else ""))
    else:
        print(f"  loaded, keys: {list(data)[:10] if isinstance(data, dict) else type(contract)}")
    for field in ("version", "sha256", "primary_key", "partition_column"):
        if isinstance(data, dict) and field in data:
            print(f"  {field}: {data[field]}")
    ok("contract loads")
except Exception as exc:
    note(f"contract introspection unavailable ({type(exc).__name__}: {exc}) — not required by Day 3")

# --------------------------------------------------------------------------
say("postgres / pgvector")
dsn_password_known = bool(os.environ.get("DOCPULSE_PG_DSN")) or bool(os.environ.get("POSTGRES_PASSWORD"))
try:
    from docpulse.embed.config import EmbeddingSettings

    settings = EmbeddingSettings.from_env()
    print(f"  dsn (redacted): {settings.public()['dsn_host']}")
    print(f"  db target     : {settings.dsn.rsplit('/', 1)[-1]}")
    if not dsn_password_known:
        note(
            "neither POSTGRES_PASSWORD nor DOCPULSE_PG_DSN is set in this container, so the "
            "DSN falls back to the literal password 'postgres'. If the embedder cannot "
            "authenticate, set DOCPULSE_PG_DSN in .env explicitly."
        )
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
    if role == "core":
        note("psycopg not installed here — expected in airflow-scheduler; the embedder checks the database")
    else:
        blocker("psycopg is required in the embedder but is not importable")
except Exception as exc:
    msg = str(exc)
    if "does not exist" in msg and "database" in msg:
        note("database does not exist yet — expected before the first `make embed-db-init` "
             "(authentication itself worked)")
    else:
        blocker(f"postgres unreachable: {type(exc).__name__}: {exc}")

# --------------------------------------------------------------------------
say("disk")
try:
    for path in ("/", os.getcwd()):
        try:
            du = shutil.disk_usage(path)
            free_gb = du.free / 1e9
            mark = "PASS" if free_gb >= 2 else "BLOCK"
            print(f"  {mark}  {path:<18} {free_gb:.1f} GB free ({du.used / du.total * 100:.0f}% used)")
            if free_gb < 1:
                blocker(f"{path} has only {free_gb:.1f} GB free; a torch image needs ~2.5 GB")
            elif free_gb < 8:
                note(
                    f"{path} has {free_gb:.1f} GB free. Enough for the ~1.1 GB embedder image, "
                    "but run `docker builder prune -f` and `docker image prune` first if you can: "
                    "a build needs room for the layer plus the cache."
                )
        except Exception as exc:
            note(f"cannot stat {path}: {exc}")
except Exception:
    pass

# --------------------------------------------------------------------------
say("model cache")
hf_home = os.environ.get("HF_HOME", os.path.expanduser("~/.cache/huggingface"))
print(f"  HF_HOME = {hf_home}")
found = False
if os.path.isdir(hf_home):
    for root, dirs, files in os.walk(hf_home):
        if root[len(hf_home):].count(os.sep) > 3:
            dirs[:] = []
        if "config.json" in files or "model.safetensors" in files:
            print(f"  cached: {root}")
            found = True
if not found:
    note("model not cached yet — first `make embed` downloads ~90 MB (one off, kept in the "
         "embed-model-cache volume)")

# --------------------------------------------------------------------------
say("verdict")
if BLOCKERS:
    print(f"  {len(BLOCKERS)} blocker(s):")
    for b in BLOCKERS:
        print(f"    - {b}")
    print("")
    if role == "core" and any("psycopg" in b or "pgvector" in b for b in BLOCKERS):
        print("  Those are expected in airflow-scheduler and are checked again in the embedder.")
    else:
        print("  Do not run `make embed` yet. Paste this output back.")
    sys.exit(1)
print(f"  no blockers for role={role}" + (f"; {len(NOTES)} note(s):" if NOTES else ""))
for n in NOTES:
    print(f"    - {n}")
print("")
if role == "core":
    print("  Next: make -f day3.mk embed-build, then embed-preflight-embedder")
else:
    print("  Next: make -f day3.mk embed-db-init, then embed-dry")
