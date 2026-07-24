"""Locale-ordered sentence slots for the reminder-rule builders.

A reminder-rule row reads as a sentence -- "Remind me [3 days] [before] each
[the deadline]." -- where the bracketed pieces are real ``<select>``s. The
English word order is not the Japanese or Chinese one, so the sentence is a
single translatable PATTERN string with ``{name}`` placeholders, and the
translator controls where each control lands:

    en  "Remind me {offset} {direction} {anchor}."
    ja  "{anchor}の{offset}{direction}に通知。"
    zh  "{anchor}{direction}{offset}提醒我。"

``split_slots`` is the pure half: it splits a translated pattern into ordered
("text", literal) / ("slot", name) parts. The web layer's ``sentence_slots``
Jinja global joins those parts with the caller's pre-rendered selects, escaping
the text runs (invariant 7 -- a translator-controlled pattern string must not
be able to inject markup).

Pure by design (domain/): no I/O, no Jinja, no markup -- just string splitting,
so it is unit-testable in isolation.
"""

import re

# Placeholders are ASCII `{name}` even inside CJK patterns (the surrounding
# text is CJK; the slot names stay English identifiers).
_SLOT_RE = re.compile(r"\{(\w+)\}")


def split_slots(pattern: str, slots) -> list[tuple[str, str]]:
    """Split a translated pattern on ``{name}`` placeholders, in order.

    Returns a flat list of parts, each a ``(kind, value)`` tuple:

    - ``("text", literal)`` for a run of literal text between placeholders.
      Adjacent slots (common in CJK, which has no spaces) produce no text part
      between them; an empty pattern produces no parts at all.
    - ``("slot", name)`` for each ``{name}`` placeholder, in the order they
      appear (a name may repeat).

    ``slots`` is the set/collection of KNOWN slot names. An ``{unknown}``
    placeholder raises ``ValueError`` -- a translator typo must fail loudly (in
    the catalogue tests) rather than silently rendering a blank where a control
    was meant to go.
    """
    known = set(slots)
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _SLOT_RE.finditer(pattern):
        if m.start() > pos:
            parts.append(("text", pattern[pos:m.start()]))
        name = m.group(1)
        if name not in known:
            raise ValueError(
                f"unknown slot {name!r} in pattern {pattern!r} "
                f"(known: {sorted(known)})"
            )
        parts.append(("slot", name))
        pos = m.end()
    if pos < len(pattern):
        parts.append(("text", pattern[pos:]))
    return parts
