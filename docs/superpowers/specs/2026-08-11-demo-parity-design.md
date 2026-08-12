# Demo parity: WISHLIST #8, judged on merit

Date: 2026-08-11
Status: design agreed, ready to plan
Entry: WISHLIST Proposed #8, "Minor demo-parity cosmetics"

## What this is

Entry #8 has been a batch of small demo-parity gaps since 2026-07-20, grown six
times and never worth a branch of its own. Its sixth item (the `.danger` card
frame also styling every danger button) shipped separately earlier today. This
design covers what remains.

The entry's standing assumption was that **the demo is right and the app should
change**. The owner replaced that with **judge each on merit** — the demo stays
the default answer, but where the app moved deliberately or is simply better,
the demo is what changes.

That review is the substance of this document, and it did not leave the entry
where it found it.

## What the merit review found

Of eight things the entry was tracking, **four are not what it said they were**:

| | Entry says | Actually |
|---|---|---|
| Following fold | app should adopt the demo's button | **Moot** — owner wants the whole surface reworked |
| `.signin-note` frame | demo owes a frame | **Dead** — the class was deleted 3 days after filing |
| Split pill | "four rejected shapes, no record of the choice" | **Choice is recorded**, in the 2026-08-01 spec |
| Tags member list | app should truncate to "+N more" | **App is right** — it is an edit surface |
| Setup pick tiles | app should use `<button aria-pressed>` | **App is right** — it works with JS off |

Two of those (the member list, the pick tiles) would have been regressions had
the pass run as filed. That is the case for the owner's ruling, and it is worth
stating plainly: a three-week-old cosmetic list is a set of claims about the
app, and claims decay.

### Following (item 1a) — removed from the entry

Put to the owner as a genuine fork: the demo's footer-bar-plus-dialog is also
what this app's own convention demands ("pickers are native `<dialog>` white
cards", and this is the only picker that is not one), against a `<details>`
fold that is the only shape surviving JS being off. He answered that he wants
to rework how following tags works altogether. So the cosmetic question is
**moot rather than settled** — deciding it now would decide it twice. Filed as
its own unranked entry at the head of Proposed; struck from #8.

### `.signin-note` (item 2) — struck, not built

Filed 2026-07-21. On 2026-07-24 the UX pass absorbed `.signin-note` into the
two-shape callout grammar and the class ceased to exist; the signed-out bounce
is now `<p class="banner banner-block">`. `dekimasen-ux-pass-demo.html` already
carries both the shape (line 724) and the migration map naming the class it
replaced (line 728). **The demo owes nothing.** The entry spent three weeks
asking for a frame for a deleted component.

### Split pill (part of item 4) — a port, not a decision

The entry calls this "worse than a gap, because the next person finds four
rejected shapes and no record of the choice". Half right: the demo carries no
frame, but the choice and its reasoning are in
`docs/superpowers/specs/2026-08-01-character-seiyuu-subunit-design.md`:

> **Merged chip shape: the split pill** (owner, from four mockups). Chosen over
> the inline `如月千早（今井麻美）` form specifically because the merge is
> conditional: when only one end is present the chip is plain, and the split
> shape makes that difference read as meaningful rather than as inconsistent
> styling.

The subunit rail's ruling and its rejected alternative are recorded in the same
place. Nothing to reopen.

## Branch A — app changes

Three changes. Python/Jinja/CSS, with tests. This is the only half that can
break anything.

### A1. `"Auto-apply"` → `"Auto-apply preset"`

`preferences.html:58`. The toggle currently carries its meaning in a `title`
attribute ("Add my default preset to new matching events"), which is invisible
on touch. The demo has said `Auto-apply preset` since it was written.

This is a **msgid change**, so both catalogues need the new key filled by hand
(CLAUDE.md: editing English copy either keeps the msgid byte-identical or
updates both `.po` files). Existing values to carry over and extend:

- ja `自動適用` → `プリセットを自動適用`
- zh `自动应用` → `自动应用预设`

**Measure before accepting.** The label grows by one word inside a `.swb`
sitting in the `.subrow`'s `.sw` span, next to `Notify` and `Unfollow`. On a
phone that row is already tight. Per the measure-don't-reason rule, check the
`.subrow` at 365px and 320px in both English and Japanese before and after, and
report the numbers. If it wraps the row, that is a finding, not a reason to
abandon the change — but it must be seen, not assumed.

### A2. Hoist the new-tag dialog footer

`tags.html`, the `#new-tag-dialog` footer at line 459.

Today the `.df` footer is the last child of `<form class="db">`, which is a
`display: grid` with `padding: 1rem` and `gap: .9rem` — so the footer's
`border-top` stops short of the dialog edges and a grid gap floats it off the
fields. The demo has `.df` as a **sibling** of the body, spanning the dialog.

**Why the app diverged, which the entry never noticed:** the app's footer holds
a real `type="submit"` for the create form, so it has had to sit inside the
`<form>`. The demo's buttons are all `type="button"` fakes and had no such
constraint.

The change: give the form an id, move `.df` out to be a sibling of it, and put
`form="<that id>"` on the submit button. Universally supported; the cost is an
id coupling bought for a visual gain, which the owner accepted explicitly.

The tag EDIT dialog (`tags.html:178`) has the same footer shape. **Apply the
same change to it if its footer also holds a real submit**, so the two dialogs
do not drift apart; if it does not, say so in the commit. What must not happen
is it being left behind *accidentally*.

### A3. `text-wrap: balance` on `.lede h1`

`style.css:1512`.

