"""
tests/test_rest_api.py
──────────────────────
End-to-end tests for the FastAPI admin API, served against in-memory
fakes (see conftest.py). Covers ops probes, metrics exposition, federation
policy CRUD, consolidation, rollback, stats and error paths — all offline.
"""

from __future__ import annotations


def test_root_metadata(api_client):
    body = api_client.get("/").json()
    assert body["service"] == "AgentMemOS"
    assert "grpc_port" in body


def test_ready_probe(api_client):
    assert api_client.get("/ready").json() == {"ready": True}


def test_metrics_prometheus_exposition(api_client):
    resp = api_client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "# TYPE agentmemos_uptime_seconds counter" in text
    assert "agentmemos_grpc_writes_total" in text


def test_policy_register_then_fetch(api_client):
    payload = {
        "owner_agent_id": "agent-A",
        "allowed_agents": ["agent-B"],
        "public": False,
        "redact_fields": ["ssn"],
    }
    reg = api_client.post("/policy", json=payload)
    assert reg.status_code == 200
    assert reg.json()["owner"] == "agent-A"

    got = api_client.get("/policy/agent-A").json()
    assert got["owner_agent_id"] == "agent-A"
    assert got["allowed_agents"] == ["agent-B"]
    assert got["redact_fields"] == ["ssn"]


def test_get_missing_policy_404(api_client):
    assert api_client.get("/policy/nobody").status_code == 404


def test_consolidate_dry_run_archives_nothing(api_client):
    body = api_client.post("/consolidate", json={"agent_id": "a1", "dry_run": True}).json()
    assert body["episodes_archived"] == 0
    body2 = api_client.post("/consolidate", json={"agent_id": "a1", "dry_run": False}).json()
    assert body2["episodes_archived"] > 0


def test_rollback_unknown_tier_400(api_client):
    resp = api_client.post(
        "/rollback",
        json={"agent_id": "a1", "version_ref": "v1", "tier": "bogus"},
    )
    assert resp.status_code == 400


def test_versions_unknown_tier_400(api_client):
    assert api_client.get("/agents/a1/versions?tier=bogus").status_code == 400


def test_agent_stats_shape(api_client):
    body = api_client.get("/agents/a1/stats").json()
    assert set(body["tiers"]) == {"working", "episodic", "semantic", "procedural"}
