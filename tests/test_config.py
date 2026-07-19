"""Whitelist parsing — the access-control logic must be bulletproof."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def make(whitelist: str) -> Settings:
    return Settings(editor_whitelist=whitelist, _env_file=None)


def make_admin(whitelist: str) -> Settings:
    return Settings(admin_whitelist=whitelist, _env_file=None)


def test_parses_ids():
    s = make("123, 456,789")
    assert s.editor_ids == frozenset({123, 456, 789})


def test_ignores_garbage_entries():
    s = make("123, not-an-id, , 456xyz, 789")
    assert s.editor_ids == frozenset({123, 789})


def test_empty_whitelist_means_nobody_edits():
    s = make("")
    assert s.editor_ids == frozenset()
    assert not s.is_editor(123)


def test_is_editor():
    s = make("42")
    assert s.is_editor(42)
    assert not s.is_editor(43)


# ── Admin whitelist (same parsing logic, separate field) ─────────────────


def test_admin_parses_ids():
    s = make_admin("123, 456,789")
    assert s.admin_ids == frozenset({123, 456, 789})


def test_admin_ignores_garbage_entries():
    s = make_admin("123, not-an-id, , 456xyz, 789")
    assert s.admin_ids == frozenset({123, 789})


def test_empty_admin_whitelist_means_nobody_admins():
    s = make_admin("")
    assert s.admin_ids == frozenset()
    assert not s.is_admin(123)


def test_is_admin():
    s = make_admin("42")
    assert s.is_admin(42)
    assert not s.is_admin(43)


# ── Session-secret startup validation ────────────────────────────────────
#
# These construct Settings() directly with _env_file=None: going through the
# lru_cache'd get_settings() (or the module-level `settings` singleton) would
# make the results depend on import order and on whoever primed the cache.

GOOD = "a" * 32


def make_settings(base_url: str, session_secret: str) -> Settings:
    return Settings(base_url=base_url, session_secret=session_secret, _env_file=None)


@pytest.mark.parametrize("secret", ["change-me", "", "   ", "x" * 31])
def test_https_rejects_unsafe_secret(secret):
    with pytest.raises(ValidationError) as exc:
        make_settings("https://dekimasen.app", secret)
    # The message must tell the operator exactly how to fix it.
    assert "secrets.token_hex(32)" in str(exc.value)


def test_https_accepts_strong_secret():
    s = make_settings("https://dekimasen.app", GOOD)
    assert s.session_secret == GOOD


def test_https_scheme_check_is_case_insensitive():
    with pytest.raises(ValidationError):
        make_settings("HTTPS://dekimasen.app", "change-me")


def test_local_http_dev_keeps_the_default_secret():
    """CLAUDE.md documents web-only local dev as a first-class workflow;
    the check must not fire there or a fresh clone can't be run at all."""
    s = make_settings("http://localhost:8000", "change-me")
    assert s.session_secret == "change-me"
