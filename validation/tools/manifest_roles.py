#!/usr/bin/env python3
"""Check that clips of one recording session never cross evaluation roles (stdlib only).

docs/validation/PLATE_SELECTION_SPLIT.md: roles are assigned per recording session, never per clip, and
each fixture carries exactly one `split-*` tag in `conditions.challenge_tags`. This reads a fixture
manifest (the git-ignored private one is the useful target) and reports every breach.

A fixture's session is its `session-YYYYMMDD` tag when present, otherwise the date in a phone-style
`VID_YYYYMMDD_` file name found in `source.reference` or `media.repository_path`. A fixture with no
recognisable session is not checked against others. Exit status 1 means a breach that was not explicitly
accepted with `--accept SESSION`; accepted sessions are still printed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

SESSION_TAG = re.compile(r"^session-(\d{8})$")
FILENAME_DATE = re.compile(r"VID_(\d{8})_")
ROLE_PREFIX = "split-"


def tags_of(fixture: Mapping[str, Any]) -> list[str]:
    return list((fixture.get("conditions") or {}).get("challenge_tags") or [])


def roles_of(fixture: Mapping[str, Any]) -> list[str]:
    return sorted(tag for tag in tags_of(fixture) if tag.startswith(ROLE_PREFIX))


def session_of(fixture: Mapping[str, Any]) -> str | None:
    for tag in tags_of(fixture):
        match = SESSION_TAG.match(tag)
        if match:
            return f"session-{match.group(1)}"
    for text in ((fixture.get("source") or {}).get("reference"), (fixture.get("media") or {}).get("repository_path")):
        match = FILENAME_DATE.search(text or "")
        if match:
            return f"session-{match.group(1)}"
    return None


def check(fixtures: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Group fixtures by session and role and list every breach of the one-role-per-session rule."""
    sessions: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    violations: list[dict[str, Any]] = []
    for fixture in fixtures:
        identifier = fixture.get("id", "?")
        roles = roles_of(fixture)
        if len(roles) > 1:
            violations.append({"kind": "multiple_roles", "fixture": identifier, "roles": roles})
        session = session_of(fixture)
        if session is not None:
            sessions[session][roles[0] if roles else "unassigned"].append(identifier)
    for session, by_role in sorted(sessions.items()):
        assigned = sorted(role for role in by_role if role != "unassigned")
        if len(assigned) > 1:
            violations.append({"kind": "session_crosses_roles", "session": session,
                               "roles": {role: sorted(by_role[role]) for role in assigned}})
        elif assigned and "unassigned" in by_role:
            violations.append({"kind": "unassigned_in_assigned_session", "session": session, "role": assigned[0],
                               "fixtures": sorted(by_role["unassigned"])})
    return {"sessions": {s: {r: sorted(ids) for r, ids in by.items()} for s, by in sorted(sessions.items())},
            "violations": violations}


def unaccepted(report: Mapping[str, Any], accepted: Iterable[str]) -> list[dict[str, Any]]:
    allowed = set(accepted)
    return [v for v in report["violations"] if v.get("session") not in allowed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--accept", action="append", default=[], metavar="SESSION",
                        help="session id (session-YYYYMMDD) whose breach is a known, documented exception")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        report = check(manifest["fixtures"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    failing = unaccepted(report, args.accept)
    if args.json:
        print(json.dumps({**report, "unaccepted": failing}, indent=2, sort_keys=True))
    else:
        for violation in report["violations"]:
            status = "ACCEPTED" if violation not in failing else "BREACH"
            print(f"{status}: {json.dumps(violation, sort_keys=True)}")
        print(f"{len(report['sessions'])} sessions, {len(report['violations'])} breaches, {len(failing)} not accepted")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
