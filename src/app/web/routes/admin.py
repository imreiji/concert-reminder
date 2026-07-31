"""Admin-only operational surfaces: the delivery log, and the broadcast.

  GET  /admin/deliveries               recent failures + the batch list
  GET  /admin/deliveries/{batch_iso}   one batch expanded to its recipients
  GET  /admin/broadcast                compose a broadcast
  POST /admin/broadcast/preview        resolve and render it -- writes NOTHING
  POST /admin/broadcast/send           queue it HELD, then redirect to status
  GET  /admin/broadcast/{id}           status: what is still held, what went
  POST /admin/broadcast/{id}/cancel    delete the rows that have not gone yet

This is the ONLY surface that names delivery recipients. The digest DM
deliberately reports counts, because a name in Discord history is a permanent
record of who follows which artists that POST /me/delete cannot reach; here it
sits behind require_admin, inside the app's own deletion story, on the 30-day
retention window.

Copy is English-only and NOT wrapped in _(), following /me/test-dm: an
operational page only admins see should not cost msgids in three languages
(tests/test_i18n_catalogues.py would enforce them).
"""

import io
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Broadcast, Notification
from app.db.service import (
    BROADCAST_BODY_MAX,
    DELIVERY_LOG_RETENTION_DAYS,
    TYPED_CONFIRM_THRESHOLD,
    ImportChoices,
    apply_tag_import,
    cancel_broadcast,
    catalogue_export_files,
    current_tag_exports,
    delivery_batch_rows,
    delivery_batches,
    delivery_failures,
    duplicate_body_recently,
    ensure_user,
    queue_broadcast,
    recent_broadcasts,
    resolve_recipients,
)
from app.db.session import get_session
from app.domain.tags_diff import plan_tag_import
from app.domain.tags_yaml import TagsFileError, parse_tags
from app.domain.types import BroadcastMode
from app.web.auth import SessionUser, require_admin
from app.web.routes.imports import MAX_DRAFT_CHARS

router = APIRouter()

templates = None  # set by web.app at startup


async def _context(session: AsyncSession, user: SessionUser, batch: datetime | None) -> dict:
    """The whole page in one dict. Both routes render the same template: the
    failure list and the batch list are the page, and opening a batch only
    appends a third section rather than navigating away from them."""
    return {
        "user": user,
        "failures": await delivery_failures(session),
        "batches": await delivery_batches(session),
        "rows": await delivery_batch_rows(session, batch) if batch else None,
        "batch": batch,
        "retention_days": DELIVERY_LOG_RETENTION_DAYS,
    }


