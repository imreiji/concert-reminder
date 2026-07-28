"""The delivery log, its digest, and the retention prune."""

from app.domain.types import DeliveryOutcome


def test_delivery_outcome_lives_in_domain_types():
    assert DeliveryOutcome.SUCCESS.value == "success"
    assert DeliveryOutcome.FORBIDDEN.value == "forbidden"
    assert DeliveryOutcome.TRANSIENT_FAILURE.value == "transient_failure"


def test_delivery_outcome_is_a_str_enum():
    """Every other enum in this app is a StrEnum, and the DB stores .value
    strings. A plain Enum here would serialise differently."""
    assert isinstance(DeliveryOutcome.SUCCESS, str)


def test_scheduler_reexports_the_same_object():
    """scheduler/loop.py keeps working through the import, so no existing
    caller had to change. If these ever diverge, an `is` comparison in tick()
    silently stops matching."""
    from app.scheduler.loop import DeliveryOutcome as FromScheduler

    assert FromScheduler is DeliveryOutcome
