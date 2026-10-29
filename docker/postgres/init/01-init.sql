-- Runs once, on first start of an empty data volume (use `make reset` to re-run).
-- POSTGRES_DB (docpulse) is the warehouse; Airflow keeps its metadata in its own database.
CREATE DATABASE airflow;

\connect docpulse
CREATE EXTENSION IF NOT EXISTS vector;
