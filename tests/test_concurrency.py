"""
tests/test_concurrency.py
─────────────────────────
Concurrency / race tests. The SemanticLRUCache and the async write path
are exercised under parallelism to catch capacity-bound and interleaving
bugs that single-threaded tests miss.
"""

from __future__ import annotations

import asyncio

import pytest

from agentmemos.core.models import MemoryEntry, MemoryTier, MemoryType, WriteRequest
from agentmemos.eviction.semantic_lru import SemanticLRUCache


def _entry(i: int) -> MemoryEntry:
    return MemoryEntry(
        agent_id="a", session_id="s", content=f"item-{i}",
        type=MemoryType.OBSERVATION, tier=MemoryTier.WORKING,
    )


def test_cache_never_exceeds_capacity_under_churn():
    cache = SemanticLRUCache(capacity=50)
    total_ghosts = 0
    for i in range(500):
        ghosts = cache.put(_entry(i), in_degree=i % 7)
        total_ghosts += len(ghosts)
        assert len(cache) <= 50
    # everything that didn't fit must have been evicted exactly once
    assert total_ghosts == 500 - 50


def test_high_centrality_entries_survive_eviction():
    cache = SemanticLRUCache(capacity=10)
    # one very central entry inserted first
    central = _entry(9999)
    cache.put(central, in_degree=1000)
    for i in range(100):
        cache.put(_entry(i), in_degree=0)
    assert central.id in cache  # protected by centrality score


@pytest.mark.asyncio
async def test_parallel_writes_all_land(fake_servicer):
    async def one(i):
        return await fake_servicer.write(
            WriteRequest(agent_id="a", session_id="s",
                         content=f"concurrent fact {i}", type=MemoryType.FACT)
        )

    results = await asyncio.gather(*[one(i) for i in range(40)])
    # let fire-and-forget writes settle
    for _ in range(10):
        await asyncio.sleep(0)
    ids = {r.memory_id for r in results}
    assert len(ids) == 40  # no id collisions under concurrency
    assert len(fake_servicer._semantic.store.get("a", [])) == 40
