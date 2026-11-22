# Day 3 (embeddings) make targets.
#
# Use either way:
#   make -f day3.mk embed          # standalone
#   echo '-include day3.mk' >> Makefile && make embed    # merged with yours
#
# Nothing here shadows a Day 1/2 target: init, test, smoke, up, ps, logs, down,
# reset, backfill, trigger, digest, silver, silver-dry are all left alone.

COMPOSE           ?= docker compose
DAY3_OVERLAY      ?= docker-compose.day3.yml
COMPOSE_DAY3      := $(COMPOSE) -f docker-compose.yml -f $(DAY3_OVERLAY)
EMBED_RUN         := $(COMPOSE_DAY3) run --rm
EMBED_ARGS        ?=

.PHONY: embed-help embed-build embed-preflight embed-preflight-embedder \
        embed-db-init embed-dry embed embed-rebuild embed-verify embed-status \
        embed-shell embed-test embed-test-db embed-logs embed-audit-hash embed-lint

embed-help:
	@echo "Day 3 targets:"
	@echo "  embed-build              build the embedder image (once, ~1.1 GB)"
	@echo "  embed-preflight          read-only assumption check inside airflow-scheduler"
	@echo "  embed-preflight-embedder same check inside the embedder (has torch)"
	@echo "  embed-db-init            create docpulse db + pgvector extension + tables"
	@echo "  embed-dry                plan only, writes nothing"
	@echo "  embed                    embed new/changed papers (idempotent, resumable)"
	@echo "  embed-rebuild            drop this model's vectors and re-embed all"
	@echo "  embed-verify             independent verification against silver"
	@echo "  embed-status             row counts, model registry, run ledger"
	@echo "  embed-shell              bash inside the embedder"
	@echo "  embed-test               Day 3 unit tests (hash encoder, no torch needed)"
	@echo "  embed-test-db            Day 3 Postgres tests (uses docpulse_embed_test)"
	@echo "  embed-audit-hash         what does silver's text_hash actually cover? (B4)"
	@echo "  embed-lint               ruff over the Day 3 code (needs ruff on the host)"
	@echo ""
	@echo "Pass extra CLI flags with EMBED_ARGS, e.g. make embed EMBED_ARGS='--limit 50'"

embed-build:
	$(COMPOSE_DAY3) build embedder

# Runs where pyarrow/boto3 live but torch does not: straight into the scheduler.
embed-preflight:
	@$(COMPOSE) exec -T airflow-scheduler python - < scripts/day3_preflight.py

embed-preflight-embedder:
	@$(EMBED_RUN) --entrypoint python embedder - < scripts/day3_preflight.py

embed-db-init:
	$(EMBED_RUN) embedder python /opt/docpulse/scripts/day3_db_init.py

embed-dry:
	$(EMBED_RUN) embedder python -m docpulse.embed.cli build --dry-run $(EMBED_ARGS)

embed:
	$(EMBED_RUN) embedder python -m docpulse.embed.cli build $(EMBED_ARGS)

embed-rebuild:
	$(EMBED_RUN) embedder python -m docpulse.embed.cli build --rebuild $(EMBED_ARGS)

embed-verify:
	$(EMBED_RUN) embedder python -m docpulse.embed.cli verify $(EMBED_ARGS)

embed-status:
	$(EMBED_RUN) embedder python -m docpulse.embed.cli status

embed-shell:
	$(EMBED_RUN) embedder bash

embed-logs:
	$(COMPOSE_DAY3) logs --tail 50 embedder

# Host-side: no docker, no database. Proves the planning/verification logic.
embed-test:
	PYTHONPATH=src:tests python3 -m pytest -q tests -k embed

# Container-side: real Postgres, real pgvector. Uses the docpulse database, so
# point DOCPULSE_TEST_PG_DSN at a scratch database if you care about the data.
embed-test-db:
	# DOCPULSE_TEST_DB=1 tells the tests to build their own DSN against
	# <db>_test and create it; nothing on the host needs to be exported.
	$(EMBED_RUN) -e DOCPULSE_TEST_DB=1 embedder \
		python -m pytest -q -p no:cacheprovider /opt/docpulse/tests/test_embed_db.py

# What does silver's text_hash actually cover? Read-only; recovers the recipe by
# testing plausible constructions against the real rows. Exit 0 either way - a
# finding is not a pipeline failure.
embed-audit-hash:
	$(EMBED_RUN) embedder python /opt/docpulse/scripts/day3_hash_audit.py

# Runs on the host, not in a container: this is a coding aid, not a pipeline step.
embed-lint:
	@command -v ruff >/dev/null 2>&1 || { echo "ruff not installed — pip install ruff"; exit 2; }
	ruff check src/docpulse/embed scripts tests
