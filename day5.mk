# Day 5 targets. Step 1 is a read-only probe: measure before building.

COMPOSE        ?= docker compose
DAY3_OVERLAY   ?= docker-compose.day3.yml
DAY4_OVERLAY   ?= docker-compose.day4.yml
COMPOSE_DAY5   := $(COMPOSE) -f docker-compose.yml -f $(DAY3_OVERLAY) -f $(DAY4_OVERLAY)
DAY5_RUN       := $(COMPOSE_DAY5) run --rm
PROBE_ARGS     ?=

.PHONY: day5-help fts-probe fts-explain

day5-help:
	@echo "Day 5 targets:"
	@echo "  fts-probe                vector vs keyword retrieval, side by side (read-only)"
	@echo "  fts-explain              print the SQL the probe runs, touch nothing"
	@echo ""
	@echo "  pass queries:  make -f day5.mk fts-probe PROBE_ARGS='--queries \"Yann LeCun\" BERT'"

# Runs in the searchapi image: it needs torch to encode the query, and psycopg to
# read the tables. Nothing is written.
fts-probe:
	$(DAY5_RUN) searchapi python /opt/docpulse/scripts/day5_fts_probe.py $(PROBE_ARGS)

fts-explain:
	$(DAY5_RUN) searchapi python /opt/docpulse/scripts/day5_fts_probe.py --explain
