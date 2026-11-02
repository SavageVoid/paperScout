"""Day 4: the API's contract, exercised without a database or a model."""

from __future__ import annotations

import unittest

try:  # the API tests need fastapi + httpx, which live in the search image
    from fastapi.testclient import TestClient
except ModuleNotFoundError:  # pragma: no cover - host venv without the API deps
    raise unittest.SkipTest(
        "fastapi/httpx not installed — pip install -r requirements-search.txt"
    ) from None

from _day4_fakes import FakeSearchDb
from docpulse.embed.encoder import HashEncoder
from docpulse.search.api import create_app
from docpulse.search.config import SearchSettings


def settings(**kw) -> SearchSettings:
    from docpulse.embed.config import EmbeddingSettings

    base = dict(
        embedding=EmbeddingSettings(dsn="postgresql://u@h/db", dim=8),
        default_limit=2,
        max_limit=5,
        cache_size=8,
    )
    base.update(kw)
    return SearchSettings(**base)


def client(db=None, *, cfg=None, encoder=None) -> TestClient:
    db = FakeSearchDb() if db is None else db
    encoder = HashEncoder(dim=8) if encoder is None else encoder
    return TestClient(create_app(settings=cfg or settings(), db=db, encoder=encoder))


class HealthTests(unittest.TestCase):
    def test_healthy_service_is_200(self):
        res = client().get("/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["db"], "up")
        self.assertEqual(body["n_vectors"], 3)
        self.assertIn("query_cache", body)

    def test_empty_table_is_503_with_a_hint(self):
        res = client(FakeSearchDb(n_vectors=0)).get("/health")
        self.assertEqual(res.status_code, 503)
        body = res.json()
        self.assertFalse(body["ok"])
        self.assertIn("embed", body["hint"])

    def test_model_mismatch_is_503_and_says_which(self):
        db = FakeSearchDb(model_fps=["someone-elses-fingerprint"])
        res = client(db).get("/health")
        self.assertEqual(res.status_code, 503)
        self.assertIn("fingerprint", res.json()["hint"])

    def test_missing_metadata_is_reported(self):
        res = client(FakeSearchDb(metadata_loaded=False)).get("/health")
        self.assertEqual(res.status_code, 503)
        self.assertIn("search-meta", res.json()["hint"])


class SearchTests(unittest.TestCase):
    def test_search_returns_the_documented_shape(self):
        res = client().get("/search", params={"q": "sparse attention"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["query"], "sparse attention")
        self.assertEqual(body["n_returned"], 2)  # default_limit
        self.assertEqual(body["limit"], 2)
        self.assertIn("fingerprint", body["model"])
        self.assertEqual(body["model"]["dim"], 8)
        first = body["results"][0]
        for key in ("arxiv_id", "title", "score", "published", "categories", "version"):
            self.assertIn(key, first)
        self.assertNotIn("abstract", first)  # only on request
        self.assertIsInstance(first["score"], float)
        self.assertTrue(first["published"].startswith("2026-01-01"))

    def test_abstract_only_when_asked(self):
        body = client().get(
            "/search", params={"q": "x", "include_abstract": "true"}
        ).json()
        self.assertIn("abstract", body["results"][0])

    def test_limit_is_clamped_to_max(self):
        body = client().get("/search", params={"q": "x", "limit": 1000}).json()
        self.assertEqual(body["limit"], 5)
        self.assertEqual(body["limit_requested"], 1000)

    def test_filters_reach_the_database(self):
        db = FakeSearchDb()
        client(db).get(
            "/search", params={"q": "x", "category": "cs.LG", "since": "2024-01-01"}
        )
        call = db.calls[-1]
        self.assertEqual(call["category"], "cs.LG")
        self.assertEqual(call["since"], "2024-01-01")
        self.assertIsNone(call["until"])
        self.assertEqual(call["limit"], 2)

    def test_blank_query_is_rejected(self):
        res = client().get("/search", params={"q": "   "})
        self.assertEqual(res.status_code, 422)

    def test_missing_query_is_rejected(self):
        self.assertEqual(client().get("/search").status_code, 422)

    def test_database_failure_is_503_not_a_traceback(self):
        db = FakeSearchDb(search_error=RuntimeError("connection refused"))
        res = client(db).get("/search", params={"q": "x"})
        self.assertEqual(res.status_code, 503)
        self.assertIn("connection refused", res.json()["detail"])

    def test_repeated_query_hits_the_encoder_once(self):
        enc = HashEncoder(dim=8)
        calls = {"n": 0}
        original = enc.encode

        def counting(texts):
            calls["n"] += 1
            return original(texts)

        enc.encode = counting
        c = client(encoder=enc)
        c.get("/search", params={"q": "same query"})
        c.get("/search", params={"q": "same   query"})  # whitespace-normalised to the same key
        self.assertEqual(calls["n"], 1)


class PaperAndStatsTests(unittest.TestCase):
    def test_paper_found(self):
        res = client().get("/papers/2601.00000")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["arxiv_id"], "2601.00000")
        self.assertTrue(body["has_vector"])

    def test_paper_missing_is_404(self):
        self.assertEqual(client().get("/papers/9999.99999").status_code, 404)

    def test_stats_exposes_settings_without_the_password(self):
        from docpulse.embed.config import EmbeddingSettings

        cfg = settings(
            embedding=EmbeddingSettings(dsn="postgresql://u:hunter2@h/db", dim=8)
        )
        res = client(cfg=cfg).get("/stats")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["n_rows"], 3)
        self.assertIn("query_cache", body)
        self.assertNotIn("hunter2", str(body))
        self.assertIn(":***@", body["settings"]["dsn"])


class PageTests(unittest.TestCase):
    def test_index_page_is_served(self):
        res = client().get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("DocPulse search", res.text)
        self.assertIn("/search?", res.text)
        # self-contained: no external scripts, so it works behind any proxy
        self.assertNotIn("http://", res.text.replace("http://www.w3.org", ""))


if __name__ == "__main__":
    unittest.main()