**Corrected 2026-08-11, after checking rather than asserting.** The claim that
"three sibling `h1` rules already carry it" is wrong. The three selectors
carrying `text-wrap: balance` are `.hero .promise` (3.5rem), `.ctafoot h2`
(1.6rem) and `.chead h1` (1.7rem) — only one is an `h1`. The real pattern is
**large display headings balance their wrap**, not "h1 rules do". Two
consequences:

- A sweep test over every `h1` rule cannot be written honestly: the bare `h1`
  (1.4rem, line 230) is a generic fallback and `.legal h1` is discussed below.
  Pin the specific set instead.
- **`.legal h1` (line 98) is 1.7rem and also lacks it** — the same size as
  `.chead h1`, which has it. So `.lede h1` may not be the only oversight.
  **This is raised for the owner, not decided here**: including it is a
  one-word widening of an approved change, and excluding it leaves the set
  looking arbitrary to whoever reads the test. Ask before implementing; the
  plan's task carries both variants.

Worth more than it looks: `.lede h1` is the **error pages' heading** — so this
one word visibly improves all four of the frames Branch B is about to draw.

## Branch B — demo frames

Six items, nine frames (B3 is four on its own). HTML only, no app code, no
tests. Ports of shipped markup into
the demos' house style, which is **high fidelity**: real CSS on the real
tokens, real Japanese content, not wireframes.

Into `dekimasen-demo.html`:

- **B1 (was 1c)** — Tags edit dialog: drop the `+5 more` truncation, render the
  full member list with each chip's delete `×`. *The demo is wrong here:* this
  is the edit dialog, every chip is removable, and an inert "+5 more" with no
  way to expand makes five of nine members unreachable on the one surface built
  for reaching them. Truncation is right for a *display* of members; this is not
  one.
- **B2 (was 1e)** — Setup pick tiles: `<button aria-pressed>` becomes a label
  wrapping a visually-hidden checkbox, **with a comment saying why**. They
  render identically; the app's version submits with JS disabled and the demo's
  cannot, since `aria-pressed` needs a script to track state and a hidden input
  to carry it. Screen readers handle both, announcing "checked" rather than
  "pressed". The entry filed this as an accessibility gap; measured against what
  each actually does, the app's is the more robust.
- **B3 (item 3)** — **four error-page frames, one per code** (403/404/422/500),
  each with its real copy. One template, four sets of copy: 404/403/500 are the
  bare shape, and 422 adds the `.banner warn` message list plus a second button
  ("Go back and fix it", a real button because there is nothing to bookmark).
  403 carries the most interesting copy — it is almost always the wrong Discord
  account, which nothing else on the page would tell you.
- **B4 (item 4)** — the split pill (`.mchip`, a character and her seiyuu as one
  two-halved element) on the concert page, and the subunit rail (`.pcluster.sub`
  there, `.grow2.sub` on the Tags page). Port the shipped shape; the design
  ruling is already recorded and is quoted above.
- **B5 (item 5)** — the editor round card's "Requires item from" select row, and
  the concert page's "🛍️ Requires: {label}" / "Needed for: {labels}" lines. The
  select is the interesting one: it is the first control on a card that **hides
  itself** when no item-sale round exists, so the frame has to decide how to
  depict a conditional control. Show it present, with a caption naming the
  condition.

Into `dekimasen-onboarding-demo.html`, which already owns import preview:

- **B6 (item 6)** — the per-round evidence block (`.edgecard ok`, "Read from the
  ticket page:"), the rejection callout (`.banner warn`) above the rounds
  section, and the "Fill rounds from a page I paste" fold. Better-behaved than
  the others: all three compose the existing two-shape callout grammar rather
  than inventing a shape, so what is owed is a frame showing them in place, not
  a design to reconstruct.

`/admin/fetch-domains` is deliberately **not** on this list: admin pages have
never had demo frames, exactly as they have never been translated.

## Testing

Branch A only. Each test is named against the mutation it must fail.

- **A1** — no new test. `test_i18n_catalogues.py` already fails on an
  untranslated msgid, so a changed English string that does not reach both `.po`
  files is caught by construction. The layout measurement is a reported number,
  not an assertion.
- **A2** — a route test that POSTs the new-tag form and asserts the tag is
  created. **Mutation it must catch: dropping the `form=` attribute**, which
  silently makes the submit button inert. This is the one change here that can
  break a working feature, and it is exactly the failure a "does the page
  render" test sails past.
- **A3** — a pinned-set test in `test_theme_and_tokens.py`: the named display
  headings each carry `text-wrap: balance`. **Not** a sweep over every `h1`, for
  the reason recorded above. **Mutation: removing it from any one selector in
  the set**, including the three that already had it.

Branch B gets no tests. It is documentation.

## What gets recorded

- **WISHLIST**: #8 moves to Shipped with the date, noting it shrank twice on the
  way out — once when the Following cosmetic became its own entry, once when
  item 2 turned out to be dead — and that two of its remaining items reversed
  direction on merit. Then the standard full revision pass over what is left.
- **`docs/architecture.md`**: the known-gap note at lines 1140-1142 names two
  gaps, and this design closes both — the `.signin-note` half because it was
  never really a gap, the error-page half because B3 draws the frames. Nothing
  remains, so **the note is deleted**, not edited.
- **`docs/ui-conventions.md`**: no change needed — the `.danger-card` lesson
  landed there already.

## Out of scope

- The Following rework. Filed separately, unranked, needs its own brainstorm.
- Any re-litigation of the split pill or subunit rail shapes.
- Admin-page demo frames.
