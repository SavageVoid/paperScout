"""Planning: which papers need a vector?

Pure functions, no I/O. Everything that decides "embed this / skip that" lives
here so the decision is unit-testable without a database, an encoder or S3.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

TEXT_SEPARATOR = "\n\n"


def compose_text(title: str, abstract: str) -> str:
    """title + blank line + abstract, stripped. Change this? bump the template version."""
    return f"{(title or '').strip()}{TEXT_SEPARATOR}{(abstract or '').strip()}".strip()


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def computed_text_hash(text: str) -> str:
    """Only used when silver has no text_hash column; flagged in the summary."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Target:
    paper_id: str
    version: int
    text_hash: str
    text: str
    chars: int


@dataclass
class Plan:
    targets: list[Target] = field(default_factory=list)
    n_rows_seen: int = 0
    n_unique_papers: int = 0
    n_duplicate_ids_collapsed: int = 0
    n_missing_id: int = 0
    n_missing_text: int = 0
    n_new: int = 0
    n_changed: int = 0
    n_unchanged: int = 0
    n_model_mismatch: int = 0
    text_hash_computed: int = 0

    @property
    def n_to_embed(self) -> int:
        return len(self.targets)

    @property
    def chars_to_embed(self) -> int:
        return sum(t.chars for t in self.targets)

    def summary(self) -> dict:
        return {
            "n_rows_seen": self.n_rows_seen,
            "n_unique_papers": self.n_unique_papers,
            "n_duplicate_ids_collapsed": self.n_duplicate_ids_collapsed,
            "n_missing_id": self.n_missing_id,
            "n_missing_text": self.n_missing_text,
            "n_new": self.n_new,
            "n_changed": self.n_changed,
            "n_unchanged": self.n_unchanged,
            "n_model_mismatch": self.n_model_mismatch,
            "text_hash_computed": self.text_hash_computed,
            "n_to_embed": self.n_to_embed,
            "chars_to_embed": self.chars_to_embed,
        }


def dedupe_rows(rows: list[dict], plan: Plan) -> dict[str, dict]:
    """One row per paper_id, highest version wins.

    Silver should already be deduped. Doing it again here is cheap, and it means
    a silver regression cannot silently double-embed or store a stale version.
    """
    best: dict[str, dict] = {}
    for row in rows:
        plan.n_rows_seen += 1
        pid = row.get("paper_id")
        if not pid:
            plan.n_missing_id += 1
            continue
        current = best.get(pid)
        if current is None:
            best[pid] = row
            continue
        plan.n_duplicate_ids_collapsed += 1
        if int(row.get("version") or 1) >= int(current.get("version") or 1):
            best[pid] = row
    plan.n_unique_papers = len(best)
    return best


def build_plan(
    rows: list[dict],
    existing: dict[str, tuple[str, str]],
    model_fp: str,
    *,
    max_chars: int = 4000,
) -> Plan:
    """existing maps paper_id -> (text_hash, model_fp) already in the table."""
    plan = Plan()
    by_id = dedupe_rows(rows, plan)

    for pid, row in by_id.items():
        text = truncate(compose_text(row.get("title", ""), row.get("abstract", "")), max_chars)
        if not text:
            plan.n_missing_text += 1
            continue

        text_hash = row.get("text_hash")
        if not text_hash:
            text_hash = computed_text_hash(text)
            plan.text_hash_computed += 1
        text_hash = str(text_hash)

        prior = existing.get(pid)
        if prior is None:
            plan.n_new += 1
        else:
            prior_hash, prior_fp = prior
            if prior_fp != model_fp:
                plan.n_model_mismatch += 1
            elif prior_hash == text_hash:
                plan.n_unchanged += 1
                continue
            else:
                plan.n_changed += 1

        plan.targets.append(
            Target(
                paper_id=pid,
                version=int(row.get("version") or 1),
                text_hash=text_hash,
                text=text,
                chars=len(text),
            )
        )

    # Deterministic order: makes runs reproducible and batching predictable.
    plan.targets.sort(key=lambda t: t.paper_id)
    return plan