@router.get("/admin/deliveries", response_class=HTMLResponse)
async def deliveries(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    return templates.TemplateResponse(
        request, "admin_deliveries.html", await _context(session, user, None)
    )


@router.get("/admin/deliveries/{batch_iso}", response_class=HTMLResponse)
async def delivery_batch(
    request: Request,
    batch_iso: str,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    try:
        batch = datetime.fromisoformat(batch_iso)
    except ValueError:
        # A malformed timestamp is a bad link, not a server fault.
        raise HTTPException(status_code=404, detail="no such batch") from None
    return templates.TemplateResponse(
        request, "admin_deliveries.html", await _context(session, user, batch)
    )


@router.get("/admin/broadcast", response_class=HTMLResponse)
async def broadcast_compose(
    request: Request,
    mode: str | None = None,
    mode_param: str | None = None,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """`mode`/`mode_param` are an optional PREFILL, nothing more -- they are how
    /admin/deliveries hands a batch over ("message these recipients") without
    the admin retyping an ISO timestamp. Both stay optional: a bare
    GET /admin/broadcast is the normal entry point and must still render. An
    unrecognised mode is simply not prefilled rather than a 422 -- this is a
    form default, and the preview re-validates it anyway.
    """
    if mode not in {m.value for m in BroadcastMode}:
        mode = None
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "preview": None,
            "status": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "prefill": {"mode": mode, "mode_param": mode_param or ""},
        },
    )


@router.post("/admin/broadcast/preview", response_class=HTMLResponse)
async def broadcast_preview(
    request: Request,
    mode: str = Form(...),
    mode_param: str = Form(""),
    body: str = Form(...),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Resolves and renders. Writes NOTHING -- the outbox is untouched until
    the admin confirms from this screen.

    No try/except around resolve_recipients: it is total by construction (a
    malformed batch timestamp comes back as zero recipients with the offending
    text in `unmatched`), so there is no failure mode here to translate.
    """
    try:
        chosen = BroadcastMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown broadcast mode") from None
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="broadcast body is empty")
    if len(text) > BROADCAST_BODY_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"body exceeds {BROADCAST_BODY_MAX} characters",
        )

    recipients = await resolve_recipients(session, chosen, mode_param or None)
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "status": None,
            "prefill": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "preview": {
                "mode": chosen.value,
                "mode_param": mode_param,
                "body": text,
                "count": len(recipients.ids),
                "unmatched": recipients.unmatched,
                "needs_typed_confirm": len(recipients.ids) > TYPED_CONFIRM_THRESHOLD,
                "duplicate": await duplicate_body_recently(session, text),
            },
        },
    )


@router.post("/admin/broadcast/send")
async def broadcast_send(
    mode: str = Form(...),
    mode_param: str = Form(""),
    body: str = Form(...),
    confirm_count: str = Form(""),
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """Re-resolves rather than trusting a snapshot from the preview form:
    tampering is not the threat (EXPLICIT already accepts arbitrary ids), drift
    is, and recipient_count must record what was actually queued.
    """
    try:
        chosen = BroadcastMode(mode)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown broadcast mode") from None

    recipients = await resolve_recipients(session, chosen, mode_param or None)
    count = len(recipients.ids)
    if count > TYPED_CONFIRM_THRESHOLD and confirm_count.strip() != str(count):
        # An absent confirm_count is as wrong as an incorrect one -- "" never
        # equals a count, so the empty default falls through this same branch.
        raise HTTPException(
            status_code=422,
            detail=f"type {count} to confirm sending to {count} people",
        )

    try:
        broadcast = await queue_broadcast(session, user.id, chosen, mode_param or None, body)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    await session.commit()
    return RedirectResponse(f"/admin/broadcast/{broadcast.id}", status_code=303)


@router.get("/admin/broadcast/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_status(
    request: Request,
    broadcast_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise HTTPException(status_code=404, detail="no such broadcast")
    pending = (
        await session.execute(
            select(func.count(Notification.id)).where(
                Notification.broadcast_id == broadcast_id,
                Notification.sent_at_utc.is_(None),
            )
        )
    ).scalar_one()
    return templates.TemplateResponse(
        request,
        "admin_broadcast.html",
        {
            "user": user,
            "past": await recent_broadcasts(session),
            "preview": None,
            "prefill": None,
            "body_max": BROADCAST_BODY_MAX,
            "bot_enabled": settings.bot_enabled,
            "status": {"broadcast": broadcast, "pending": pending},
        },
    )


@router.post("/admin/broadcast/{broadcast_id}/cancel")
async def broadcast_cancel(
    broadcast_id: int,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    if await session.get(Broadcast, broadcast_id) is None:
        raise HTTPException(status_code=404, detail="no such broadcast")
    await cancel_broadcast(session, broadcast_id)
    await session.commit()
    return RedirectResponse(f"/admin/broadcast/{broadcast_id}", status_code=303)


# ── Catalogue import ─────────────────────────────────────────────────────


def _import_page(request, user, text, *, plan=None, report=None, error=None):
    return templates.TemplateResponse(
        request,
        "admin_import_tags.html",
        {"user": user, "text": text, "plan": plan, "report": report, "error": error},
    )


def _read_choices(form) -> ImportChoices:
    """Pull the operator's decisions out of the posted form.

    Names are `conflict__<handle>__<field>` and `member__<handle>__<member>`;
    handles are [a-z0-9_-] by construction, so they need no escaping. Values are
    checked against the literal sets and ANYTHING ELSE IS DISCARDED -- the
    browser supplies a decision, never data, so an unexpected value can only
    ever mean "keep mine".
    """
    choices = ImportChoices()
    for key, value in form.multi_items():
        parts = key.split("__")
        if len(parts) != 3:
            continue
        kind, handle, name = parts
        if kind == "conflict" and value in ("mine", "theirs"):
            choices.fields[(handle, name)] = value
        elif kind == "member" and value in ("add", "remove"):
            choices.members[(handle, name)] = value
    return choices


async def _plan_or_error(session, text):
    """(plan, error). Shared by both halves so they cannot disagree about what
    a bad file is."""
    if len(text) > MAX_DRAFT_CHARS:
        return None, "that file is too large -- pastes are capped at 200k characters"
    try:
        parsed = parse_tags(text)
    except TagsFileError as exc:
        return None, str(exc)
    plan = plan_tag_import(parsed.tags, await current_tag_exports(session), parsed.warnings)
    return plan, None


@router.get("/admin/import/tags", response_class=HTMLResponse)
async def import_tags_form(
    request: Request,
    user: SessionUser = Depends(require_admin),
):
    """Paste a tags.yaml from a catalogue export.

    English-only and NOT wrapped in _(), like every other admin surface. Only
    the Preferences LINK to this page is translated.
    """
    return _import_page(request, user, "")


@router.post("/admin/import/tags", response_class=HTMLResponse)
async def import_tags_preview(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    text: str = Form(""),
):
    """Show what the import WOULD do. Writes nothing -- looking is not doing."""
    plan, error = await _plan_or_error(session, text)
    return _import_page(request, user, text, plan=plan, error=error)


@router.post("/admin/import/tags/apply", response_class=HTMLResponse)
async def import_tags_apply(
    request: Request,
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    text: str = Form(""),
):
    """Commit, resolved by the choices in the form.

    RE-PARSES and RE-PLANS from the pasted file rather than trusting anything
    the browser says about content. The form carries decisions only, so a forged
    post cannot inject a value that was not in the file -- and a conflict that
    vanished since the preview, because the catalogue changed underneath, simply
    is not applied.

    One transaction: a file that raises leaves nothing behind.
    """
    plan, error = await _plan_or_error(session, text)
    if error:
        return _import_page(request, user, text, error=error)
    choices = _read_choices(await request.form())
    await ensure_user(session, user.id, user.username)
    report = await apply_tag_import(session, plan, choices, created_by=user.id)
    await session.commit()
    return _import_page(request, user, text, report=report)


@router.get("/admin/export.zip")
async def export_zip(
    user: SessionUser = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
):
    """The whole catalogue, zipped at request time.

    Same shape as GET /concerts/import/skill.zip: a committed binary would go
    stale the moment the data changed, and a few hundred KB of YAML is not worth
    a thread hop.

    Every entry goes through an EXPLICIT ZipInfo pinned to the 1980
    reproducible-build epoch. ZipFile.writestr otherwise stamps the current
    time, and zip timestamps have TWO-SECOND resolution -- so two exports
    seconds apart would differ in bytes while every file inside was identical,
    quietly costing the archive its diffability, which is most of a backup's
    value. Measured, not assumed.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in await catalogue_export_files(session):
            zf.writestr(zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0)), text)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="dekimasen-catalogue.zip"'},
    )
