"""Whitelist parsing — the access-control logic must be bulletproof."""

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
