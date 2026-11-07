#!/usr/bin/env python3
"""Day 7 tail: give the services that actually die a restart policy.

Postgres, the object store, the Airflow webserver and the Airflow scheduler declared
no `restart:` at all, so they defaulted to `no` and stayed dead for 37 hours while the
box was suspended. `searchapi` already says `unless-stopped`; the embedder already says
`"no"` on purpose, because it only ever runs through `compose run --rm`.

`airflow-init` is deliberately left alone: it is a one-shot migration container, and a
restart policy on a process whose job is to exit would loop it on every stack start.

Every service name is looked up through PyYAML on the real file before anything is
written, so a wrong file or a renamed service fails loudly instead of inserting a line
in the wrong block.

    python3 scripts/day7_patch_restart_policy.py --dry-run
    python3 scripts/day7_patch_restart_policy.py

Runs twice safely. Also fixes one cosmetic slip in EVIDENCE.md: a bold marker left
inside a code span, which renders as literal asterisks.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

COMPOSE = Path("docker-compose.yml")
EVIDENCE = Path("EVIDENCE.md")

POLICY = "unless-stopped"

# services that must survive a reboot or a daemon restart
TARGETS = ["postgres", "objectstore", "airflow-webserver", "airflow-scheduler"]

# services we touch on purpose NOT to change
SKIP = {
    "airflow-init": "one-shot migration container; a restart policy would loop it",
    "searchapi": "already unless-stopped in docker-compose.day4.yml",
    "embedder": 'already restart: "no" in docker-compose.day3.yml, by design',
}

EVIDENCE_OLD = "| Schedule | 06:00 daily, `is_paused **False**`, one DAG `arxiv_bronze_ingest` |"
EVIDENCE_NEW = "| Schedule | 06:00 daily, `is_paused False`, one DAG `arxiv_bronze_ingest` |"


def load_services(text: str) -> dict:
    import yaml

    doc = yaml.safe_load(text)
    services = (doc or {}).get("services")
    if not isinstance(services, dict) or not services:
        raise SystemExit("no 'services:' mapping found — is this the stack's compose file?")
    return services


def insert_policy(text: str, service: str) -> tuple[str, str]:
    """Insert the policy directly under the service header, two spaces deeper."""
    lines = text.splitlines(keepends=True)
    header = f"  {service}:"
    hits = [i for i, line in enumerate(lines) if line.rstrip("\n") == header]
    if len(hits) != 1:
        raise SystemExit(
            f"'{service}': found {len(hits)} header lines exactly equal to {header!r}, "
            "expected 1. Nothing was written."
        )
    start = hits[0]

    # the service body runs until the next line indented two spaces (or less)
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        body.append(line)
    if any(line.strip().startswith("restart:") for line in body):
        return text, f"  . {service}: already declares a restart policy"

    lines.insert(start + 1, f"    restart: {POLICY}\n")
    return "".join(lines), f"  + {service}: restart: {POLICY}"


def patch_compose(text: str) -> tuple[str, list[str]]:
    services = load_services(text)
    missing = [name for name in TARGETS if name not in services]
    if missing:
        raise SystemExit(
            f"service(s) not found in {COMPOSE}: {', '.join(missing)}. "
            f"Found: {', '.join(sorted(services))}. Nothing was written."
        )

    notes = []
    for name in TARGETS:
        text, note = insert_policy(text, name)
        notes.append(note)

    after = load_services(text)
    for name in TARGETS:
        actual = after[name].get("restart")
        if actual != POLICY:
            raise SystemExit(f"post-check failed: services.{name}.restart is {actual!r}")
    if after["airflow-init"].get("restart"):
        raise SystemExit("post-check failed: airflow-init picked up a restart policy")
    return text, notes


def patch_evidence(text: str) -> tuple[str, str]:
    if EVIDENCE_NEW in text:
        return text, "  . EVIDENCE.md: already applied"
    if text.count(EVIDENCE_OLD) != 1:
        return text, "  ! EVIDENCE.md: anchor not found once, skipped (cosmetic only)"
    return text.replace(EVIDENCE_OLD, EVIDENCE_NEW, 1), "  + EVIDENCE.md: is_paused bold inside code span"


def _diff(before: str, after: str, path: Path) -> None:
    sys.stdout.writelines(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"{path} (current)",
            tofile=f"{path} (patched)",
            n=3,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="give the long-running services a restart policy")
    parser.add_argument("--compose", default=str(COMPOSE))
    parser.add_argument("--evidence", default=str(EVIDENCE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    compose_path = Path(args.compose)
    if not compose_path.is_file():
        raise SystemExit(f"{compose_path} not found — run this from the repo root")

    before = compose_path.read_text(encoding="utf-8")
    after, notes = patch_compose(before)
    for note in notes:
        print(note)
    for name, why in SKIP.items():
        print(f"  - {name}: left alone — {why}")

    evidence_path = Path(args.evidence)
    ev_before = ev_after = ""
    if evidence_path.is_file():
        ev_before = evidence_path.read_text(encoding="utf-8")
        ev_after, note = patch_evidence(ev_before)
        print(note)

    if after == before and ev_after == ev_before:
        print("nothing to do")
        return 0

    if args.dry_run:
        if after != before:
            _diff(before, after, compose_path)
        if ev_after != ev_before:
            _diff(ev_before, ev_after, evidence_path)
        print("--dry-run: nothing written.")
        return 0

    if after != before:
        compose_path.write_text(after, encoding="utf-8")
        print(f"wrote {compose_path}")
    if ev_after != ev_before:
        evidence_path.write_text(ev_after, encoding="utf-8")
        print(f"wrote {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
