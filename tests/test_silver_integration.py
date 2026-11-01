import importlib.util
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from docpulse.config import ArxivSettings
from docpulse.ingest.tasks import run_bronze_ingest
from docpulse.silver.build import build_silver, paper_fields, papers_key, verify_silver
from docpulse.silver.codecs import Field, JsonCodec, ParquetCodec
from docpulse.silver.contract import load_contract
from docpulse.silver.tasks import default_contract_path, run_build_silver, run_verify_silver, summarize
from docpulse.storage import LocalStore
from fakes import FakeArxiv, make_dataset
from silver_fakes import n_papers, rich_entry, write_bronze

UTC = timezone.utc
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "silver_papers.yml"
HAVE_PYARROW = importlib.util.find_spec("pyarrow") is not None


class BronzeToSilverTests(unittest.TestCase):
    """Bronze written by the REAL Day 1 ingest code, consumed by silver."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)
        self.contract = load_contract(CONTRACT_PATH)

    def ingest(self, day):
        data = make_dataset(300, datetime(2026, 10, 27, 17, 59, 59, tzinfo=UTC), step_minutes=30)
        start = datetime(2026, 10, day, tzinfo=UTC)
        run_bronze_ingest(start, datetime(2026, 10, day + 1, tzinfo=UTC),
                          settings=ArxivSettings(page_size=50, max_pages=20),
                          store=self.store, client=FakeArxiv(data), now=datetime(2026, 10, day + 1, 0, 30, tzinfo=UTC))

    def test_end_to_end_with_overlapping_partitions(self):
        self.ingest(28)
        self.ingest(29)  # same feed again: overlap must collapse
        rep = run_build_silver(store=self.store, contract=self.contract, codec_factory=JsonCodec)
        self.assertEqual(rep["n_quarantined"], 0)
        self.assertEqual(rep["n_bronze_partitions"], 2)
        self.assertGreater(rep["n_superseded_duplicates"], 0)
        self.assertEqual(rep["n_papers_total"], rep["n_unique_papers_in_bronze"])
        summary = summarize(rep)
        self.assertEqual(run_verify_silver(summary, store=self.store, contract=self.contract,
                                           codec_factory=JsonCodec)["n_papers_total"], rep["n_papers_total"])

    def test_verify_task_rejects_a_summary_that_disagrees_with_disk(self):
        self.ingest(28)
        rep = run_build_silver(store=self.store, contract=self.contract, codec_factory=JsonCodec)
        wrong = summarize(rep) | {"n_papers_total": rep["n_papers_total"] + 1}
        with self.assertRaises(RuntimeError):
            run_verify_silver(wrong, store=self.store, contract=self.contract, codec_factory=JsonCodec)

    def test_default_contract_path_honours_env(self):
        import os
        old = os.environ.get("DOCPULSE_CONTRACT")
        os.environ["DOCPULSE_CONTRACT"] = "/somewhere/else.yml"
        try:
            self.assertEqual(default_contract_path(), Path("/somewhere/else.yml"))
        finally:
            os.environ.pop("DOCPULSE_CONTRACT") if old is None else os.environ.__setitem__("DOCPULSE_CONTRACT", old)


@unittest.skipUnless(HAVE_PYARROW, "pyarrow not installed: these run on a machine with `pip install -r requirements-dev.txt`")
class ParquetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)
        self.contract = load_contract(CONTRACT_PATH)
        self.fields = paper_fields(self.contract)

    def sample_rows(self):
        from docpulse.silver.parse import parse_entry, ATOM
        from silver_fakes import FEED_HEAD
        from xml.etree.ElementTree import fromstring
        root = fromstring(FEED_HEAD.format(total=1, n=1) + rich_entry(comment=None) + "</feed>")
        return [parse_entry(root.find(ATOM + "entry"), self.contract.known_elements).row]

    def test_roundtrip_preserves_types_and_nulls(self):
        codec = ParquetCodec(self.fields)
        rows = self.sample_rows()
        back = codec.decode(codec.encode(rows))
        self.assertEqual(back, rows)
        self.assertIsNone(back[0]["comment"])
        self.assertIsInstance(back[0]["authors"], list)
        self.assertEqual(back[0]["published"].utcoffset().total_seconds(), 0)

    def test_schema_matches_contract_types(self):
        import pyarrow as pa
        import pyarrow.parquet as pq
        codec = ParquetCodec(self.fields)
        schema = pq.read_schema(pa.BufferReader(codec.encode(self.sample_rows())))
        self.assertEqual(schema.field("version").type, pa.int32())
        self.assertEqual(schema.field("authors").type, pa.list_(pa.string()))
        self.assertEqual(schema.field("published").type, pa.timestamp("us", tz="UTC"))

    def test_encoding_is_deterministic_and_empty_table_works(self):
        codec = ParquetCodec(self.fields)
        rows = self.sample_rows()
        self.assertEqual(codec.encode(rows), codec.encode(rows))
        self.assertEqual(codec.decode(codec.encode([])), [])

    def test_full_build_verify_and_rerun_byte_identical(self):
        write_bronze(self.store, datetime(2026, 10, 28).date(), n_papers(30) + [rich_entry("2610.90001", title="")])
        rep1 = build_silver(self.store, self.contract, ParquetCodec)
        verify_silver(self.store, self.contract, ParquetCodec)
        snap = {k: self.store.get_bytes(k) for k in self.store.list_keys("silver/") if "_manifest" not in k}
        rep2 = build_silver(self.store, self.contract, ParquetCodec)
        self.assertEqual(rep2["months_written"], [])
        self.assertEqual({k: self.store.get_bytes(k) for k in self.store.list_keys("silver/") if "_manifest" not in k}, snap)
        self.assertEqual(rep1["n_quarantined"], 1)
        self.assertTrue(any(k.endswith("quarantine.parquet") for k in snap))
        self.assertTrue(any(k.endswith("papers.parquet") for k in snap))


if __name__ == "__main__":
    unittest.main()
