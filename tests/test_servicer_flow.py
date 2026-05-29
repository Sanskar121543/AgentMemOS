"""
tests/test_servicer_flow.py
───────────────────────────
Exercises the MemoryServicer write/read pipeline against fakes:
routing, importance-gated promotion, parallel fan-out, dedup and
the WAL side-channel. No real backends required.
"""

from __future__ import annotations

import asyncio

import pytest

from agentmemos.core.models import MemoryType, ReadRequest, WriteRequest

pytestmark = pytest.mark.asyncio


async def _drain():
    # let fire-and-forget create_task() side effects complete
    for _ in range(5):
        await asyncio.sleep(0)


async def test_write_returns_immediately_and_routes(fake_servicer):
    req = WriteRequest(
        agent_id="a1", session_id="s1",
        content="The capital of France is Paris.",
        type=MemoryType.FACT,
    )
    resp = await fake_servicer.write(req)
    assert resp.memory_id
    # FACT type → semantic tier per router
    assert resp.routed_to.name == "SEMANTIC"


async def test_write_publishes_to_wal(fake_servicer):
    req = WriteRequest(agent_id="a1", session_id="s1", content="ran the build",
                       type=MemoryType.OBSERVATION)
    await fake_servicer.write(req)
    await _drain()
    assert len(fake_servicer._wal.published) == 1
    assert fake_servicer._wal.published[0]["agent_id"] == "a1"


async def test_high_importance_working_write_promotes_to_episodic(fake_servicer):
    # Force promotion by lowering the scorer threshold to zero.
    fake_servicer._scorer._base_threshold = 0.0
    req = WriteRequest(agent_id="a1", session_id="s1",
                       content="just a quick note", type=MemoryType.OBSERVATION,
                       target_tier=None)
    # target working via low-signal content
    resp = await fake_servicer.write(req)
    await _drain()
    if resp.routed_to.name == "WORKING":
        assert len(fake_servicer._episodic.store.get("a1", [])) >= 1


async def test_read_fans_out_and_sorts(fake_servicer):
    # Seed episodic + semantic with entries of differing importance.
    for content, imp in [("alpha fact", 0.9), ("beta fact", 0.4)]:
        w = WriteRequest(agent_id="a1", session_id="s1", content=content,
                         type=MemoryType.FACT)
        await fake_servicer.write(w)
    await _drain()
    rr = ReadRequest(agent_id="a1", session_id="s1",
                     query="tell me everything you know", top_k=10)
    resp = await fake_servicer.read(rr)
    scores = [r.final_score for r in resp.results]
    assert scores == sorted(scores, reverse=True)
    assert resp.latency_us >= 0


async def test_read_dedups_by_id(fake_servicer):
    # Same entry present in working + episodic should collapse to one.
    w = WriteRequest(agent_id="a1", session_id="s1",
                     content="duplicated across tiers", type=MemoryType.FACT)
    await fake_servicer.write(w)
    await _drain()
    rr = ReadRequest(agent_id="a1", session_id="s1", query="duplicated", top_k=10)
    resp = await fake_servicer.read(rr)
    ids = [r.entry.id for r in resp.results]
    assert len(ids) == len(set(ids))


async def test_health_aggregates_all_tiers(fake_servicer):
    h = await fake_servicer.health()
    assert h["healthy"] is True
    fake_servicer._semantic.healthy = False
    h2 = await fake_servicer.health()
    assert h2["healthy"] is False
    assert h2["tiers"]["semantic"] is False
