import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from xml.etree.ElementTree import fromstring

from docpulse.silver.build import (
    ContractViolation, NoBronzeDataError, SilverVerificationError, build_silver, discover_bronze,
    papers_key, paper_fields, verify_silver, MANIFEST_KEY,
)
from docpulse.silver.codecs import JsonCodec
from docpulse.silver.contract import ContractError, load_contract, violations
from docpulse.silver.parse import ATOM, normalize_text, parse_entry, split_arxiv_id
from docpulse.storage import LocalStore
from silver_fakes import FEED_HEAD, n_papers, rich_entry, write_bronze

UTC = timezone.utc
CONTRACT_PATH = Path(__file__).resolve().parents[1] / "contracts" / "silver_papers.yml"
D1, D2, D3 = date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30)
T = datetime(2026, 10, 20, 9, 10, 11, tzinfo=UTC)


def parse_one(entry_xml: str, contract):
    root = fromstring(FEED_HEAD.format(total=1, n=1) + entry_xml + "</feed>")
    return parse_entry(root.find(ATOM + "entry"), contract.known_elements)


class ParseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_contract(CONTRACT_PATH)

    def test_messy_entry_is_normalised(self):
        p = parse_one(rich_entry(version=2, updated=datetime(2026, 10, 27, 17, 59, 59, tzinfo=UTC)), self.contract)
        r = p.row
        self.assertEqual((r["arxiv_id"], r["version"]), ("2610.00001", 2))
        self.assertEqual(r["title"], "A Study of Things")
        self.assertEqual(r["abstract"], "We study things. And more things.")
        self.assertEqual(r["authors"], ["Ada Lovelace", "Alan Turing"])
        self.assertEqual((r["primary_category"], r["categories"]), ("cs.LG", ["cs.LG", "stat.ML"]))
        self.assertEqual(r["pdf_url"], "http://arxiv.org/pdf/2610.00001v2")
        self.assertEqual(r["abs_url"], "https://arxiv.org/abs/2610.00001")
        self.assertEqual(r["comment"], "12 pages, 3 figures")
        self.assertEqual(len(r["text_hash"]), 64)
        self.assertEqual(violations(self.contract, r), [])
        self.assertEqual(p.unknown_elements, ())

    def test_old_style_id_and_split(self):
        self.assertEqual(split_arxiv_id("http://arxiv.org/abs/hep-th/9901001v3"), ("hep-th/9901001", 3))
        self.assertEqual(split_arxiv_id("http://arxiv.org/abs/math.GT/0309136v1"), ("math.GT/0309136", 1))
        self.assertIsNone(split_arxiv_id("http://arxiv.org/abs/garbage"))
        self.assertIsNone(split_arxiv_id(None))

    def test_text_hash_ignores_whitespace_and_metadata(self):
        a = parse_one(rich_entry(title="Hello   World", comment="v1 comment"), self.contract).row
        b = parse_one(rich_entry(title="Hello\n World", comment="different comment", version=2), self.contract).row
        self.assertEqual(a["text_hash"], b["text_hash"])
        c = parse_one(rich_entry(title="Hello World!"), self.contract).row
        self.assertNotEqual(a["text_hash"], c["text_hash"])

    def test_unicode_is_nfc_normalised(self):
        self.assertEqual(normalize_text("Cafe\u0301"), "Caf\u00e9")

    def test_unknown_element_is_reported(self):
        p = parse_one(rich_entry(extra="<arxiv:newthing>x</arxiv:newthing><weird/>"), self.contract)
        self.assertEqual(p.unknown_elements, ("newthing", "weird"))

    def test_unparseable_date_becomes_none_with_note(self):
        p = parse_one(rich_entry(updated="not-a-date"), self.contract)
        self.assertIsNone(p.row["updated"])
        self.assertTrue(any("unparseable updated" in n for n in p.notes))


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = load_contract(CONTRACT_PATH)

    def row(self, **over):
        r = parse_one(rich_entry(), self.c).row
        r.update(over)
        return r

    def reasons(self, **over):
        return [r for r, _ in violations(self.c, self.row(**over), reference_time=datetime(2026, 10, 30, tzinfo=UTC))]

    def test_valid_row_has_no_violations(self):
        self.assertEqual(self.reasons(), [])

    def test_each_rule_maps_to_its_reason_code(self):
        self.assertIn("missing_title", self.reasons(title=""))
        self.assertIn("missing_title", self.reasons(title=None))
        self.assertIn("missing_abstract", self.reasons(abstract="   "))
        self.assertIn("missing_authors", self.reasons(authors=[]))
        self.assertIn("missing_category", self.reasons(primary_category=None))
        self.assertIn("missing_category", self.reasons(categories=[]))
        self.assertIn("bad_id", self.reasons(arxiv_id="not-an-id"))
        self.assertIn("bad_id", self.reasons(version=0))
        self.assertIn("bad_date", self.reasons(published=None))
        self.assertIn("bad_type", self.reasons(version="2"))
        self.assertIn("bad_type", self.reasons(text_hash="xyz"))

    def test_derived_columns_do_not_double_report(self):
        self.assertEqual(self.reasons(title=None, text_hash=None), ["missing_title"])
        self.assertEqual(self.reasons(abstract="", text_hash=None), ["missing_abstract"])
        self.assertEqual([r for r in self.reasons(arxiv_id=None, abs_url=None)], ["bad_id"])
        self.assertIn("bad_type", self.reasons(text_hash=None))  # but a hash missing for NO reason is still caught

    def test_date_rules(self):
        self.assertIn("bad_date", self.reasons(updated=datetime(2026, 10, 1, tzinfo=UTC)))  # before published
        self.assertIn("bad_date", self.reasons(published=datetime(1980, 1, 1, tzinfo=UTC),
                                               updated=datetime(1980, 1, 2, tzinfo=UTC)))
        self.assertIn("bad_date", self.reasons(updated=datetime(2026, 12, 1, tzinfo=UTC)))  # far in the future
        self.assertEqual(self.reasons(updated=datetime(2026, 10, 31, tzinfo=UTC)), [])      # within 48 h skew
        self.assertIn("bad_type", self.reasons(published=datetime(2026, 10, 20)))           # naive datetime = wrong type

    def test_malformed_contracts_are_rejected(self):
        text = CONTRACT_PATH.read_text()
        cases = {
            "unknown type": text.replace("type: int32", "type: bigint", 1),
            "reason not defined": text.replace("reason: missing_title", "reason: nope", 1),
            "bad primary key": text.replace("primary_key: arxiv_id", "primary_key: nothing", 1),
            "unknown column key": text.replace("min_value: 1}", "min_value: 1, bogus: 1}", 1),
        }
        for name, bad in cases.items():
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "c.yml"
                path.write_text(bad)
                with self.assertRaises(ContractError, msg=name):
                    load_contract(path)

    def test_hash_changes_with_contract_text(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.yml"
            path.write_text(CONTRACT_PATH.read_text() + "\n# edit\n")
            self.assertNotEqual(load_contract(path).sha256, self.c.sha256)


class SilverTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = LocalStore(self._tmp.name)
        self.contract = load_contract(CONTRACT_PATH)
        self.codec = JsonCodec(paper_fields(self.contract))

    def build(self, store=None, **kw):
        return build_silver(store or self.store, self.contract, JsonCodec, **kw)

    def rows(self, month="2026-10", store=None):
        return self.codec.decode((store or self.store).get_bytes(papers_key(month, self.codec)))

    def silver_snapshot(self, store=None):
        s = store or self.store
        return {k: s.get_bytes(k) for k in s.list_keys("silver/") if not k.endswith("_manifest.json")}


class BuildTests(SilverTestCase):
    def test_basic_build_dedupes_overlap_and_keeps_latest_version(self):
        v1 = rich_entry("2610.00001", 1, updated=datetime(2026, 10, 21, tzinfo=UTC), title="Old title")
        v2 = rich_entry("2610.00001", 2, updated=datetime(2026, 10, 25, tzinfo=UTC), title="New title")
        other = rich_entry("2610.00002", 1)
        write_bronze(self.store, D1, [v1, other])
        write_bronze(self.store, D2, [v1, v2, other])  # overlap: same entries re-fetched, plus a revision
        rep = self.build()
        self.assertEqual((rep["n_entries"], rep["n_valid_entries"], rep["n_quarantined"]), (5, 5, 0))
        self.assertEqual(rep["n_unique_papers_in_bronze"], 2)
        self.assertEqual(rep["n_superseded_duplicates"], 3)
        got = {r["arxiv_id"]: r for r in self.rows()}
        self.assertEqual(set(got), {"2610.00001", "2610.00002"})
        self.assertEqual((got["2610.00001"]["version"], got["2610.00001"]["title"]), (2, "New title"))
        self.assertEqual(rep["n_papers_total"], 2)

    def test_partitioned_by_published_month_and_sorted(self):
        entries = [
            rich_entry("2610.00009", published=datetime(2026, 10, 3, tzinfo=UTC)),
            rich_entry("2609.00002", published=datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)),
            rich_entry("2610.00001", published=datetime(2026, 10, 1, tzinfo=UTC)),
        ]
        write_bronze(self.store, D1, entries)
        self.build()
        self.assertEqual([r["arxiv_id"] for r in self.rows("2026-10")], ["2610.00001", "2610.00009"])
        self.assertEqual([r["arxiv_id"] for r in self.rows("2026-09")], ["2609.00002"])

    def test_bad_entries_are_quarantined_with_reasons_and_raw_xml(self):
        good = n_papers(100)
        bad = [
            rich_entry("2610.90001", omit=("summary",)),
            rich_entry("2610.90002", title="  "),
            rich_entry("2610.90003", updated="garbage"),
            rich_entry("2610.90004", primary=None),
        ]
        write_bronze(self.store, D1, good + bad)
        rep = self.build()
        self.assertEqual(rep["n_quarantined"], 4)
        self.assertEqual(rep["quarantine_by_reason"],
                         {"bad_date": 1, "missing_abstract": 1, "missing_category": 1, "missing_title": 1})
        self.assertEqual(len(self.rows()), 100)  # bad ones are NOT in silver
        qcodec = JsonCodec([f for f in __import__("docpulse.silver.build", fromlist=["x"]).QUARANTINE_FIELDS])
        q = qcodec.decode(self.store.get_bytes("silver/quarantine/ingest_date=2026-10-28/quarantine.jsonl"))
        self.assertEqual(len(q), 4)
        self.assertTrue(all("entry" in r["raw_entry"] and "2610.9000" in r["raw_entry"] for r in q))
        self.assertEqual({r["arxiv_id_hint"].rsplit("/", 1)[1][:10] for r in q},
                         {"2610.90001", "2610.90002", "2610.90003", "2610.90004"})

    def test_circuit_breaker_fails_loudly_and_writes_nothing(self):
        write_bronze(self.store, D1, n_papers(10) + [rich_entry("2610.90001", title="")] * 3)  # 3/13 = 23%
        with self.assertRaises(ContractViolation) as ctx:
            self.build()
        self.assertIn("missing_title", str(ctx.exception))
        self.assertEqual(self.store.list_keys("silver/"), [])

    def test_schema_drift_trips_the_breaker(self):
        drifted = [rich_entry(f"2610.{k:05d}", extra="<arxiv:license>cc</arxiv:license>") for k in range(1, 21)]
        write_bronze(self.store, D1, drifted)
        with self.assertRaises(ContractViolation) as ctx:
            self.build()
        self.assertIn("schema_drift", str(ctx.exception))
        self.assertIn("license", str(ctx.exception))
        self.assertEqual(self.store.list_keys("silver/"), [])

    def test_rare_drift_below_threshold_is_quarantined_not_fatal(self):
        entries = n_papers(60) + [rich_entry("2610.90001", extra="<arxiv:license>cc</arxiv:license>")]
        write_bronze(self.store, D1, entries)
        rep = self.build()
        self.assertEqual(rep["quarantine_by_reason"], {"schema_drift": 1})
        self.assertEqual(len(self.rows()), 60)

    def test_future_dated_entry_is_quarantined(self):
        entries = n_papers(30) + [rich_entry("2610.90001", published=T, updated=datetime(2027, 1, 1, tzinfo=UTC))]
        write_bronze(self.store, D1, entries, fetched_at=datetime(2026, 10, 30, tzinfo=UTC))
        self.assertEqual(self.build()["quarantine_by_reason"], {"bad_date": 1})

    def test_no_bronze_and_incomplete_partitions(self):
        with self.assertRaises(NoBronzeDataError):
            self.build()
        write_bronze(self.store, D1, n_papers(5), complete=False)
        with self.assertRaises(NoBronzeDataError):
            self.build()
        write_bronze(self.store, D2, n_papers(5))
        rep = self.build()
        self.assertEqual((rep["n_bronze_partitions"], rep["n_bronze_partitions_skipped_incomplete"]), (1, 1))
        self.assertEqual(discover_bronze(self.store), ([D2], [D1]))

    def test_dry_run_writes_nothing_and_reports(self):
        write_bronze(self.store, D1, n_papers(40) + [rich_entry("2610.90001", title="")])
        rep = self.build(dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertEqual(rep["quarantine_by_reason"], {"missing_title": 1})
        self.assertEqual(rep["quarantine_examples"][0]["reasons"], ["missing_title"])
        self.assertEqual(self.store.list_keys("silver/"), [])


class IdempotencyAndMergeTests(SilverTestCase):
    def test_rerun_changes_nothing(self):
        write_bronze(self.store, D1, n_papers(30) + [rich_entry("2610.90001", title="")])
        write_bronze(self.store, D2, n_papers(30))
        self.build()
        before = self.silver_snapshot()
        rep = self.build()
        self.assertEqual(rep["months_written"], [])
        self.assertEqual(rep["months_unchanged"], ["2026-10"])
        self.assertEqual(self.silver_snapshot(), before)  # byte-identical

    def test_incremental_equals_one_shot(self):
        e1 = n_papers(20)
        e2 = [rich_entry("2610.00005", 2, updated=datetime(2026, 10, 27, tzinfo=UTC), title="Revised")] + n_papers(25)[20:]
        with tempfile.TemporaryDirectory() as other:
            one_shot = LocalStore(other)
            for st in (self.store, one_shot):
                write_bronze(st, D1, e1)
                write_bronze(st, D2, e2)
            self.build(until=D1)
            self.build(since=D2)
            self.build(store=one_shot)
            self.assertEqual(self.silver_snapshot(), self.silver_snapshot(one_shot))
            self.assertEqual(len(self.rows()), 25)

    def test_silver_keeps_papers_after_old_bronze_is_gone(self):
        write_bronze(self.store, D1, [rich_entry("2610.00001")])
        self.build()
        self.store.delete_prefix("bronze/arxiv/ingest_date=2026-10-28/")  # retention removed old bronze
        write_bronze(self.store, D2, [rich_entry("2610.00002")])
        rep = self.build()
        self.assertEqual([r["arxiv_id"] for r in self.rows()], ["2610.00001", "2610.00002"])
        self.assertEqual((rep["n_new_papers"], rep["n_papers_total"]), (1, 2))

    def test_older_snapshot_never_overwrites_a_newer_version(self):
        write_bronze(self.store, D2, [rich_entry("2610.00001", 2, updated=datetime(2026, 10, 25, tzinfo=UTC), title="v2")])
        self.build()
        write_bronze(self.store, D1, [rich_entry("2610.00001", 1, updated=datetime(2026, 10, 21, tzinfo=UTC), title="v1")])
        rep = self.build(until=D1)  # replaying an OLD partition only
        self.assertEqual(rep["n_stale_ignored"], 1)
        self.assertEqual(self.rows()[0]["title"], "v2")

    def test_version_outranks_timestamp_when_they_disagree(self):
        # v3 carries an EARLIER `updated` than v2 (clock skew / odd data): the contract says version wins.
        write_bronze(self.store, D1, [
            rich_entry("2610.00001", 2, updated=datetime(2026, 10, 26, tzinfo=UTC), title="v2"),
            rich_entry("2610.00001", 3, updated=datetime(2026, 10, 24, tzinfo=UTC), title="v3"),
        ])
        self.build()
        self.assertEqual(self.rows()[0]["title"], "v3")
        # and a later, lower version replayed against the stored v3 must not win
        write_bronze(self.store, D2, [rich_entry("2610.00001", 2, updated=datetime(2026, 10, 28, tzinfo=UTC), title="v2 again")])
        rep = self.build(since=D2)
        self.assertEqual(rep["n_stale_ignored"], 1)
        self.assertEqual(self.rows()[0]["title"], "v3")

    def test_revision_and_text_change_counters(self):
        write_bronze(self.store, D1, [rich_entry("2610.00001"), rich_entry("2610.00002"), rich_entry("2610.00003")])
        self.build()
        write_bronze(self.store, D2, [
            rich_entry("2610.00001", 2, updated=datetime(2026, 10, 25, tzinfo=UTC), abstract="Totally new abstract"),
            rich_entry("2610.00002", 1, comment="metadata-only change"),
            rich_entry("2610.00003", 2, updated=datetime(2026, 10, 26, tzinfo=UTC)),  # new version, same text
        ])
        rep = self.build(since=D2)
        self.assertEqual(rep["n_revised_papers"], 2)       # two version bumps
        self.assertEqual(rep["n_text_changed_papers"], 1)  # only one changed the text that gets embedded
        self.assertEqual(rep["n_new_papers"], 0)

    def test_quarantine_file_disappears_when_the_problem_is_fixed(self):
        write_bronze(self.store, D1, n_papers(40) + [rich_entry("2610.90001", title="")])
        self.build()
        key = "silver/quarantine/ingest_date=2026-10-28/quarantine.jsonl"
        self.assertIn(key, self.store.list_keys("silver/"))
        write_bronze(self.store, D1, n_papers(41))  # bronze corrected and re-fetched
        self.build()
        self.assertNotIn(key, self.store.list_keys("silver/"))


class VerifyTests(SilverTestCase):
    def setUp(self):
        super().setUp()
        write_bronze(self.store, D1, n_papers(20, published=datetime(2026, 10, 1, tzinfo=UTC)))
        write_bronze(self.store, D2, [rich_entry("2609.00001", published=datetime(2026, 9, 5, tzinfo=UTC))])
        self.build()

    def verify(self):
        return verify_silver(self.store, self.contract, JsonCodec)

    def rewrite_month(self, month, mutate):
        """Change a month file AND keep the manifest consistent, to test content checks, not checksums."""
        import hashlib
        key = papers_key(month, self.codec)
        rows = mutate(self.codec.decode(self.store.get_bytes(key)))
        data = self.codec.encode(rows)
        self.store.put_bytes(key, data)
        manifest = json.loads(self.store.get_bytes(MANIFEST_KEY))
        for m in manifest["months"]:
            if m["month"] == month:
                m.update(rows=len(rows), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        manifest["n_papers_total"] = sum(m["rows"] for m in manifest["months"])
        self.store.put_bytes(MANIFEST_KEY, json.dumps(manifest).encode())

    def test_healthy_silver_verifies(self):
        self.assertEqual(self.verify()["n_papers_total"], 21)
        self.assertEqual(self.verify()["n_months"], 2)

    def test_detects_tampered_file(self):
        self.store.put_bytes(papers_key("2026-10", self.codec), b"{}\n")
        with self.assertRaises(SilverVerificationError):
            self.verify()

    def test_detects_missing_file_and_stray_file(self):
        self.store.put_bytes("silver/papers/published_month=2026-08/papers.jsonl", b"")
        with self.assertRaises(SilverVerificationError):
            self.verify()
        self.store.delete_prefix("silver/papers/published_month=2026-08/")
        self.store.delete_prefix("silver/papers/published_month=2026-09/")
        with self.assertRaises(SilverVerificationError):
            self.verify()

    def test_detects_contract_violation_inside_a_file(self):
        self.rewrite_month("2026-10", lambda rows: [dict(rows[0], title="")] + rows[1:])
        with self.assertRaises(SilverVerificationError) as ctx:
            self.verify()
        self.assertIn("contract", str(ctx.exception))

    def test_detects_wrong_month_and_cross_month_duplicates_and_text_hash(self):
        self.rewrite_month("2026-10", lambda rows: [dict(rows[0], published=datetime(2026, 7, 1, tzinfo=UTC),
                                                         updated=datetime(2026, 7, 1, tzinfo=UTC))] + rows[1:])
        with self.assertRaises(SilverVerificationError):
            self.verify()
        self.setUp()
        self.rewrite_month("2026-09", lambda rows: rows + [dict(rows[0], arxiv_id="2610.00001")])
        with self.assertRaises(SilverVerificationError):
            self.verify()
        self.setUp()
        self.rewrite_month("2026-10", lambda rows: [dict(rows[0], title="Edited without rehashing")] + rows[1:])
        with self.assertRaises(SilverVerificationError) as ctx:
            self.verify()
        self.assertIn("text_hash", str(ctx.exception))

    def test_detects_changed_contract(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.yml"
            path.write_text(CONTRACT_PATH.read_text() + "\n# tweak\n")
            with self.assertRaises(SilverVerificationError):
                verify_silver(self.store, load_contract(path), JsonCodec)


if __name__ == "__main__":
    unittest.main()
