"""Content fingerprint of a set of raw arXiv Atom pages.

Raw page bytes are an unreliable thing to compare across fetches: responses carry a feed-level
``<updated>`` timestamp equal to the time of the request (observed in uncached responses; identical
URLs fetched minutes apart were once returned byte-identical, probably via arXiv's CDN), and page
boundaries shift as papers are updated mid-run. What IS stable is the set of ``<entry>`` elements. The fingerprint hashes each
entry on its own, de-duplicates (an entry may appear on two adjacent pages) and hashes the sorted
result, so it is independent of the feed envelope, page boundaries and entry order.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from xml.etree.ElementTree import tostring

from defusedxml import ElementTree as SafeET

ATOM = "{http://www.w3.org/2005/Atom}"


def entries_fingerprint(pages: Iterable[bytes]) -> tuple[str, int]:
    """Return (fingerprint, number of distinct entries)."""
    hashes: set[str] = set()
    for raw in pages:
        root = SafeET.fromstring(raw)
        for entry in root.findall(ATOM + "entry"):
            hashes.add(hashlib.sha256(tostring(entry, encoding="utf-8")).hexdigest())
    combined = hashlib.sha256("".join(sorted(hashes)).encode()).hexdigest()
    return combined[:16], len(hashes)
