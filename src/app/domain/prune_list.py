"""Parse an agent-authored prune list into dismissal instructions.

The third half of the paste-a-file vocabulary beside domain/tags_yaml.py and
domain/yaml_import.py, and pure in the same sense: no I/O, SafeLoader ONLY
-- this is pasted text from outside the trust boundary, written by an agent
that classified a batch of /admin/discoveries leads. Parsing goes through
`_UniqueKeyLoader`, a `yaml.SafeLoader` subclass (never `Loader`/
`FullLoader`) that additionally refuses a repeated mapping key -- plain
`yaml.safe_load` silently resolves a repeated key to its last occurrence,
and for a file whose every entry becomes a permanent dismissal, a silently
dropped block is the worst available outcome.

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


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader (never Loader/FullLoader -- this is still untrusted pasted
    text) that additionally refuses a repeated mapping key.

    Plain SafeLoader resolves a repeated key to its LAST occurrence and
    drops every earlier one with no error and no warning -- for a file whose
    every surviving entry becomes a permanent dismissal, a silently dropped
    reason block (or a silently dropped `dismiss:` block, if repeated at the
    top level) is the worst available outcome.
    """

    def construct_mapping(self, node, deep=False):
        seen_keys = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen_keys:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen_keys.add(key)
        return super().construct_mapping(node, deep=deep)


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
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise PruneListError(f"that doesn't parse as YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise PruneListError(
            "a prune list is a YAML mapping (key: value lines) -- this isn't one"
        )

    dismiss = data.get("dismiss") or {}
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
            # str, int only -- and never bool, which is a subclass of int in
            # Python, so `- true`/`- false` would otherwise slip through an
            # isinstance(x, int) check. Anything else (null, a mapping, a
            # list) would str() into a garbage id ("None", "{'a': 1}") that
            # is accepted as real and matches no eventernote_event_id --
            # looking exactly like a stale file rather than a malformed one.
            if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
                raise PruneListError(
                    f"dismiss.{reason_key}: {raw_id!r} isn't a usable id "
                    "(expected a string or number)"
                )
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

    if not entries:
        raise PruneListError(
            "no dismissals found -- expected a top-level 'dismiss:' block with "
            "at least one reason and a non-empty list of ids"
        )

    return PruneList(entries=tuple(entries), warnings=tuple(warnings))
