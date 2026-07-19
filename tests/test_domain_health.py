"""Pure health logic. No filesystem, no DB -- that is the point of the split:
the transition machine is the highest-risk code here and the hardest to
exercise through real I/O."""
from datetime import UTC, datetime, timedelta

from app.domain.health import (
    StoredState,
    backup_is_stale,
    disk_is_low,
    should_alert,
)

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def test_backup_stale_when_marker_missing():
    assert backup_is_stale(None, NOW, timedelta(hours=36)) is True


def test_backup_fresh_within_window():
    assert backup_is_stale(NOW - timedelta(hours=20), NOW, timedelta(hours=36)) is False


def test_backup_stale_past_window():
    assert backup_is_stale(NOW - timedelta(hours=37), NOW, timedelta(hours=36)) is True


def test_disk_low_by_ratio():
    # 20GB disk, 1.5GB free: over the 1GB floor but under 10%.
    assert disk_is_low(free_bytes=1_500_000_000, total_bytes=20_000_000_000) is True


def test_disk_low_by_absolute_floor():
    # 4TB disk, 800MB free: 0.02% ratio is fine on a huge disk, the floor is not.
    assert disk_is_low(free_bytes=800_000_000, total_bytes=4_000_000_000_000) is True


def test_disk_ok_when_both_satisfied():
    assert disk_is_low(free_bytes=5_000_000_000, total_bytes=20_000_000_000) is False


def test_disk_hysteresis_holds_low_between_the_two_thresholds():
    """12% free on a 20GB disk: past the 10% trip, short of the 15% clear."""
    between = dict(free_bytes=2_400_000_000, total_bytes=20_000_000_000)
    assert disk_is_low(**between, currently_low=False) is False
    assert disk_is_low(**between, currently_low=True) is True


def test_disk_hysteresis_clears_once_headroom_is_real():
    assert disk_is_low(
        free_bytes=3_200_000_000, total_bytes=20_000_000_000, currently_low=True
    ) is False


def test_disk_hysteresis_also_applies_to_the_absolute_floor():
    """1.2GB free on an 8GB disk: 15% clears both ratio thresholds, so only
    the byte floor is in play -- over the 1GB trip, under the 1.5GB clear."""
    between = dict(free_bytes=1_200_000_000, total_bytes=8_000_000_000)
    assert disk_is_low(**between, currently_low=False) is False
    assert disk_is_low(**between, currently_low=True) is True


def test_an_oscillating_check_would_alert_in_neither_direction():
    """Why hysteresis exists at all. F,T,F,T against a healthy confirmed state
    never produces two agreeing observations, so should_alert's anti-flap rule
    keeps the confirmed state pinned and nothing ever fires. Documented as a
    property of the machine so nobody 'fixes' the thresholds back."""
    state = should_alert(None, observed_ok=True, now=NOW).state
    for i, observed in enumerate([False, True, False, True, False, True]):
        decision = should_alert(state, observed, NOW + timedelta(minutes=5 * (i + 1)))
        assert decision.notify is False
        state = decision.state
    assert state.ok is True  # never moved, despite three failing sightings


def test_first_observation_healthy_is_adopted_silently():
    d = should_alert(None, observed_ok=True, now=NOW)
    assert d.notify is False
    assert d.state.ok is True


def test_first_observation_failing_waits_for_confirmation():
    d = should_alert(None, observed_ok=False, now=NOW)
    assert d.notify is False
    assert d.state.ok is None          # nothing confirmed yet
    assert d.state.pending_ok is False


def test_first_observation_failing_alerts_once_confirmed():
    first = should_alert(None, observed_ok=False, now=NOW)
    second = should_alert(first.state, observed_ok=False, now=NOW + timedelta(minutes=5))
    assert second.notify is True
    assert second.state.ok is False


def test_single_blip_does_not_alert():
    healthy = should_alert(None, observed_ok=True, now=NOW)
    blip = should_alert(healthy.state, observed_ok=False, now=NOW + timedelta(minutes=5))
    recovered = should_alert(blip.state, observed_ok=True, now=NOW + timedelta(minutes=10))
    assert blip.notify is False
    assert recovered.notify is False
    assert recovered.state.ok is True


def test_recovery_alerts_once_confirmed():
    broken = StoredState(ok=False, changed_at=NOW, last_notified_at=NOW,
                         pending_ok=None, pending_since=None)
    first = should_alert(broken, observed_ok=True, now=NOW + timedelta(minutes=5))
    second = should_alert(first.state, observed_ok=True, now=NOW + timedelta(minutes=10))
    assert first.notify is False
    assert second.notify is True
    assert second.state.ok is True


def test_still_broken_realerts_after_24h():
    broken = StoredState(ok=False, changed_at=NOW, last_notified_at=NOW,
                         pending_ok=None, pending_since=None)
    early = should_alert(broken, observed_ok=False, now=NOW + timedelta(hours=23))
    late = should_alert(broken, observed_ok=False, now=NOW + timedelta(hours=25))
    assert early.notify is False
    assert late.notify is True
    assert late.state.last_notified_at == NOW + timedelta(hours=25)
