"""Day 4: the SQL and row-shaping layer, tested without a database.

This file exists because of a real bug: Day 4's SQL assumed the embeddings table
keyed on ``arxiv_id``, while Day 3 creates it with ``paper_id``. Nothing failed
until a query ran. These tests do not need Postgres, but they do pin the column
names the queries are built from.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from docpulse.search import db as search_db
from docpulse.search.db import PgSearchDb, rows_to_results, vector_literal


class VectorLiteralTests(unittest.TestCase):
    def test_brackets_and_precision(self):
        self.assertEqual(vector_literal([0.5, -1.0, 0.1234567]), "[0.500000,-1.000000,0.123457]")

    def test_384_dimensions_stay_small(self):
        literal = vector_literal([0.01] * 384)
        self.assertTrue(literal.startswith("[") and literal.endswith("]"))
        self.assertEqual(literal.count(","), 383)
        self.assertLess(len(literal), 4096)

    def test_integers_are_accepted(self):
        self.assertEqual(vector_literal([1, 0]), "[1.000000,0.000000]")


class SqlRenderTests(unittest.TestCase):
    def test_search_sql_uses_day3s_id_column(self):
        sql = search_db.SEARCH_SQL.format(
            table="paper_embeddings", meta="paper_meta", idcol="paper_id"
        )
        self.assertIn("e.paper_id AS arxiv_id", sql)
        self.assertIn("m.arxiv_id = e.paper_id", sql)
        self.assertNotIn("e.arxiv_id", sql)
        self.assertNotIn("{", sql)  # every placeholder was filled

    def test_paper_sql_uses_day3s_id_column(self):
        sql = search_db.PAPER_SQL.format(
            table="paper_embeddings", meta="paper_meta", idcol="paper_id"
        )
        self.assertIn("e.paper_id = m.arxiv_id", sql)
        self.assertNotIn("{", sql)

    def test_id_column_is_configurable(self):
        db = PgSearchDb("postgresql://u@h/d", id_column="arxiv_id")
        self.assertEqual(db.id_column, "arxiv_id")
        self.assertEqual(PgSearchDb("postgresql://u@h/d").id_column, "paper_id")


class RowsToResultsTests(unittest.TestCase):
    def _row(self, **kw):
        base = {
            "arxiv_id": "2601.00001",
            "title": "T",
            "abstract": "A",
            "categories": "cs.LG",
            "published": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "updated": None,
            "version": 2,
            "score": 0.6123456789,
        }
        base.update(kw)
        return base

    def test_shape_and_rounding(self):
        out = rows_to_results([self._row()])[0]
        self.assertEqual(out["arxiv_id"], "2601.00001")
        self.assertEqual(out["score"], 0.612346)  # 6 dp, not 0.6123456789
        self.assertEqual(out["published"], "2026-01-02T00:00:00+00:00")
        self.assertIsNone(out["updated"])
        self.assertNotIn("abstract", out)

    def test_abstract_is_opt_in(self):
        out = rows_to_results([self._row()], include_abstract=True)[0]
        self.assertEqual(out["abstract"], "A")

    def test_null_title_does_not_crash(self):
        # a vector without metadata is a real state: paper_meta may lag the build
        out = rows_to_results([self._row(title=None, categories=None)])[0]
        self.assertIsNone(out["title"])
        self.assertIsNone(out["categories"])

    def test_string_dates_pass_through(self):
        out = rows_to_results([self._row(published="2026-01-02")])[0]
        self.assertEqual(out["published"], "2026-01-02")


if __name__ == "__main__":
    unittest.main()
