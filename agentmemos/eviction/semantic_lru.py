"""
agentmemos.eviction.semantic_lru
─────────────────────────────────
Semantic LRU eviction algorithm.

Standard LRU evicts the least-recently-used entry.
Semantic LRU evicts based on a combined score of:
  - Recency  (time since last access, exponential decay)
  - Semantic centrality (normalised in-degree / PageRank proxy)

Entries with the lowest combined score are evicted first.
Evicted entries become ghost tombstones in Redis so the agent can
detect it once knew something and trigger cold-tier retrieval.

Why ghost entries?
──────────────────
Inspired by CPU cache ghost/victim entries.  When the agent encounters
a ghost during a read, it knows relevant information exists in cold
storage (S3) and triggers a prefetch before the next operation — avoiding
a "cache miss penalty" on the next request.

Eviction trigger
────────────────
Called by the working-tier writer when entry count exceeds MAX_ENTRIES.
Also called by the consolidation pipeline after archiving.
"""

from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from typing import Sequence

from agentmemos.core.models import GhostEntry, MemoryEntry, MemoryTier


# ─────────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvictionScore:
    memory_id:    str
    recency:      float   # 0 = stale, 1 = fresh
    centrality:   float   # 0 = peripheral, 1 = central
    combined:     float   # higher = keep, lower = evict


def _recency_score(last_accessed: float, half_life: float = 3600.0) -> float:
    age = time.time() - last_accessed
    lam = math.log(2) / half_life
    return math.exp(-lam * age)


def _centrality_score(in_degree: int, max_degree: int = 30) -> float:
    return math.log1p(in_degree) / math.log1p(max(max_degree, in_degree))


def score_entry(
    entry: MemoryEntry,
    last_accessed: float | None = None,
    in_degree: int = 0,
    recency_weight: float = 0.5,
    centrality_weight: float = 0.5,
) -> EvictionScore:
    la = last_accessed or entry.created_at.timestamp()
    r = _recency_score(la)
    c = _centrality_score(in_degree)
    combined = recency_weight * r + centrality_weight * c
    return EvictionScore(
        memory_id=entry.id,
        recency=r,
        centrality=c,
        combined=combined,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SemanticLRUCache
# ─────────────────────────────────────────────────────────────────────────────

class SemanticLRUCache:
    """
    Fixed-capacity cache with semantic eviction policy.

    Usage
    -----
    cache = SemanticLRUCache(capacity=512)
    cache.put(entry, in_degree=5)
    entry = cache.get(memory_id)
    evicted_ghosts = cache.evict_n(10)
    """

    def __init__(
        self,
        capacity: int = 512,
        recency_weight: float = 0.5,
        centrality_weight: float = 0.5,
    ) -> None:
        self._capacity = capacity
        self._rw = recency_weight
        self._cw = centrality_weight
        self._entries: dict[str, MemoryEntry] = {}
        self._last_accessed: dict[str, float] = {}
        self._in_degrees: dict[str, int] = {}

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, memory_id: str) -> MemoryEntry | None:
        entry = self._entries.get(memory_id)
        if entry is not None:
            self._last_accessed[memory_id] = time.time()
        return entry

    def __contains__(self, memory_id: str) -> bool:
        return memory_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    # ── Write ─────────────────────────────────────────────────────────────────

    def put(
        self,
        entry: MemoryEntry,
        in_degree: int = 0,
    ) -> list[GhostEntry]:
        """
        Insert entry into cache.
        If over capacity, evict lowest-scoring entries and return ghost entries.
        """
        self._entries[entry.id]       = entry
        self._last_accessed[entry.id] = time.time()
        self._in_degrees[entry.id]    = in_degree

        if len(self._entries) > self._capacity:
            overflow = len(self._entries) - self._capacity
            return self.evict_n(overflow)
        return []

    def update_degree(self, memory_id: str, in_degree: int) -> None:
        """Called by PageRank refresh to update centrality signal."""
        self._in_degrees[memory_id] = in_degree

    # ── Eviction ──────────────────────────────────────────────────────────────

    def evict_n(self, n: int) -> list[GhostEntry]:
        """
        Evict N lowest-scoring entries.
        Returns GhostEntry tombstones for each evicted item.
        """
        if not self._entries:
            return []

        scores = [
            score_entry(
                entry=entry,
                last_accessed=self._last_accessed.get(mid),
                in_degree=self._in_degrees.get(mid, 0),
                recency_weight=self._rw,
                centrality_weight=self._cw,
            )
            for mid, entry in self._entries.items()
        ]
        scores.sort(key=lambda s: s.combined)

        ghosts: list[GhostEntry] = []
        for eviction_score in scores[:n]:
            mid = eviction_score.memory_id
            entry = self._entries.pop(mid, None)
            self._last_accessed.pop(mid, None)
            self._in_degrees.pop(mid, None)

            if entry:
                ghost = GhostEntry(
                    original_id=mid,
                    agent_id=entry.agent_id,
                    content_hash=hashlib.sha256(entry.content.encode()).hexdigest(),
                    cold_path=f"s3://agentmemos-archive/{entry.agent_id}/{mid}.json",
                    tier=entry.tier,
                )
                ghosts.append(ghost)

        return ghosts

    # ── Batch operations ──────────────────────────────────────────────────────

    def scores(self) -> list[EvictionScore]:
        """Return all current eviction scores (for monitoring)."""
        return sorted(
            [
                score_entry(
                    entry=entry,
                    last_accessed=self._last_accessed.get(mid),
                    in_degree=self._in_degrees.get(mid, 0),
                    recency_weight=self._rw,
                    centrality_weight=self._cw,
                )
                for mid, entry in self._entries.items()
            ],
            key=lambda s: s.combined,
            reverse=True,
        )

    def snapshot(self) -> list[MemoryEntry]:
        """Return all cached entries ordered by descending combined score."""
        scored = self.scores()
        return [self._entries[s.memory_id] for s in scored if s.memory_id in self._entries]

    def stats(self) -> dict:
        return {
            "capacity": self._capacity,
            "size": len(self._entries),
            "utilisation": len(self._entries) / self._capacity,
        }
