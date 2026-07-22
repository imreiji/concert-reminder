"""The all-or-nothing rule for locale variants.

A translatable field is filled in every language or none of them. A field
half-translated is worse than one left alone: `loc_field` falls back to the
original when a variant is empty, so a partial fill renders as a silent
mix of languages that nobody notices is wrong -- an English viewer sees
Japanese in the middle of an English page and assumes it is intentional.

Pure by design (domain/): the browser, the route and the templates all
apply the SAME rule, and this is the only place it is written down.
"""

_SLOTS = ("ja", "en", "zh")


def missing_variants(
    base: str, en: str, zh: str, *, mandatory: bool = False
) -> tuple[str, ...]:
    """Which of ja/en/zh are blank but must not be. Empty tuple means fine.

    `base` IS the Japanese value -- there is no `_ja` column; the original
    column is the Japanese side (see i18n.loc_field). Slot order is always
    `("ja", "en", "zh")`, stable across calls, since a caller may interpolate
    the returned slots into a user-facing message.

    Whitespace-only counts as blank -- editors paste, and a stray space must
    not read as "filled".

    A non-mandatory field may be left blank in all three; a mandatory one
    (a concert title, a tag name) may not, because the record cannot be
    rendered without it.
    """
    values = dict(zip(_SLOTS, (base.strip(), en.strip(), zh.strip()), strict=True))
    if not mandatory and not any(values.values()):
        return ()
    return tuple(slot for slot in _SLOTS if not values[slot])
