.PHONY: init up down reset logs ps test lint fmt smoke trigger backfill

COMPOSE ?= docker compose
DATE ?= $(shell python3 -c "from datetime import date,timedelta;print(date.today()-timedelta(days=1))")

.env:
	cp .env.example .env
	fernet=$$(openssl rand -base64 32 | tr '+/' '-_'); secret=$$(openssl rand -hex 32); \
	  sed -i.bak "s|^AIRFLOW_FERNET_KEY=.*|AIRFLOW_FERNET_KEY=$$fernet|; s|^AIRFLOW_SECRET_KEY=.*|AIRFLOW_SECRET_KEY=$$secret|" .env && rm -f .env.bak
	echo "Created .env. Edit ARXIV_CONTACT_EMAIL before the first run."

init: .env  ## create .env with generated secrets

up: .env  ## build and start the stack (Airflow UI on http://localhost:8080)
	$(COMPOSE) up -d --build

down:  ## stop the stack, keep data
	$(COMPOSE) down

reset:  ## stop the stack and DELETE all volumes (db, object store, logs)
	$(COMPOSE) down -v

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f --tail=100

trigger:  ## run the bronze DAG once now (ingests the most recent complete day)
	$(COMPOSE) exec airflow-scheduler airflow dags trigger arxiv_bronze_ingest

backfill:  ## (re)run bronze for ONE day: make backfill DATE=2026-10-26
	$(COMPOSE) exec airflow-scheduler airflow dags backfill arxiv_bronze_ingest -s $(DATE) -e $(DATE) --reset-dagruns -y

test:  ## unit tests (pytest if installed, else unittest)
	PYTHONPATH=src:tests python3 -m pytest -q 2>/dev/null || PYTHONPATH=src:tests python3 -m unittest discover -s tests

lint:
	ruff check . && ruff format --check .

fmt:
	ruff check --fix . && ruff format .

smoke:  ## real arXiv ingest of one day, no Docker (about a minute, needs network)
	set -a; . ./.env; set +a; PYTHONPATH=src python3 -m docpulse.ingest.cli --store local --ingest-date $(DATE) --max-pages 50
