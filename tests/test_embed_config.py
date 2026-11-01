"""Day 3: configuration defaults, including the database-name trap."""

from __future__ import annotations

import unittest

from docpulse.embed.config import (
    DEFAULT_DIM,
    DEFAULT_DB,
    DEFAULT_MODEL_REVISION,
    EmbeddingSettings,
    dsn_from_env,
)


class DsnTests(unittest.TestCase):
    def test_default_database_is_not_the_airflow_one(self):
        # With POSTGRES_DB unset the postgres image names the db after POSTGRES_USER,
        # so on this stack 'docpulse' is very likely Airflow's metadata database.
        self.assertEqual(DEFAULT_DB, "docpulse_embed")
        dsn = dsn_from_env({"POSTGRES_USER": "docpulse", "POSTGRES_PASSWORD": "x"})
        self.assertTrue(dsn.endswith("/docpulse_embed"), dsn)

    def test_explicit_db_wins(self):
        dsn = dsn_from_env({"POSTGRES_USER": "u", "DOCPULSE_DB": "custom"})
        self.assertTrue(dsn.endswith("/custom"))

    def test_explicit_dsn_wins_over_everything(self):
        dsn = dsn_from_env({"DOCPULSE_PG_DSN": "postgresql://a@b:5/c", "POSTGRES_USER": "u"})
        self.assertEqual(dsn, "postgresql://a@b:5/c")

    def test_host_port_is_always_5432_inside_a_container(self):
        # POSTGRES_PORT is the *host* mapping; using it inside the container is a
        # classic silent timeout.
        dsn = dsn_from_env({"POSTGRES_USER": "u", "POSTGRES_PORT": "5433"})
        self.assertIn(":5432/", dsn)
        self.assertNotIn(":5433", dsn)

    def test_docpulse_pg_port_overrides(self):
        dsn = dsn_from_env({"POSTGRES_USER": "u", "DOCPULSE_PG_PORT": "6543"})
        self.assertIn(":6543/", dsn)

    def test_password_is_url_quoted(self):
        dsn = dsn_from_env({"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p@ss:word"})
        self.assertIn("p%40ss%3Aword", dsn)

    def test_redaction_hides_the_password(self):
        public = EmbeddingSettings.from_env({"POSTGRES_PASSWORD": "secret"}).public()
        self.assertNotIn("secret", str(public))


class SettingsTests(unittest.TestCase):
    def test_defaults_are_pinned(self):
        s = EmbeddingSettings.from_env({})
        self.assertEqual(s.dim, DEFAULT_DIM)
        self.assertEqual(s.model_revision, DEFAULT_MODEL_REVISION)
        self.assertEqual(len(s.model_revision), 40)  # a real commit sha, not a tag
        self.assertTrue(s.normalize)
        self.assertFalse(s.allow_model_change)
        self.assertFalse(s.prune_orphans)

    def test_batch_size_cannot_be_zero(self):
        self.assertGreaterEqual(EmbeddingSettings.from_env({"EMBED_BATCH_SIZE": "0"}).batch_size, 1)

    def test_bad_values_fall_back(self):
        s = EmbeddingSettings.from_env({"EMBED_DIM": "not-a-number", "EMBED_BATCH_SIZE": ""})
        self.assertEqual(s.dim, DEFAULT_DIM)
        self.assertEqual(s.batch_size, 32)


if __name__ == "__main__":
    unittest.main()
