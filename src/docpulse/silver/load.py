"""Day 7: the load step — silver parquet into ``paper_meta``, and the gate after it.

Airflow-free and unit-testable, like :mod:`docpulse.silver.tasks`: the DAG wraps
these, the tests drive them with a fake cursor.

Why this module exists. Paper metadata used to reach Postgres through a one-off
script (``scripts/day4_load_meta.py``). Bronze and silver ran on a schedule; the hop
that makes a paper *searchable* did not, so a new announcement landed in silver and
stopped there. And because that script never wrote ``authors``, every reload quietly
produced papers that author search could not find — with every existing check green.

Two properties are deliberate:

* ``load_meta`` is an upsert and **never deletes**. Pruning stays a human decision in
  the one-off CLI. A scheduled DELETE keyed on "whatever is in silver right now" will
  one day run against a half-built silver, and ``paper_embeddings`` has no foreign key
  back to ``paper_meta`` to stop it.
* ``check_meta`` is a gate, not a report. It raises, because a number nobody fails on
  is a number nobody reads.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Callable

META_TABLE = "paper_meta"
DEFAULT_BATCH = 500

SCHEMA = """
CREATE TABLE IF NOT EXISTS {meta} (
    arxiv_id         text PRIMARY KEY,
    title            text,
    abstract         text,
    categories       text,
    primary_category text,
    published        timestamptz,
    updated          timestamptz,
    version          integer,
    text_hash        text,
    authors          text[],
    authors_ci       text[],
    authors_tsv      tsvector,
    loaded_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS {meta}_published_idx  ON {meta} (published);
CREATE INDEX IF NOT EXISTS {meta}_categories_idx ON {meta} (categories);
CREATE INDEX IF NOT EXISTS {meta}_authors_idx     ON {meta} USING GIN (authors);
CREATE INDEX IF NOT EXISTS {meta}_authors_ci_idx  ON {meta} USING GIN (authors_ci);
CREATE INDEX IF NOT EXISTS {meta}_authors_tsv_idx ON {meta} USING GIN (authors_tsv);
"""

# authors_tsv is built here, in SQL, with the same expression day6_add_authors.sql
# used: one definition of the tsvector, so a row loaded by the DAG and a row loaded
# by the one-off script are indistinguishable.
UPSERT = """
INSERT INTO {meta} (arxiv_id, title, abstract, categories, primary_category,
                    published, updated, version, text_hash,
                    authors, authors_ci, authors_tsv)
VALUES (%(arxiv_id)s, %(title)s, %(abstract)s, %(categories)s, %(primary_category)s,
        %(published)s::timestamptz, %(updated)s::timestamptz, %(version)s, %(text_hash)s,
        %(authors)s::text[], %(authors_ci)s::text[],
        to_tsvector('simple',
                    array_to_string(coalesce(%(authors)s::text[], ARRAY[]::text[]), ' ')))
ON CONFLICT (arxiv_id) DO UPDATE SET
       title = excluded.title,
       abstract = excluded.abstract,
       categories = excluded.categories,
       primary_category = excluded.primary_category,
       published = excluded.published,
       updated = excluded.updated,
       version = excluded.version,
       text_hash = excluded.text_hash,
       authors = excluded.authors,
       authors_ci = excluded.authors_ci,
       authors_tsv = excluded.authors_tsv,
       loaded_at = now()
"""

# NB: the empty-array literal is escaped as {{}} because this string goes through
# str.format(); a bare {} is read as a positional field and raises IndexError.
CHECK_SQL = """
SELECT count(*)                                                          AS papers,
       count(*) FILTER (WHERE authors IS NULL)                            AS null_authors,
       count(*) FILTER (WHERE authors = '{{}}')                           AS empty_authors,
       count(*) FILTER (WHERE authors IS NOT NULL AND authors_ci IS NULL) AS missing_ci,
       count(*) FILTER (WHERE authors IS NOT NULL AND authors_tsv IS NULL) AS missing_tsv,
       md5(string_agg(arxiv_id || '|' || coalesce(array_to_string(authors, ','), ''),
                      ';' ORDER BY arxiv_id))                            AS authors_fp
  FROM {meta}
"""


class MetaIntegrityError(RuntimeError):
    """paper_meta is in a state that would degrade search; fail the run."""


def _connect() -> Any:
    """Open a connection using the same settings the embedder already reads.

    Two drivers, one interface. The embedder image ships psycopg 3; the Airflow image
    ships psycopg2, because that is what its own metadata database needs. Both accept
    a DSN, %(name)s parameters and a list of dicts for executemany, so the fallback is
    a rename rather than a second code path.

    This exists because the first real DAG run failed here with ModuleNotFoundError
    after silver had already grown to 8,274 papers -- the load task was the first code
    in the project to run in the Airflow image *and* talk to the corpus database.
    """
    try:
        import psycopg
    except ModuleNotFoundError:  # the Airflow image: psycopg2 only
        import psycopg2 as psycopg

    from docpulse.embed.config import EmbeddingSettings

    return psycopg.connect(EmbeddingSettings.from_env().dsn, connect_timeout=10)


def read_silver_rows(*, store: Any = None) -> tuple[list[dict], dict[str, Any]]:
    """Every row silver holds, decoded, plus the facts worth logging.

    The store is injectable so this can be driven by a fake. ``compat`` owns the
    storage details (manifest location, month decoding, the key names silver uses);
    this function deliberately reimplements none of them.
    """
    from docpulse.embed import compat

    store = store if store is not None else compat.get_store()
    manifest_key = compat.find_silver_manifest_key(store)
    manifest = json.loads(store.get_bytes(manifest_key))
    months = manifest.get("months") or manifest.get("parts") or manifest.get("files") or []

    raw_rows: list[dict] = []
    for item in months:
        key = item if isinstance(item, str) else (item.get("key") or item.get("path"))
        if not key:
            continue
        decoded, _mode = compat._decode_month(store, key)
        raw_rows.extend(decoded)

    return raw_rows, {
        "manifest_key": manifest_key,
        "n_months": len(months),
        "n_rows_read": len(raw_rows),
    }


def _pick(row: dict, keys) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    return iso() if callable(iso) else str(value)


def normalise_categories(value: Any) -> str | None:
    """Store categories as a space-separated token list, whatever silver hands us.

    pyarrow gives a Python list, which ``str()`` would turn into the literal text
    "['cs.CL', 'cs.CV']". The search filter splits on spaces, so that text matches
    nothing and ``?category=cs.LG`` quietly returns zero rows.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            items = [str(v) for v in parsed] if isinstance(parsed, (list, tuple)) else text.split()
        elif "," in text and " " not in text:
            items = text.split(",")
        else:
            items = text.split()
    tokens: list[str] = []
    for item in items:
        token = item.strip().strip("'\"[](),")
        if token and token not in tokens:
            tokens.append(token)
    return " ".join(tokens) or None


def _authors(raw: dict) -> list[str]:
    """Silver's author list, cleaned the one way there is.

    pyarrow gives a list; a round trip through parquet or json can hand back its
    repr. Order is preserved and duplicates are kept — they exist in the source, and
    inventing a cleaner corpus than arXiv ships would be a quiet lie. ``clean_name``
    is the shared fold that keeps ``Herv{é}`` searchable as ``Hervé``.
    """
    from docpulse.search.authors import clean_name

    value = raw.get("authors")
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                parsed = None
            items = ([str(v) for v in parsed]
                     if isinstance(parsed, (list, tuple))
                     else text.split(","))
        elif "," in text:
            items = text.split(",")
        else:
            items = [text]

    names: list[str] = []
    for item in items:
        name = clean_name(item.strip().strip("'\""))
        if name:
            names.append(name)
    return names


def build_rows(
    raw_rows: list[dict],
    *,
    id_keys=("arxiv_id", "paper_id", "id"),
    version_keys=("version",),
    hash_keys=("text_hash", "content_hash", "text_sha256"),
) -> list[dict]:
    """Map silver's columns onto the ``paper_meta`` shape. Pure: easy to test."""
    from docpulse.search.authors import normalise

    out: dict[str, dict] = {}
    for raw in raw_rows:
        paper_id = _pick(raw, id_keys)
        if paper_id is None:
            continue
        try:
            version = int(_pick(raw, version_keys) or 1)
        except (TypeError, ValueError):
            version = 1
        authors = _authors(raw)
        row = {
            "arxiv_id": str(paper_id).strip(),
            "title": _text(raw.get("title")),
            "abstract": _text(raw.get("abstract")),
            "categories": normalise_categories(raw.get("categories")),
            "primary_category": _text(raw.get("primary_category")),
            "published": _iso(raw.get("published")),
            "updated": _iso(raw.get("updated")),
            "version": version,
            "text_hash": _text(_pick(raw, hash_keys)),
            "authors": authors,
            "authors_ci": [normalise(a) for a in authors],
        }
        # highest version wins, same rule as the embedding build
        current = out.get(row["arxiv_id"])
        if current is None or row["version"] >= current["version"]:
            out[row["arxiv_id"]] = row
    return sorted(out.values(), key=lambda r: r["arxiv_id"])


def build_rows_from_silver(raw_rows: list[dict]) -> list[dict]:
    """build_rows with silver's own key names, so callers need not know them."""
    from docpulse.embed import compat

    return build_rows(
        raw_rows,
        id_keys=compat.ID_KEYS,
        version_keys=compat.VERSION_KEYS,
        hash_keys=compat.HASH_KEYS,
    )


def load_meta(
    rows: list[dict],
    *,
    connect: Callable[[], Any] | None = None,
    batch: int = DEFAULT_BATCH,
    meta_table: str = META_TABLE,
) -> dict[str, Any]:
    """Upsert rows into paper_meta. Never deletes: see the module docstring."""
    connect = connect or _connect
    upsert = UPSERT.format(meta=meta_table)
    written = 0
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA.format(meta=meta_table))
            for start in range(0, len(rows), max(1, batch)):
                chunk = rows[start:start + max(1, batch)]
                cur.executemany(upsert, chunk)
                written += len(chunk)
        conn.commit()
    return {"n_written": written, "meta_table": meta_table}


def check_meta(
    *,
    connect: Callable[[], Any] | None = None,
    meta_table: str = META_TABLE,
    min_papers: int = 1,
) -> dict[str, Any]:
    """Fail the run if paper_meta would make author search lie."""
    connect = connect or _connect
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(CHECK_SQL.format(meta=meta_table))
            row = cur.fetchone()

    keys = ("papers", "null_authors", "empty_authors", "missing_ci", "missing_tsv", "authors_fp")
    report = dict(zip(keys, row, strict=True))

    problems = {
        "null_authors": report["null_authors"],
        "empty_authors": report["empty_authors"],
        "missing_ci": report["missing_ci"],
        "missing_tsv": report["missing_tsv"],
    }
    bad = {k: v for k, v in problems.items() if v}
    if report["papers"] < min_papers:
        raise MetaIntegrityError(
            f"{meta_table} holds {report['papers']} papers, expected at least {min_papers}"
        )
    if bad:
        raise MetaIntegrityError(
            f"{meta_table} is not loadable by author search: {bad} "
            f"(authors_fp={report['authors_fp']})"
        )
    return report
