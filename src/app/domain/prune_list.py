"""Parse an agent-authored prune list into dismissal instructions.

The third half of the paste-a-file vocabulary beside domain/tags_yaml.py and
domain/yaml_import.py, and pure in the same sense: no I/O, yaml.safe_load
ONLY -- this is pasted text from outside the trust boundary, written by an
agent that classified a batch of /admin/discoveries leads.

It keys on the Eventernote event id (the external id the discovery copy
block's URLs expose) because that is the only id a human or agent writing
this file ever sees -- DiscoveredEvent.id is an internal primary key the
file never carries.

Unlike yaml_import.py's parse_draft, which prefers a warning and a skipped
row so a slightly-off draft still renders something to fix, this module
raises on anything it cannot fully understand. Every surviving entry becomes
a permanent dismissal (Task 2's dismiss_lead, one call per lead, nothing
un-dismisses), so a half-understood file is worse than no file at all --
there is no preview step downstream to catch a misread row.
"""

from __future__ import annotations

import dataclasses

import yaml

from app.domain.types import DismissReason

_VALID = {r.value for r in DismissReason}


class PruneListError(Exception):
    """The file cannot be used. Unlike the draft parser, which prefers a
    warning and a skipped row, an unusable prune list must raise: every entry
    becomes a permanent dismissal, so a half-understood file is worse than
    no file."""


@dataclasses.dataclass(frozen=True)
class PruneEntry:
    event_id: str
    reason: DismissReason


@dataclasses.dataclass(frozen=True)
class PruneList:
    entries: tuple[PruneEntry, ...]
    warnings: tuple[str, ...]


def parse_prune_list(text: str) -> PruneList:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PruneListError(f"that doesn't parse as YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise PruneListError(
            "a prune list is a YAML mapping (key: value lines) -- this isn't one"
        )

    dismiss = data.get("dismiss")
    if not dismiss:
        raise PruneListError(
            "no dismissals found -- expected a top-level 'dismiss:' block with "
            "at least one reason and id"
        )
    if not isinstance(dismiss, dict):
        raise PruneListError(
            f"dismiss: expected a mapping of reason -> list of ids, got "
            f"{type(dismiss).__name__}"
        )

    warnings: list[str] = []
    entries: list[PruneEntry] = []
    seen: dict[str, DismissReason] = {}

    for reason_key, ids in dismiss.items():
        if reason_key not in _VALID:
            raise PruneListError(
                f"dismiss.{reason_key}: not a recognised dismissal reason "
                f"(expected one of {sorted(_VALID)})"
            )
        if not isinstance(ids, list):
            raise PruneListError(
                f"dismiss.{reason_key}: expected a list of ids, got "
                f"{type(ids).__name__}"
            )
        reason = DismissReason(reason_key)
        for raw_id in ids:
            # str(), never int -- eventernote_event_id is a String column,
            # and `- 481833` reads back from YAML as an int. Comparing int
            # to str would silently match nothing downstream.
            event_id = str(raw_id).strip()
            prior = seen.get(event_id)
            if prior is None:
                seen[event_id] = reason
                entries.append(PruneEntry(event_id=event_id, reason=reason))
            elif prior == reason:
                warnings.append(
                    f"{event_id}: listed twice under {reason_key!r} -- "
                    "duplicate ignored"
                )
            else:
                raise PruneListError(
                    f"{event_id}: listed under both {prior.value!r} and "
                    f"{reason_key!r} -- resolve which reason applies"
                )

    return PruneList(entries=tuple(entries), warnings=tuple(warnings))
