"""Day 3: encoder contract."""

from __future__ import annotations

import math
import unittest

from docpulse.embed.config import EmbeddingSettings
from docpulse.embed.encoder import (
    HashEncoder,
    SentenceTransformerEncoder,
    _fingerprint,
    build_encoder,
)


class HashEncoderTests(unittest.TestCase):
    def test_dimension_and_unit_norm(self):
        enc = HashEncoder(dim=64)
        vec = enc.encode(["hello world"])[0]
        self.assertEqual(len(vec), 64)
        self.assertAlmostEqual(math.sqrt(sum(v * v for v in vec)), 1.0, places=6)

    def test_deterministic(self):
        enc = HashEncoder(dim=32)
        self.assertEqual(enc.encode(["same text"]), enc.encode(["same text"]))

    def test_different_text_different_vector(self):
        enc = HashEncoder(dim=32)
        a, b = enc.encode(["paper one"]), enc.encode(["paper two"])
        self.assertNotEqual(a, b)

    def test_batch_matches_single(self):
        enc = HashEncoder(dim=24)
        batch = enc.encode(["a", "b", "c"])
        self.assertEqual(len(batch), 3)
        self.assertEqual(batch[1], enc.encode(["b"])[0])

    def test_no_non_finite_values(self):
        enc = HashEncoder(dim=384)
        for text in ("", "x", "émoji 🚀 unicode", "a" * 5000):
            for v in enc.encode([text])[0]:
                self.assertTrue(math.isfinite(v))


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_changes_with_every_semantic_input(self):
        base = _fingerprint("m", "rev", 384, True, "t1")
        self.assertEqual(base, _fingerprint("m", "rev", 384, True, "t1"))
        self.assertNotEqual(base, _fingerprint("m2", "rev", 384, True, "t1"))
        self.assertNotEqual(base, _fingerprint("m", "rev2", 384, True, "t1"))
        self.assertNotEqual(base, _fingerprint("m", "rev", 768, True, "t1"))
        self.assertNotEqual(base, _fingerprint("m", "rev", 384, False, "t1"))
        self.assertNotEqual(base, _fingerprint("m", "rev", 384, True, "t2"))

    def test_hash_encoder_fingerprint_tracks_parameters(self):
        self.assertEqual(HashEncoder(dim=16).fingerprint, HashEncoder(dim=16).fingerprint)
        self.assertNotEqual(HashEncoder(dim=16).fingerprint, HashEncoder(dim=32).fingerprint)


class BuildEncoderTests(unittest.TestCase):
    def _settings(self, **kw):
        base = dict(dsn="postgresql://u@h/db", dim=32, batch_size=4)
        base.update(kw)
        return EmbeddingSettings(**base)

    def test_explicit_hash(self):
        enc = build_encoder(self._settings(encoder="hash"))
        self.assertIsInstance(enc, HashEncoder)
        self.assertEqual(enc.dim, 32)

    def test_auto_picks_sentence_transformers_when_available(self):
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            with self.assertRaises(RuntimeError) as ctx:
                build_encoder(self._settings(encoder="auto"))
            # the error must tell the operator what to do, not just fail
            self.assertIn("embedder", str(ctx.exception))
        else:
            enc = build_encoder(self._settings(encoder="auto"))
            self.assertIsInstance(enc, SentenceTransformerEncoder)
            # lazy: no model download at construction time
            self.assertIsNone(enc._model)

    def test_unknown_encoder_is_rejected(self):
        with self.assertRaises(ValueError):
            build_encoder(self._settings(encoder="magic"))


if __name__ == "__main__":
    unittest.main()
