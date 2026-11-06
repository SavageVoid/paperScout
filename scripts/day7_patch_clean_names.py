#!/usr/bin/env python3
"""Day 7 stage 2b: one definition of a clean author name, used by both paths.

The reload exposed a regression: day6_add_authors.py cleaned names before writing
them (HTML entities, LaTeX accents, `Herv{é}` -> `Hervé`, stray braces), but the
loader wrote silver's raw strings. Reloading therefore *undid* the fold the Day 6
README says must happen, and moved the authors fingerprint.

Two fixes, one source of truth:

  1. `clean_name` moves into docpulse/search/authors.py, next to `normalise`, so an
     ingest path and a search-query path cannot disagree about what a name is.
  2. the loader's `_authors` calls it, so silver -> paper_meta produces exactly what
     the backfill produced.

After this the authors fingerprint must return to its pre-reload value. That is the
acceptance test, not a note in a doc.

    python3 scripts/day7_patch_clean_names.py --dry-run
    python3 scripts/day7_patch_clean_names.py
"""

from __future__ import annotations

import argparse
import ast
import difflib
import pathlib
import sys

AUTHORS = pathlib.Path("src/docpulse/search/authors.py")
LOADER = pathlib.Path("scripts/day4_load_meta.py")

AUTHORS_IMPORT_OLD = """from __future__ import annotations

import re
"""

AUTHORS_IMPORT_NEW = """from __future__ import annotations

import re
import unicodedata
from html import unescape
"""

HELPERS_OLD = "def normalise(author: str | None) -> str:"

HELPERS_NEW = r'''# arXiv ships names as XML text, which carries three kinds of debris:
#
#   "Herv{é} Panetto"     single-character braces, a rendering artefact
#   "M{\"u}ller"          the older LaTeX accent spelling
#   "&amp; colleagues"    HTML entities, from XML escaping
#
# Left alone, the first can never match someone typing "Hervé". This lives in the
# module the search engine already owns, so the loader and the query side cannot
# drift apart -- the Day 6 backfill kept its own copy, and the reload immediately
# showed what that costs.
_SINGLE_CHAR_BRACES_RE = re.compile(r"\{([^{}\\\\])\}")
_LATEX_ACCENT_RE = re.compile(r"\{\\([`'^\"~=.])\s*\{?([A-Za-z])\}?\}")
_ACCENT_MAP = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308",
               "~": "\u0303", "=": "\u0304", ".": "\u0307"}


def _compose(letter: str, combining: str) -> str:
    return unicodedata.normalize("NFC", letter + combining) if combining else letter


def clean_name(name: str | None) -> str:
    """Fold arXiv's markup debris out of a name and collapse its whitespace."""
    text = unescape(name or "")
    text = _LATEX_ACCENT_RE.sub(
        lambda m: _compose(m.group(2), _ACCENT_MAP.get(m.group(1), "")), text)
    text = _SINGLE_CHAR_BRACES_RE.sub(r"\1", text)
    text = text.replace("{", "").replace("}", "").replace("\\", "")
    return " ".join(text.split())


def normalise(author: str | None) -> str:'''

HELPER_IMPORT_OLD = '''    """
    value = raw.get("authors")'''

HELPER_IMPORT_NEW = '''    """
    from docpulse.search.authors import clean_name

    value = raw.get("authors")'''

LOADER_CALL_OLD = r'''        name = " ".join(item.strip().strip("'\"").split())'''

LOADER_CALL_NEW = r'''        name = clean_name(item.strip().strip("'\""))'''

EDITS = (
    ("authors.py imports unicodedata and unescape", AUTHORS, AUTHORS_IMPORT_OLD, AUTHORS_IMPORT_NEW),
    ("authors.py gains clean_name", AUTHORS, HELPERS_OLD, HELPERS_NEW),
    ("_authors imports clean_name", LOADER, HELPER_IMPORT_OLD, HELPER_IMPORT_NEW),
    ("loader cleans each name", LOADER, LOADER_CALL_OLD, LOADER_CALL_NEW),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Share clean_name between loader and search")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--authors", default=str(AUTHORS))
    parser.add_argument("--loader", default=str(LOADER))
    args = parser.parse_args(argv)

    targets = {AUTHORS: pathlib.Path(args.authors), LOADER: pathlib.Path(args.loader)}
    for path in targets.values():
        if not path.is_file():
            print(f"ERROR: {path} not found (run from the repo root)")
            return 2

    before = {p: targets[p].read_text() for p in targets}
    after = dict(before)
    applied, skipped = [], []
    for name, which, old, new in EDITS:
        text = after[which]
        if new in text:
            skipped.append(name)
            continue
        hits = text.count(old)
        if hits != 1:
            raise SystemExit(
                f"ERROR: anchor for {name!r} appears {hits} times in {which}, expected 1.\n"
                "Refusing to guess. Nothing was written."
            )
        after[which] = text.replace(old, new, 1)
        applied.append(name)

    for name in applied:
        print(f"  + {name}")
    for name in skipped:
        print(f"  . {name} (already present)")
    if not applied:
        print("nothing to do.")
        return 0

    for text in after.values():
        ast.parse(text)

    if args.dry_run:
        for which in targets:
            if before[which] == after[which]:
                continue
            sys.stdout.writelines(difflib.unified_diff(
                before[which].splitlines(keepends=True),
                after[which].splitlines(keepends=True),
                fromfile=f"{targets[which]} (current)",
                tofile=f"{targets[which]} (patched)",
                n=2,
            ))
        print("--dry-run: nothing written.")
        return 0

    for which in targets:
        if before[which] != after[which]:
            targets[which].write_text(after[which])
            print(f"  wrote {targets[which]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
