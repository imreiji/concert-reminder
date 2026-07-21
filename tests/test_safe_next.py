"""safe_next: the open-redirect guard on the post-login return path.

The value reaches a Location header, so everything here is about refusing
targets that leave the origin. It never raises -- a bad `next` is a stale
bookmark or a hostile link, not an editor mistake worth a 422.
"""

import pytest

from app.domain.urls import safe_next


@pytest.mark.parametrize("raw", [
    "/preferences",
    "/concerts/aqours-9th",
    "/discover?tag=3&status=open",
    "/setup/applications",
])
def test_same_origin_paths_survive(raw):
    assert safe_next(raw) == raw


def test_fragment_is_dropped():
    """The server never sees a fragment, so there is nothing to carry."""
    assert safe_next("/discover#tail") == "/discover"


@pytest.mark.parametrize("raw", [
    "https://evil.com/phish",           # absolute, off-origin
    "//evil.com/phish",                 # scheme-relative
    "/\\evil.com",                      # browsers fold \ to / -> //evil.com
    "\\/evil.com",                      # same trick, leading backslash
    "javascript:alert(1)",              # not a path at all
    "discover",                         # bare relative segment
    "",
    None,
])
def test_off_origin_and_junk_are_refused(raw):
    assert safe_next(raw) is None


def test_control_characters_cannot_smuggle_a_scheme():
    """C0 controls are deleted from the interior, so a split scheme cannot
    reassemble past the check -- same rule clean_url follows."""
    assert safe_next("/\x00\\evil.com") is None
    assert safe_next("java\tscript:alert(1)") is None


def test_overlong_target_is_dropped_not_truncated():
    """Half a path is not a better destination than the default."""
    assert safe_next("/" + "a" * 600) is None
