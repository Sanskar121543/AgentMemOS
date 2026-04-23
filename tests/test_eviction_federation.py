"""
tests/test_eviction_federation.py
──────────────────────────────────
Tests for SemanticLRUCache and FederationPolicyEngine.
No external deps — all in-process.

Run with:  pytest tests/test_eviction_federation.py -v
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from agentmemos.core.models import (
    FederationPolicy,
    MemoryEntry,
    MemoryTier,
    MemoryType,
    RankedMemory,
)
from agentmemos.eviction.semantic_lru import (
    SemanticLRUCache,
    EvictionScore,
    score_entry,
    _recency_score,
    _centrality_score,
)
from agentmemos.federation.policy import (
    FederationPolicyEngine,
    PolicyStore,
    PolicyDecision,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_entry(agent_id: str = "a1", content: str = "test") -> MemoryEntry:
    return MemoryEntry(
        agent_id=agent_id,
        session_id="s1",
        content=content,
        type=MemoryType.OBSERVATION,
        tier=MemoryTier.WORKING,
    )


def make_ranked(entry: MemoryEntry, score: float = 0.8) -> RankedMemory:
    return RankedMemory(
        entry=entry,
        relevance=score,
        recency=score,
        final_score=score,
        source_tier=entry.tier,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Semantic LRU — scoring functions
# ─────────────────────────────────────────────────────────────────────────────

class TestEvictionScoring:
    def test_fresh_access_high_recency(self):
        score = _recency_score(time.time())
        assert score > 0.99

    def test_old_access_low_recency(self):
        score = _recency_score(time.time() - 7 * 86400)  # 7 days ago
        assert score < 0.05

    def test_zero_degree_returns_zero(self):
        assert _centrality_score(0) == 0.0

    def test_high_degree_saturates(self):
        s30 = _centrality_score(30)
        s100 = _centrality_score(100)
        assert s30 <= s100 <= 1.0

    def test_score_entry_returns_eviction_score(self):
        entry = make_entry()
        ev = score_entry(entry, last_accessed=time.time(), in_degree=5)
        assert isinstance(ev, EvictionScore)
        assert 0.0 <= ev.combined <= 1.0

    def test_high_degree_raises_combined_score(self):
        entry = make_entry()
        low  = score_entry(entry, in_degree=0)
        high = score_entry(entry, in_degree=20)
        assert high.combined > low.combined


# ─────────────────────────────────────────────────────────────────────────────
# SemanticLRUCache
# ─────────────────────────────────────────────────────────────────────────────

class TestSemanticLRUCache:
    def test_put_and_get(self):
        cache = SemanticLRUCache(capacity=10)
        e = make_entry()
        cache.put(e)
        assert cache.get(e.id) is e

    def test_miss_returns_none(self):
        cache = SemanticLRUCache(capacity=10)
        assert cache.get("nonexistent") is None

    def test_contains(self):
        cache = SemanticLRUCache(capacity=10)
        e = make_entry()
        assert e.id not in cache
        cache.put(e)
        assert e.id in cache

    def test_capacity_triggers_eviction(self):
        cache = SemanticLRUCache(capacity=3)
        entries = [make_entry(content=f"entry {i}") for i in range(4)]
        ghosts = []
        for e in entries:
            ghosts.extend(cache.put(e))
        assert len(cache) <= 3
        assert len(ghosts) >= 1

    def test_ghost_created_on_eviction(self):
        cache = SemanticLRUCache(capacity=2)
        e1 = make_entry(content="first")
        e2 = make_entry(content="second")
        e3 = make_entry(content="third")
        cache.put(e1)
        cache.put(e2)
        ghosts = cache.put(e3)
        assert len(ghosts) == 1
        ghost = ghosts[0]
        assert ghost.agent_id == "a1"
        assert ghost.cold_path.startswith("s3://")
        assert len(ghost.content_hash) == 64  # SHA-256 hex

    def test_high_degree_protected_from_eviction(self):
        cache = SemanticLRUCache(capacity=2)
        important = make_entry(content="very important")
        cache.put(important, in_degree=20)

        # Put a fresh entry to trigger eviction
        for i in range(3):
            evicted = cache.put(make_entry(content=f"fill {i}"), in_degree=0)

        # Important entry (high centrality) should survive
        assert cache.get(important.id) is not None

    def test_update_degree(self):
        cache = SemanticLRUCache(capacity=10)
        e = make_entry()
        cache.put(e, in_degree=0)
        cache.update_degree(e.id, 15)
        scores = cache.scores()
        entry_score = next(s for s in scores if s.memory_id == e.id)
        assert entry_score.centrality > 0

    def test_stats(self):
        cache = SemanticLRUCache(capacity=10)
        for i in range(5):
            cache.put(make_entry(content=str(i)))
        stats = cache.stats()
        assert stats["size"] == 5
        assert stats["capacity"] == 10
        assert stats["utilisation"] == 0.5

    def test_snapshot_ordered_by_score(self):
        cache = SemanticLRUCache(capacity=10)
        e_peripheral = make_entry(content="peripheral")
        e_central    = make_entry(content="central")
        cache.put(e_peripheral, in_degree=0)
        cache.put(e_central, in_degree=20)
        snapshot = cache.snapshot()
        ids = [e.id for e in snapshot]
        assert ids.index(e_central.id) < ids.index(e_peripheral.id)


# ─────────────────────────────────────────────────────────────────────────────
# FederationPolicyEngine
# ─────────────────────────────────────────────────────────────────────────────

class TestFederationPolicyEngine:
    def _make_engine(self, policies: list[FederationPolicy]) -> FederationPolicyEngine:
        store = PolicyStore()
        for p in policies:
            store.register(p)
        return FederationPolicyEngine(store)

    @pytest.mark.asyncio
    async def test_self_access_always_allowed(self):
        engine = self._make_engine([])
        decision = await engine.evaluate("agent-A", "agent-A")
        assert decision.allow is True
        assert decision.reason == "self_access"

    @pytest.mark.asyncio
    async def test_no_policy_denies(self):
        engine = self._make_engine([])
        decision = await engine.evaluate("agent-B", "agent-A")
        assert decision.allow is False

    @pytest.mark.asyncio
    async def test_public_policy_allows_all(self):
        policy = FederationPolicy(
            owner_agent_id="agent-A",
            public=True,
        )
        engine = self._make_engine([policy])
        decision = await engine.evaluate("agent-X", "agent-A")
        assert decision.allow is True
        assert decision.reason == "policy_public"

    @pytest.mark.asyncio
    async def test_allowed_agent_gets_access(self):
        policy = FederationPolicy(
            owner_agent_id="agent-A",
            allowed_agents=["agent-B"],
        )
        engine = self._make_engine([policy])
        decision = await engine.evaluate("agent-B", "agent-A")
        assert decision.allow is True
        assert decision.reason == "agent_allowed"

    @pytest.mark.asyncio
    async def test_unlisted_agent_denied(self):
        policy = FederationPolicy(
            owner_agent_id="agent-A",
            allowed_agents=["agent-B"],
        )
        engine = self._make_engine([policy])
        decision = await engine.evaluate("agent-C", "agent-A")
        assert decision.allow is False

    @pytest.mark.asyncio
    async def test_team_access(self):
        policy = FederationPolicy(
            owner_agent_id="agent-A",
            allowed_teams=["team-alpha"],
        )
        engine = self._make_engine([policy])
        decision = await engine.evaluate(
            "agent-X", "agent-A", requesting_team="team-alpha"
        )
        assert decision.allow is True
        assert decision.reason == "team_allowed"

    @pytest.mark.asyncio
    async def test_wrong_team_denied(self):
        policy = FederationPolicy(
            owner_agent_id="agent-A",
            allowed_teams=["team-alpha"],
        )
        engine = self._make_engine([policy])
        decision = await engine.evaluate(
            "agent-X", "agent-A", requesting_team="team-beta"
        )
        assert decision.allow is False

    def test_redact_removes_fields(self):
        engine = self._make_engine([])
        entry = make_entry()
        entry.metadata = {"secret_key": "abc", "public_data": "xyz"}
        ranked = [make_ranked(entry)]
        redacted = engine.redact(ranked, fields=["secret_key"])
        assert "secret_key" not in redacted[0].entry.metadata
        assert "public_data" in redacted[0].entry.metadata

    def test_redact_marks_as_federated(self):
        engine = self._make_engine([])
        entry = make_entry()
        ranked = [make_ranked(entry)]
        redacted = engine.redact(ranked, fields=["any"])
        assert redacted[0].from_federation is True

    def test_redact_empty_fields_noop(self):
        engine = self._make_engine([])
        entry = make_entry()
        entry.metadata = {"key": "val"}
        ranked = [make_ranked(entry)]
        result = engine.redact(ranked, fields=[])
        # When fields is empty, no-op: returns same list
        assert result[0].entry.metadata["key"] == "val"

    def test_policy_store_register_and_get(self):
        store = PolicyStore()
        policy = FederationPolicy(owner_agent_id="agent-Z")
        store.register(policy)
        assert store.get("agent-Z") is policy
        assert store.get("agent-nonexistent") is None
