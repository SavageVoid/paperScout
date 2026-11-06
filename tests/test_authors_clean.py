"""Day 7: clean_name, the one definition of an author name shared by both paths.

Day 6 folded arXiv's markup debris in a one-off backfill script. Day 7 moved that
fold into docpulse.search.authors so the ingest path and the query path cannot
disagree -- and then promptly proved why it matters: the first reload without it
wrote 4,271 rows of raw names, moved the authors fingerprint, and left eight names
that no one could search for. Nothing failed. These tests are the alarm that was
missing.
"""

from __future__ import annotations

import unittest

from docpulse.search.authors import clean_name, normalise


class CleanNameTests(unittest.TestCase):
    def test_single_char_braces_are_folded(self):
        # arXiv renders some accents as "Herv{é}"; left alone this matches nothing
        self.assertEqual(clean_name("Herv{é} Panetto"), "Hervé Panetto")
        self.assertEqual(clean_name("Łukasz Smoli{ń}ski"), "Łukasz Smoliński")

    def test_latex_accents_are_composed(self):
        self.assertEqual(clean_name("M{\\\"u}ller"), "Müller")
        self.assertEqual(clean_name("Dvo{\\v r}{\\'a}k"), "Dvov rák")

    def test_html_entities_are_unescaped(self):
        self.assertEqual(clean_name("Smith &amp; Wesson"), "Smith & Wesson")

    def test_stray_braces_and_backslashes_never_survive(self):
        for raw in ("A { B", "a}b", "back\\slash", "{{double}}"):
            out = clean_name(raw)
            assert "{" not in out and "}" not in out and "\\" not in out, (raw, out)

    def test_whitespace_is_collapsed_and_stripped(self):
        self.assertEqual(clean_name("  Tim   Rocktäschel  "), "Tim Rocktäschel")

    def test_ordinary_names_pass_through_untouched(self):
        for name in ("Yann LeCun", "Wei Wang", "Randall Balestriero"):
            self.assertEqual(clean_name(name), name)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(clean_name(None), "")
        self.assertEqual(clean_name(""), "")
        self.assertEqual(clean_name("   "), "")

    def test_clean_then_normalise_is_what_the_exact_match_stores(self):
        # the loader writes authors_ci with exactly this composition
        self.assertEqual(normalise(clean_name("Herv{é} Panetto")), "hervé panetto")
        self.assertEqual(normalise(clean_name("  Wei   Wang ")), "wei wang")

    def test_accent_folding_is_not_attempted(self):
        # documented gap: "Herve" will not match "Hervé" until unaccent is added.
        # This test pins the current behaviour so a future change is deliberate.
        self.assertNotEqual(normalise(clean_name("Hervé Panetto")), "herve panetto")


if __name__ == "__main__":
    unittest.main()
