import tempfile
import unittest
from pathlib import Path

from docpulse.storage import LocalStore, ObjectNotFound, S3Store, StorageError
from fakes import FakeClientError, FakeS3Client


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)

    def test_roundtrip_and_list(self):
        self.store.put_bytes("a/b/1.txt", b"one")
        self.store.put_bytes("a/b/2.txt", b"two")
        self.store.put_bytes("a/c/3.txt", b"three")
        self.assertEqual(self.store.get_bytes("a/b/1.txt"), b"one")
        self.assertEqual(self.store.list_keys("a/b/"), ["a/b/1.txt", "a/b/2.txt"])

    def test_overwrite(self):
        self.store.put_bytes("k", b"old")
        self.store.put_bytes("k", b"new")
        self.assertEqual(self.store.get_bytes("k"), b"new")

    def test_missing_raises_object_not_found(self):
        with self.assertRaises(ObjectNotFound):
            self.store.get_bytes("nope")

    def test_rejects_path_traversal_and_bad_keys(self):
        for bad in ("../x", "/abs", "a/../../x", "a//b", "", "a\\b"):
            with self.assertRaises(ValueError, msg=bad):
                self.store.put_bytes(bad, b"x")

    def test_delete_prefix_requires_trailing_slash_and_is_exact(self):
        self.store.put_bytes("p/ingest_date=2026-10-2/x", b"1")
        self.store.put_bytes("p/ingest_date=2026-10-27/x", b"2")
        with self.assertRaises(ValueError):
            self.store.delete_prefix("p/ingest_date=2026-10-2")  # would also match ...-27
        self.assertEqual(self.store.delete_prefix("p/ingest_date=2026-10-2/"), 1)
        self.assertEqual(self.store.list_keys("p/"), ["p/ingest_date=2026-10-27/x"])

    def test_delete_prefix_prunes_empty_dirs(self):
        self.store.put_bytes("p/d/x", b"1")
        self.store.delete_prefix("p/d/")
        self.assertFalse((Path(self._tmp.name) / "p" / "d").exists())


class S3StoreTests(unittest.TestCase):
    def setUp(self):
        self.client = FakeS3Client(page_size=2)
        self.store = S3Store("bkt", self.client, delete_batch=3)

    def test_roundtrip_missing_and_paginated_list(self):
        for i in range(5):
            self.store.put_bytes(f"p/{i}", str(i).encode())
        self.assertEqual(self.store.get_bytes("p/3"), b"3")
        self.assertEqual(self.store.list_keys("p/"), [f"p/{i}" for i in range(5)])
        with self.assertRaises(ObjectNotFound):
            self.store.get_bytes("p/missing")

    def test_delete_prefix_batches_and_is_exact(self):
        for i in range(7):
            self.store.put_bytes(f"p/d/{i}", b"x")
        self.store.put_bytes("p/d2/keep", b"x")
        self.assertEqual(self.store.delete_prefix("p/d/"), 7)
        self.assertEqual(self.client.delete_calls, [3, 3, 1])
        self.assertEqual(self.store.list_keys("p/"), ["p/d2/keep"])

    def test_ensure_bucket_creates_when_missing_then_is_noop(self):
        self.assertTrue(self.store.ensure_bucket())
        self.assertFalse(self.store.ensure_bucket())

    def test_ensure_bucket_retries_while_store_is_starting(self):
        self.client.head_failures = [ConnectionError("not up"), ConnectionError("not up")]
        slept = []
        self.assertTrue(self.store.ensure_bucket(attempts=5, delay_s=1, sleep=slept.append))
        self.assertEqual(slept, [1, 1])

    def test_ensure_bucket_gives_up(self):
        self.client.head_failures = [FakeClientError("403")] * 3
        with self.assertRaises(StorageError):
            self.store.ensure_bucket(attempts=3, delay_s=0, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
