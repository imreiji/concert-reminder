"""Whitelist parsing — the access-control logic must be bulletproof."""

import pytest

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


# ── Session secret safety ────────────────────────────────────────────────
# The check is deliberately https-only: an https BASE_URL is the one signal
# that says "this is production, fail closed". Local dev keeps the default.

STRONG = "a" * 32


def make_web(base_url: str, secret: str) -> Settings:
    return Settings(base_url=base_url, session_secret=secret, _env_file=None)


@pytest.mark.parametrize(
    "secret",
    ["change-me", "", "   ", "a" * 31],
    ids=["placeholder", "empty", "whitespace", "too-short"],
)
def test_https_rejects_unsafe_secret(secret):
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        make_web("https://dekimasen.app", secret)


def test_https_accepts_strong_secret():
    s = make_web("https://dekimasen.app", STRONG)
    assert s.session_secret == STRONG


def test_error_names_the_generation_command():
    with pytest.raises(ValueError) as exc:
        make_web("https://dekimasen.app", "change-me")
    msg = str(exc.value)
    assert 'python -c "import secrets; print(secrets.token_hex(32))"' in msg
    assert msg.isascii()  # owner's Windows box is GBK; non-ASCII crashes it


def test_local_dev_keeps_working_with_the_default():
    # CLAUDE.md documents running web-only against http://localhost:8000.
    s = make_web("http://localhost:8000", "change-me")
    assert s.session_secret == "change-me"
