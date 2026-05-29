"""Smoke test for the REST /health endpoint (runs offline via fakes)."""

from __future__ import annotations


def test_health_ok(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["healthy"] is True
    assert body["version"] == "1.0.0"
    assert set(body["tiers"]) == {"working", "episodic", "semantic", "procedural"}


def test_health_degraded_returns_503(api_client, fake_servicer):
    fake_servicer._working.healthy = False
    resp = api_client.get("/health")
    assert resp.status_code == 503
