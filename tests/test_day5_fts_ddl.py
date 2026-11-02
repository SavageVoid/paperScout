"""Day 5 step 3a: the migration script's SQL, tested before it touches the database.

A schema change is the one thing in this project that cannot be tested by running
the unit tests afterwards, so the properties that make it safe are asserted here:
every statement idempotent, the generated expression immutable, and the stored
column provably equal to the inline expression the probe measured.
"""

from __future__ import annotations

import importlib.util
import pathlib
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "day5_add_fts.py"
_spec = importlib.util.spec_from_file_location("day5_add_fts_under_test", SCRIPT)
assert _spec and _spec.loader
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)


class IdempotenceTests(unittest.TestCase):
    def test_every_statement_can_run_twice(self):
        for note, sql in migration.DDL:
            if sql.startswith("ANALYZE"):
                continue  # inherently repeatable and harmless
            self.assertTrue(
                "IF NOT EXISTS" in sql,
                f"not idempotent ({note}): {sql}",
            )

    def test_nothing_destructive(self):
        for _note, sql in migration.DDL:
            for verb in ("DROP ", "TRUNCATE", "DELETE ", "UPDATE "):
                self.assertNotIn(verb, sql.upper())

    def test_analyze_runs_last(self):
        self.assertTrue(migration.DDL[-1][1].startswith("ANALYZE"))


class GeneratedColumnTests(unittest.TestCase):
    def expression(self) -> str:
        return migration.FTS_EXPR

    def test_the_cast_to_regconfig_is_explicit(self):
        # to_tsvector('english', text) is STABLE only; Postgres rejects a STABLE
        # expression in a generated column, so the cast is not cosmetic
        self.assertEqual(self.expression().count("'english'::regconfig"), 2)
        self.assertNotIn("to_tsvector('english',", self.expression())

    def test_title_outranks_abstract(self):
        self.assertIn("coalesce(title, '')), 'A')", self.expression())
        self.assertIn("coalesce(abstract, '')), 'B')", self.expression())

    def test_nulls_are_coalesced(self):
        # both columns are nullable; a null would make the whole tsvector null
        self.assertIn("coalesce(title, '')", self.expression())
        self.assertIn("coalesce(abstract, '')", self.expression())

    def test_column_is_stored_not_virtual(self):
        ddl = migration.DDL[0][1]
        self.assertIn("GENERATED ALWAYS AS", ddl)
        self.assertIn("STORED", ddl)
        self.assertNotIn("VIRTUAL", ddl)

    def test_the_column_is_a_tsvector(self):
        self.assertIn("ADD COLUMN IF NOT EXISTS fts tsvector", migration.DDL[0][1])

    def test_the_index_is_gin_and_on_fts(self):
        gin = [sql for _n, sql in migration.DDL if "CREATE INDEX" in sql and "_fts_idx" in sql]
        self.assertEqual(len(gin), 1)
        self.assertIn("USING gin (fts)", gin[0])


class VerificationTests(unittest.TestCase):
    def test_agreement_query_compares_stored_against_inline(self):
        sql = migration.AGREEMENT_SQL
        self.assertIn("fts @@ to_tsquery", sql)
        self.assertIn("to_tsvector('english'::regconfig", sql)
        self.assertIn("AS stored", sql)
        self.assertIn("AS inline", sql)

    def test_agreement_query_is_parameterised(self):
        self.assertIn("%(tsq)s", migration.AGREEMENT_SQL)
        self.assertNotIn("sparse", migration.AGREEMENT_SQL)

    def test_timings_use_the_same_tsquery_for_both_arms(self):
        self.assertIn("BENCH_TSQ", dir(migration))
        for sql in (migration.STORED_SQL, migration.INLINE_SQL):
            self.assertIn("%(tsq)s", sql)

    def test_updated_index_sorts_null_last(self):
        updated = [sql for _n, sql in migration.DDL if "_updated_idx" in sql]
        self.assertEqual(len(updated), 1)
        self.assertIn("updated DESC NULLS LAST", updated[0])

    def test_dry_run_prints_and_exits_without_a_database(self):
        self.assertEqual(migration.main(["--dry-run"]), 0)


if __name__ == "__main__":
    unittest.main()
