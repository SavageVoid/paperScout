"""Day 4: the query-embedding cache."""

from __future__ import annotations

import unittest

from docpulse.embed.encoder import HashEncoder
from docpulse.search.query import QueryEncoder, normalise_query


class NormaliseTests(unittest.TestCase):
    def test_whitespace_is_collapsed(self):
        self.assertEqual(normalise_query("  sparse   attention \n"), "sparse attention")

    def test_empty_stays_empty(self):
        self.assertEqual(normalise_query("   \t "), "")


class CacheTests(unittest.TestCase):
    def _enc(self, cache_size=4):
        return QueryEncoder(HashEncoder(dim=8), cache_size=cache_size)

    def test_same_query_encodes_once(self):
        qe = self._enc()
        a = qe.encode("hello world")
        b = qe.encode("hello   world")
        self.assertEqual(a, b)
        self.assertEqual(qe.stats()["hits"], 1)
        self.assertEqual(qe.stats()["misses"], 1)

    def test_different_queries_miss(self):
        qe = self._enc()
        qe.encode("alpha")
        qe.encode("beta")
        self.assertEqual(qe.stats()["misses"], 2)
        self.assertEqual(qe.stats()["size"], 2)

    def test_capacity_evicts_oldest(self):
        qe = self._enc(cache_size=2)
        for word in ("one", "two", "three"):
            qe.encode(word)
        self.assertEqual(qe.stats()["size"], 2)
        qe.encode("one")  # evicted earlier, so this is a miss
        self.assertEqual(qe.stats()["misses"], 4)

    def test_cache_size_zero_always_encodes(self):
        qe = self._enc(cache_size=0)
        qe.encode("x")
        qe.encode("x")
        self.assertEqual((qe.stats()["hits"], qe.stats()["misses"]), (0, 2))
        self.assertEqual(qe.stats()["size"], 0)

    def test_a_different_model_never_reuses_a_vector(self):
        class OtherEncoder(HashEncoder):
            @property
            def fingerprint(self):
                return "different-fingerprint"

        qe = self._enc()
        qe.encode("same text")
        first = qe.stats()
        qe._encoder = OtherEncoder(dim=8)  # simulate a model swap
        qe.encode("same text")
        self.assertEqual(qe.stats()["misses"], first["misses"] + 1)

    def test_clear_resets_counters(self):
        qe = self._enc()
        qe.encode("x")
        qe.clear()
        self.assertEqual(qe.stats()["size"], 0)
        self.assertEqual(qe.stats()["misses"], 0)


if __name__ == "__main__":
    unittest.main()
