# Non-negotiable invariants

Full text of the eight invariants. Each exists because breaking it shipped (or nearly shipped) a real bug. Read the relevant one in full before touching its area; the one-line summaries in CLAUDE.md are reminders, not substitutes.


1. **Timezones**: the DB stores aware UTC only — the `UTCDateTime`
   TypeDecorator rejects naive datetimes. Forms enter times in JST
   (`parse_jst`/`jst_to_utc`); display is always dual via `fmt_dual`:
   "…JST (…user-tz)". Never store or compare naive datetimes.
2. **Queue sync**: `reminder_queue` is a materialized outbox. Any edit to
   concerts/windows/days/rules must call the relevant `sync_*` function.
   Re-planning is always safe: unsent rows update/delete freely; a deadline
   postponed after its reminder was sent re-arms it (sent_at cleared). Only
   successful DM delivery marks a row sent; discord.Forbidden drops it;
   transient errors retry next tick. Never break these semantics.
   A cancelled `ConcertDay` is never deleted, only flagged —
   `db/service.py`'s `concert_round_rows()` and every `applies_to` consumer
   rely on the day row still existing. Rounds have no status of their own; a round counts
   as cancelled when every day in its `applies_to` is cancelled.
   `RoundOutcome` (per-user, per-round lottery progress) layers a second,
   per-user suppression pass onto the same `sync_rule` candidate-list
   filtering, orthogonal to cancellation — see
   `db/service.py`'s `_apply_outcome_suppression`.
   An UPGRADE round (`RoundKind.UPGRADE`) is a nested second campaign whose
   availability is per-user DERIVED, never stored: a user is eligible only
   when they hold a secured (WON/PAID) ticket in one of the round's
   `round_qualifiers` (an empty qualifier set means any secured ticket on the
   concert, mirroring `applies_to`'s empty-means-all). Eligibility is pure
   (`domain/upgrades.py:is_upgrade_eligible`) and threaded through the same per-user
   seams — `_apply_outcome_suppression` (exempt from the secured-elsewhere
   suppression, then re-suppressed when ineligible), auto-arm, `column_for`,
   and every capture surface — not the pure planner. Editors set the
   qualifier set as chips (`parse_round_qualifiers`); never persist
   eligibility.
3. **Group tag expansion** (agreed with the owner, do not change): attaching
   a GROUP tag to a concert materializes its members AT THAT MOMENT only.
   Editors prune non-performers; removed members stay removed; detach +
   re-attach re-expands; group membership edits never rewrite existing
   concerts. The creation form passes `expand=False` because its explicit
   artist list is authoritative. `POST /concerts/{event_id}/duplicate`
   (`web/routes/concerts.py`) follows the same rule when cloning a concert:
   it re-attaches the source's exact already-pruned tag set with
   `expand=False`, never re-expanding a GROUP tag to its current membership.
4. **Notifications**: new-event notices go through the `notifications`
   table (DB outbox drained by the scheduler) — never send DMs directly
   from web routes. One narrow, explicit exception: `POST /me/test-dm`
   (`web/routes/preferences.py`) sends synchronously and reports the
   result inline — a manual, user-initiated, low-volume diagnostic
   action is a different animal from a system-initiated notice, which
   must still go through the outbox for its retry/ordering/audit
   properties. Don't extend this carve-out to anything else without
   discussing it first.
5. **Auth**: three tiers — admin, editor, user. Admins = `ADMIN_WHITELIST`
   env (Discord IDs), env-only by design (no runtime UI; edit `.env` +
   restart). Editors = `EDITOR_WHITELIST` env (permanent bootstrap/
   break-glass set) OR the `users.is_editor` DB flag, which admins can
   toggle live from the preferences page or `/promote-editor` /
   `/demote-editor` Discord commands. Admins automatically pass editor
   checks too. Sessions are DB-backed sha256 token hashes (revocable).
   Ownership checks 404, not 403, on other users' presets/subscriptions.
   Being SIGNED OUT is not an error: `require_user` raises `LoginRequired`
   (not an HTTPException), and `web/app.py`'s handler sends the visitor to
   `/`, which signed out is the real landing page with the sign-in CTA --
   303, never 307, so a signed-out POST is not replayed against `/`, and
   `HX-Redirect` + 204 for htmx requests, since an XHR would follow a 303
   and swap the whole landing page into a fragment target. Being signed in
   and unauthorized IS an error and stays 403 (`require_editor`/
   `require_admin`) -- don't fold the two together.
   The redirect carries `?next=<path>` so login returns the visitor to the
   page they asked for. Three rules hold it together: only GETs get a
   `next` (a POST body is gone, so replaying its URL renders a form that
   looks like it submitted and didn't); htmx uses `HX-Current-URL`, since
   the fragment endpoint is not somewhere you can stand; and the value
   always passes `domain/urls.py:safe_next`, which reduces it to a
   same-origin path or None (it folds backslashes -- `/\evil.com` reaches
   the network as scheme-relative `//evil.com`). `next` rides to Discord
   in OUR signed session cookie next to `oauth_state`, never as an OAuth
   query param, so it cannot return attacker-controlled; the callback
   re-validates anyway, and a brand-new account still goes to `/welcome`
   regardless. Templates link sign-in via the `login_url(request)` global,
   never a bare `/auth/login` -- miss one CTA and that button silently
   drops the destination the others keep.
   No separate CSRF token: mutating routes rely on `SameSite=Lax` cookies
   (`web/app.py`'s `SessionMiddleware`). Deliberate for an app this size —
   don't read it as a gap to fill or bolt a token system onto.
   Any future personal-secret-link feature (the calendar feed is the first)
   should reuse the same shape: `secrets.token_urlsafe`, only the SHA-256
   hash stored (`User.calendar_token_hash` mirrors `WebSession.token_hash`),
   the raw value shown to the user exactly once and never persisted
   anywhere retrievable — recovery is "generate a new one," not "look up
   the old one."
   Self-serve erasure is `POST /me/delete` (`web/routes/preferences.py`):
   `require_user`-scoped to the caller, revokes the session via the shared
   `revoke_session` helper, then calls `service.delete_user`, behind a
   heavy client-side confirmation naming what is kept vs removed.
6. **`event_id` vs `id`**: every FK targets `Concert.id` (internal PK), but
   URLs use the editor-chosen, unique `event_id` string instead. `"new"` and
   `"import"` are reserved and rejected as `event_id` values so they can
   never collide with the `/concerts/new` and `/concerts/import` routes.
7. **Injection boundaries** -- three rules, each cheap to follow and silent
   when broken:
   - **URLs**: every editor-supplied URL goes through `form_url`
     (`web/forms.py`) at the route boundary; it wraps `domain.urls.clean_url`
     and turns a bad scheme into a 422. Stored URLs land in `href`
     attributes, so a `javascript:` value that slips past executes
     in-origin. The bot layer uses `clean_url` directly, via
     `safe_button_url` in `bot/messages.py` -- never `form_url`, which
     would drag fastapi into the bot.
   - **Inline `<script>` data**: tag names and anything else user-controlled
     that reaches the picker's inline script use `| tojson`, never `| safe`,
     and the context value stays a raw Python object. Hand `tojson` the
     output of `json.dumps` and it double-encodes into a quoted string --
     the picker silently breaks while the escaping tests still pass.
   - **Inline `on*` handlers**: never interpolate user-controlled text into
     one. The browser HTML-decodes the attribute before parsing it as JS, so
     Jinja's `&#39;` escaping does not protect you. Put the value in a
     `data-` attribute and read it via `dataset`. Use `data-tag-name` /
     `data-preset-name`, not `data-name`: that one collides with the shared
     `filterChips()` selector in `base.html`. The i18n build hit the same
     rule with translated `confirm()` text: `onclick="return
     confirm(this.dataset.confirm)"` reading a `data-confirm="{{ _(...) }}"`
     attribute, never `onclick="return confirm('{{ _(...) }}')"` -- a
     translated string is just as user-controlled as a tag name once it can
     contain an apostrophe.
8. **Concert subscriptions are OVERRIDES, not records.** Whether a user
   "tracks" a concert is derived, and `tracked_concert_ids` is the single
   place that derivation lives -- do not add a second. The rule: a concert is
   tracked when a followed tag matches AND no `opted_out` row exists, OR a
   `subscribed` row exists. **No row is the common case** and means "follow
   the tag-derived default" -- so `ConcertSubscription` and `LegOptOut` are
   never backfilled; they hold only explicit user edits, exactly as group-tag
   expansion (invariant 3) materializes members lazily and persists only
   prunes. A prune STICKS across unfollow/re-follow of the tag (removed stays
   removed); Preferences surfaces the otherwise-invisible pruned count. Any
   write to a subscription or leg opt-out re-syncs that user's rules via
   `reinstate_user_rules`, the same resync `record_round_outcome` runs -- skip
   it and a pruned concert keeps reminding. An opt-out suppresses informational
   reminders only; it never deletes a `RoundOutcome`, so opting out of a won
   ticket forfeits the reminder, not the record (the UI gates that with a heavy
   confirmation naming the loss). Per-leg opt-out suppresses a round only when
   EVERY leg in its `applies_to` is opted out -- the per-user analogue of the
   every-leg cancellation rule, folded into `_apply_outcome_suppression`.

