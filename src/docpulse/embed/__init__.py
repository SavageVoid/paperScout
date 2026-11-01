"""DocPulse embedding layer (Day 3).

Hash-based incremental embeddings of silver papers into Postgres + pgvector.

Design rules this package follows
---------------------------------
1. Nothing here is imported at Airflow-parse time that needs torch or psycopg.
   Both are imported lazily, inside the functions that use them, so the DAG and
   the airflow image stay exactly as they are.
2. Change detection uses the silver ``text_hash`` that Day 2 already computes.
   A paper is re-embedded only when its text changed or the model changed.
3. Every batch is committed on its own. A run killed mid-flight resumes from the
   database, not from memory, so ``make embed`` is safe to re-run at any time.
4. A model change (different id, revision, dim or normalisation) is refused
   unless explicitly requested, because mixing two vector spaces in one column
   silently ruins every similarity query built on top of it.
"""

__all__ = [
    "config",
    "compat",
    "encoder",
    "db",
    "plan",
    "build",
    "verify",
    "tasks",
    "cli",
]
