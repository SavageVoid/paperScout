"""Day 4: search settings sit on top of Day 3's, and must not drift from them."""

from __future__ import annotations

import unittest

from docpulse.search.config import DEFAULT_LIMIT, MAX_LIMIT, SearchSettings


class DefaultsTests(unittest.TestCase):
    def test_model_and_database_come_from_day3(self):
        s = SearchSettings.from_env({"POSTGRES_USER": "docpulse", "EMBED_DIM": "384"})
        self.assertEqual(s.dim, 384)
        self.assertTrue(s.dsn.endswith("/docpulse_embed"), s.dsn)
        self.assertEqual(s.table, "paper_embeddings")

    def test_defaults(self):
        s = SearchSettings.from_env({})
        self.assertEqual(s.port, 8080)
        self.assertEqual(s.default_limit, DEFAULT_LIMIT)
        self.assertEqual(s.max_limit, MAX_LIMIT)
        self.assertEqual(s.meta_table, "paper_meta")

    def test_env_overrides(self):
        s = SearchSettings.from_env(
            {"SEARCH_PORT": "9000", "SEARCH_DEFAULT_LIMIT": "3", "SEARCH_MAX_LIMIT": "7", "SEARCH_CACHE_SIZE": "0"}
        )
        self.assertEqual((s.port, s.default_limit, s.max_limit, s.cache_size), (9000, 3, 7, 0))

    def test_junk_falls_back_instead_of_crashing(self):
        s = SearchSettings.from_env({"SEARCH_PORT": "not-a-port", "SEARCH_DEFAULT_LIMIT": ""})
        self.assertEqual(s.port, 8080)
        self.assertEqual(s.default_limit, DEFAULT_LIMIT)

    def test_max_limit_cannot_be_below_default(self):
        s = SearchSettings.from_env({"SEARCH_DEFAULT_LIMIT": "50", "SEARCH_MAX_LIMIT": "5"})
        self.assertGreaterEqual(s.max_limit, s.default_limit)


class ClampTests(unittest.TestCase):
    def setUp(self):
        self.s = SearchSettings.from_env({"SEARCH_DEFAULT_LIMIT": "10", "SEARCH_MAX_LIMIT": "20"})

    def test_none_uses_default(self):
        self.assertEqual(self.s.clamp_limit(None), 10)

    def test_zero_and_negative_floor_at_one(self):
        self.assertEqual(self.s.clamp_limit(0), 1)
        self.assertEqual(self.s.clamp_limit(-5), 1)

    def test_above_max_is_capped(self):
        self.assertEqual(self.s.clamp_limit(10_000), 20)

    def test_garbage_uses_default(self):
        self.assertEqual(self.s.clamp_limit("abc"), 10)


class PublicTests(unittest.TestCase):
    def test_password_is_redacted(self):
        s = SearchSettings.from_env(
            {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "hunter2", "DOCPULSE_DB": "d"}
        )
        public = s.public()
        self.assertNotIn("hunter2", str(public))
        self.assertIn(":***@", public["dsn"])


if __name__ == "__main__":
    unittest.main()
