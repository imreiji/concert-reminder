"""The `ok` regression test is the important one here: UptimeRobot keyword-
matches '"ok":true', so that field has an external consumer and changing its
meaning silently would defeat the point of this work."""
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.web.app import create_app


def test_healthz_reports_every_check():
    client = TestClient(create_app())
    body = client.get("/healthz").json()
    assert set(body["checks"]) == {"backup", "disk", "scheduler", "dms"}
    assert set(body["checks"]["backup"]) == {"ok", "detail"}


def test_healthz_ok_still_tracks_scheduler_only(monkeypatch):
    """A failing backup must NOT flip `ok`, or the existing uptime monitor
    starts meaning something different without anyone deciding that."""
    from app import ops

    monkeypatch.setattr(ops.settings, "backup_marker_path", "/nonexistent/marker")
    # Past the startup grace, or a missing marker reports healthy by design.
    monkeypatch.setattr(ops, "_started_at", datetime.now(UTC) - timedelta(hours=40))
    client = TestClient(create_app())
    body = client.get("/healthz").json()
    assert body["checks"]["backup"]["ok"] is False
    assert body["ok"] == body["scheduler_ok"]


def test_healthz_survives_a_check_that_raises(monkeypatch):
    """/healthz must stay answerable even when a check is broken -- a 500 here
    would read to the uptime monitor as a full outage."""
    from app import ops

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(
        ops, "REGISTRY", [ops.RegistryEntry("disk", boom, alerting=True)]
    )
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["checks"]["disk"]["ok"] is False
