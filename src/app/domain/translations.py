"""The all-or-nothing rule for locale variants.

A translatable field is filled in every language or none of them. A field
half-translated is worse than one left alone: `loc_field` falls back to the
original when a variant is empty, so a partial fill renders as a silent
mix of languages that nobody notices is wrong -- an English viewer sees
Japanese in the middle of an English page and assumes it is intentional.

Pure by design (domain/): the browser, the route and the templates all
apply the SAME rule.

It is written down TWICE, deliberately. `missingVariants` in
`web/templates/_variant_guard.html` re-implements `missing_variants` below
in JavaScript, because the browser has to block the submit before it
happens -- a 422 here navigates the editor to a raw JSON body and loses
everything they typed. The duplication is accepted; going unnoticed is not.
A change to either side is a change to both, and that file names this one
in return.
"""

_SLOTS = ("ja", "en", "zh")

# Language names are deliberately NOT _()-wrapped: this project never
# translates them (see the UI conventions in CLAUDE.md) -- someone reading an
# error about a missing 中文 value has to recognise the language before they
# can read anything else. Public (not `_SLOT_LABEL`) because both
# `web/forms.py` (the create-boundary 422) and `db/service.py` (the edit
# page's "what's missing" notice) import it -- a single source so the two
# surfaces can never name the same missing language differently.
SLOT_LABEL = {"ja": "日本語", "en": "English", "zh": "中文"}
assert tuple(SLOT_LABEL) == _SLOTS


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
