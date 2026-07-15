"""Whitelist parsing — the access-control logic must be bulletproof."""

from app.config import Settings


def make(whitelist: str) -> Settings:
    return Settings(editor_whitelist=whitelist, _env_file=None)


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
