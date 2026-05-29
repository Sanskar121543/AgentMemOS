"""
tests/test_memory_pipeline.py
─────────────────────────────
Integration smoke test for the admin API request pipeline, run offline
against in-memory fakes (see conftest.py). Asserts the full middleware →
route → servicer path works end to end.
"""

from __future__ import annotations


def test_pipeline_health(api_client):
    assert api_client.get("/health").status_code == 200


def test_pipeline_policy_roundtrip(api_client):
    api_client.post("/policy", json={"owner_agent_id": "p1", "public": True})
    assert api_client.get("/policy/p1").json()["public"] is True
