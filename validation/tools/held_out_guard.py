"""Single definition of "held-out" for research entry points (stdlib only).

A fixture is held-out, and must be refused unless the caller passes an explicit
``--allow-held-out`` for a frozen run, when it is either:

- ``purpose == "validation"`` (the reserved #57 set), or
- tagged ``split-reserved-57`` in ``conditions.challenge_tags``
  (docs/validation/PLATE_SELECTION_SPLIT.md).

Either signal is enough, so retagging or repurposing a clip cannot silently
release it. Entry points keep their own exception type and message; they only
share this predicate so the rule cannot drift between scripts.
"""
from __future__ import annotations

from typing import Any, Mapping

RESERVED_TAG = "split-reserved-57"


def is_held_out(fixture: Mapping[str, Any]) -> bool:
    """True when the fixture must not be opened without ``--allow-held-out``."""
    if fixture.get("purpose") == "validation":
        return True
    tags = (fixture.get("conditions") or {}).get("challenge_tags") or []
    return RESERVED_TAG in tags
