"""Web skeleton smoke tests."""

from fastapi.testclient import TestClient

from app.web.app import create_app


def test_healthz():
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_index_renders():
    client = TestClient(create_app())
    r = client.get("/")
    assert r.status_code == 200
    assert "Deadline tracking" in r.text
    assert "Sign in with Discord" in r.text
