#!/usr/bin/env bash
#
# Embed papers that were loaded into Postgres but never vectorised.
#
# The Airflow DAG ends at the integrity gate on purpose: the Airflow image has no
# torch and build_encoder() refuses to write meaningless vectors. That leaves one
# human step -- `make embed` -- between "a paper is in paper_meta" and "a paper is
# searchable by vector". This script removes it.
#
# It asks the database the only question that matters: are there more rows in
# paper_meta than in paper_embeddings? If not, it exits in under a second and costs
# nothing. If yes, it runs the embedder in the image that owns the model.
#
# Exit codes:
#   0  nothing to do, or embedding finished cleanly
#   1  the embedder failed, or reported rows it could not embed
#   2  row counts could not be read (database or driver problem)
#   3  the embedder ran but its counters could not be parsed
#   4  the table holds vectors from a different model than the encoder
#
# Designed for cron: no aliases, no interactive prompts, one run at a time.

set -uo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$REPO_ROOT" || exit 2

# `dc` is a shell alias and aliases do not exist in cron, so the chain is spelled out.
COMPOSE_CMD=${DOCPULSE_COMPOSE:-"docker compose -f docker-compose.yml -f docker-compose.day3.yml -f docker-compose.day4.yml"}
read -r -a COMPOSE <<< "$COMPOSE_CMD"

LOG=${DOCPULSE_EMBED_LOG:-$REPO_ROOT/logs/embed_new.log}
mkdir -p "$(dirname -- "$LOG")" || exit 2

LOCK=${TMPDIR:-/tmp}/docpulse-embed.lock

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" | tee -a "$LOG"; }

# A 300 s build must never overlap the next tick.
exec 9>"$LOCK" || exit 2
if ! flock -n 9; then
    log "skip: another run holds $LOCK"
    exit 0
fi

# A model change is a decision for a human, not for cron. Row counts cannot see one:
# during the MiniLM -> bge swap this script no-opped because 8,937 == 8,937 while every
# vector was stale. /health knows, so ask it and stop rather than lie.
HEALTH_URL=${DOCPULSE_HEALTH_URL:-http://localhost:8090/health}
HEALTH_BODY=$(curl -sS --max-time 10 "$HEALTH_URL" 2>/dev/null || true)

if [ -z "$HEALTH_BODY" ]; then
    log "warning: $HEALTH_URL unreachable; falling back to row counts alone"
elif printf '%s' "$HEALTH_BODY" | grep -Eq '"model_matches"[[:space:]]*:[[:space:]]*false'; then
    log "error: stored vectors are from a different model than the encoder;"
    log "error: this needs a deliberate re-embed (EMBED_ALLOW_MODEL_CHANGE=1), not a cron job"
    exit 4
fi

COUNTS=$("${COMPOSE[@]}" run --rm --entrypoint python airflow-scheduler -c '
from docpulse.silver.load import _connect

conn = _connect()
cur = conn.cursor()
cur.execute(
    "select (select count(*) from paper_meta), (select count(*) from paper_embeddings)"
)
papers, vectors = cur.fetchone()
print(f"COUNTS {papers} {vectors}")
' 2>&1)

read -r _ papers vectors <<< "$(printf '%s\n' "$COUNTS" | grep -E '^COUNTS ' | tail -1)"

if [ -z "${papers:-}" ] || [ -z "${vectors:-}" ]; then
    log "error: could not read row counts from the database"
    printf '%s\n' "$COUNTS" | tail -5 >> "$LOG"
    exit 2
fi

log "papers=$papers vectors=$vectors"

if [ "$papers" -le "$vectors" ]; then
    log "nothing new: every paper already has a vector"
    exit 0
fi

log "up to $((papers - vectors)) rows without a vector; embedding"

OUT=$("${COMPOSE[@]}" run --rm searchapi python -m docpulse.embed.cli build 2>&1)
rc=$?
printf '%s\n' "$OUT" >> "$LOG"

if [ "$rc" -ne 0 ]; then
    log "error: embedder exited $rc"
    exit 1
fi

failed=$(printf '%s\n' "$OUT" | grep -Eo 'n_failed[^0-9]*[0-9]+' | grep -Eo '[0-9]+$' | tail -1)
embedded=$(printf '%s\n' "$OUT" | grep -Eo 'n_embedded[^0-9]*[0-9]+' | grep -Eo '[0-9]+$' | tail -1)

if [ -z "${failed:-}" ]; then
    log "warning: embedder succeeded but n_failed was not in its output; read the log above"
    exit 3
fi

log "n_embedded=${embedded:-?} n_failed=$failed"

if [ "$failed" -ne 0 ]; then
    log "error: $failed rows failed to embed"
    exit 1
fi

log "ok"
exit 0
