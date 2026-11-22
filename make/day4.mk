# Day 4 (search API) make targets.
#
#   make -f day4.mk search-build     # once: adds fastapi/uvicorn to the embedder image
#   make -f day4.mk search-meta      # load titles/abstracts into postgres
#   make -f day4.mk search-up        # start on http://localhost:8080
#
# Nothing here shadows a Day 1/2/3 target.

COMPOSE           ?= docker compose
DAY3_OVERLAY      ?= docker-compose.day3.yml
DAY4_OVERLAY      ?= docker-compose.day4.yml
COMPOSE_SEARCH    := $(COMPOSE) -f docker-compose.yml -f $(DAY3_OVERLAY) -f $(DAY4_OVERLAY)
SEARCH_RUN        := $(COMPOSE_SEARCH) run --rm
SEARCH_PUBLISH    ?= 8090
SEARCH_URL        := http://localhost:$(SEARCH_PUBLISH)
SEARCH_META_ARGS  ?=
SEARCH_PSQL_DB    ?= docpulse_embed
SEARCH_PSQL_USER  ?= docpulse

.PHONY: search-help search-build search-meta search-up search-down search-logs \
        search-test search-smoke search-index search-shell search-stats

search-help:
	@echo "Day 4 targets:"
	@echo "  search-build             rebuild the embedder image with fastapi/uvicorn (once)"
	@echo "  search-meta              load paper titles/abstracts into postgres"
	@echo "  search-up                start the API (http://localhost:8080)"
	@echo "  search-down              stop it"
	@echo "  search-logs              follow container logs"
	@echo "  search-test              Day 4 tests inside the container"
	@echo "  search-smoke             curl /health and one real query"
	@echo "  search-stats             curl /stats"
	@echo "  search-index             opt-in: create the HNSW index (only pays off >100k rows)"
	@echo "  search-shell             bash inside the API container"
	@echo ""
	@echo "Pass flags through, e.g. make -f day4.mk search-meta SEARCH_META_ARGS='--prune'"

# The API needs fastapi+uvicorn; they are pinned in requirements-embed.txt, so the
# shared image gets rebuilt once. Layers below the pip step stay cached.
search-build:
	$(COMPOSE_SEARCH) build embedder

search-meta:
	$(SEARCH_RUN) searchapi python /opt/docpulse/scripts/day4_load_meta.py $(SEARCH_META_ARGS)

search-up:
	SEARCH_PUBLISH=$(SEARCH_PUBLISH) $(COMPOSE_SEARCH) up -d searchapi
	@printf "waiting for the API"
	@i=0; while :; do \
	  code=$$(curl -sS -o /dev/null -m 2 -w '%{http_code}' $(SEARCH_URL)/health 2>/dev/null || echo 000); \
	  case "$$code" in 200|503) break;; esac; \
	  i=$$((i+1)); \
	  if [ $$i -gt 60 ]; then \
	    echo ""; \
	    echo "  no answer after 60s — the model may still be loading, or it crashed:"; \
	    echo "  make -f day4.mk search-logs"; \
	    exit 1; \
	  fi; \
	  printf "."; sleep 1; \
	done; \
	echo " ready (HTTP $$code)"
	@echo ""
	@echo "  open $(SEARCH_URL)/"
	@echo "  health: $(SEARCH_URL)/health"
	@echo ""
	@echo "  (8080 is Airflow's webserver on this stack; override with SEARCH_PUBLISH=####)"

search-down:
	$(COMPOSE_SEARCH) stop searchapi

search-logs:
	$(COMPOSE_SEARCH) logs -f --tail=100 searchapi

search-test:
	$(SEARCH_RUN) searchapi python -m pytest -q -p no:cacheprovider \
		/opt/docpulse/tests/test_search_config.py \
		/opt/docpulse/tests/test_search_query.py \
		/opt/docpulse/tests/test_search_api.py

search-smoke:
	@curl -fsS $(SEARCH_URL)/health -o /tmp/day4_health.json \
		|| { echo "no response on $(SEARCH_URL) — is it up? (make -f day4.mk search-up)"; exit 1; }
	@python3 -c "import json,sys; d=json.load(open('/tmp/day4_health.json')); \
	  sys.exit(0) if 'n_vectors' in d else sys.exit(1)" \
		|| { echo "something else is answering on $(SEARCH_URL) — not the search API."; \
		     echo "Port taken? try: SEARCH_PUBLISH=8091 make -f day4.mk search-up"; exit 1; }
	@python3 -m json.tool /tmp/day4_health.json
	@echo ""
	@curl -fsS "$(SEARCH_URL)/search?q=sparse+attention&limit=3" | python3 -m json.tool | head -40

search-stats:
	@curl -fsS $(SEARCH_URL)/stats | python3 -m json.tool

# Sequential scan is the right choice at a few thousand rows. Past ~100k, the
# index starts paying for itself; measure before enabling it.
search-index:
	$(COMPOSE) exec -T postgres psql -U $(SEARCH_PSQL_USER) -d $(SEARCH_PSQL_DB) -c \
		"CREATE INDEX IF NOT EXISTS paper_embeddings_hnsw \
		 ON paper_embeddings USING hnsw (embedding vector_cosine_ops);"
	$(COMPOSE) exec -T postgres psql -U $(SEARCH_PSQL_USER) -d $(SEARCH_PSQL_DB) -c \
		"ANALYZE paper_embeddings;"

search-shell:
	$(SEARCH_RUN) searchapi bash
