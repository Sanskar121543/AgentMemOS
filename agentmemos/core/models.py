"""
agentmemos.core.models
─────────────────────
Canonical domain models shared across all tiers and services.
All cross-tier communication uses these types; protobuf messages are
converted to/from these at the gRPC boundary only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import IntEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class MemoryTier(IntEnum):
    WORKING    = 1   # Redis   — sub-ms, TTL=session
    EPISODIC   = 2   # Pinecone — vector search
    SEMANTIC   = 3   # Neo4j   — knowledge graph
    PROCEDURAL = 4   # PostgreSQL — structured traces


class MemoryType(IntEnum):
    OBSERVATION = 1
    ACTION      = 2
    OUTCOME     = 3
    FACT        = 4
    PROCEDURE   = 5
    REFLECTION  = 6


class RouterIntent(IntEnum):
    """Classified intent of a memory operation, used by MemoryRouter."""
    SHORT_TERM_STORE = 1   # → WORKING
    EPISODE_STORE    = 2   # → EPISODIC
    LEARN_FACT       = 3   # → SEMANTIC
    LEARN_PROCEDURE  = 4   # → PROCEDURAL
    RECALL_RECENT    = 5   # → WORKING + EPISODIC fan-out
    RECALL_DEEP      = 6   # → all tiers fan-out
    INTROSPECT       = 7   # → SEMANTIC only


# ─────────────────────────────────────────────────────────────────────────────
# Importance Signals
# ─────────────────────────────────────────────────────────────────────────────

class ImportanceSignals(BaseModel):
    recency_score:    float = Field(ge=0.0, le=1.0, description="Exponential decay from creation time")
    cross_ref_count:  float = Field(ge=0.0, description="Normalized count of in-graph references")
    outcome_salience: float = Field(ge=0.0, le=1.0, description="Success/failure signal of subsequent actions")
    agent_confidence: float = Field(ge=0.0, le=1.0, description="Agent confidence at time of formation")
    semantic_novelty: float = Field(ge=0.0, le=1.0, description="Novelty vs existing semantic memory")

    @property
    def composite(self) -> float:
        """Weighted composite score used for promotion decisions."""
        return (
            0.20 * self.recency_score
            + 0.25 * self.cross_ref_count
            + 0.25 * self.outcome_salience
            + 0.10 * self.agent_confidence
            + 0.20 * self.semantic_novelty
        )


# ─────────────────────────────────────────────────────────────────────────────
# Core Memory Entry
# ─────────────────────────────────────────────────────────────────────────────

class MemoryEntry(BaseModel):
    id:          str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id:    str
    session_id:  str
    content:     str = Field(min_length=1)
    type:        MemoryType
    tier:        MemoryTier
    importance:  float = Field(default=0.0, ge=0.0, le=1.0)
    signals:     ImportanceSignals | None = None
    created_at:  datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at:  datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata:    dict[str, Any] = Field(default_factory=dict)
    related_ids: list[str] = Field(default_factory=list)
    version_ref: str | None = None
    embedding:   list[float] | None = Field(default=None, exclude=True)  # never serialised to DB

    model_config = {"use_enum_values": False}

    @field_validator("content")
    @classmethod
    def strip_content(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def sync_updated_at(self) -> MemoryEntry:
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at
        return self

    def namespace_key(self) -> str:
        """Composite key for multi-tenant namespace isolation."""
        return f"{self.agent_id}:{self.tier.value}:{self.id}"


# ─────────────────────────────────────────────────────────────────────────────
# Ranked Memory (returned from fan-out reads)
# ─────────────────────────────────────────────────────────────────────────────

class RankedMemory(BaseModel):
    entry:           MemoryEntry
    relevance:       float = Field(ge=0.0, le=1.0)
    recency:         float = Field(ge=0.0, le=1.0)
    final_score:     float = Field(ge=0.0, le=1.0)
    source_tier:     MemoryTier
    from_federation: bool = False

    @classmethod
    def fuse(
        cls,
        entry: MemoryEntry,
        relevance: float,
        recency: float,
        *,
        from_federation: bool = False,
        relevance_weight: float = 0.6,
        recency_weight:   float = 0.4,
    ) -> RankedMemory:
        final = relevance_weight * relevance + recency_weight * recency
        return cls(
            entry=entry,
            relevance=relevance,
            recency=recency,
            final_score=min(final, 1.0),
            source_tier=entry.tier,
            from_federation=from_federation,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Write / Read Request/Response (internal — not protobuf)
# ─────────────────────────────────────────────────────────────────────────────

class WriteRequest(BaseModel):
    agent_id:    str
    session_id:  str
    content:     str
    type:        MemoryType
    confidence:  float = Field(default=0.8, ge=0.0, le=1.0)
    metadata:    dict[str, Any] = Field(default_factory=dict)
    related_ids: list[str] = Field(default_factory=list)
    target_tier: MemoryTier | None = None  # None = let router decide


class WriteResponse(BaseModel):
    memory_id:   str
    routed_to:   MemoryTier
    importance:  float
    promoted:    bool = False     # promoted to Tier 2 or 3
    version_ref: str | None = None
    written_at:  datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReadRequest(BaseModel):
    agent_id:           str
    session_id:         str
    query:              str
    top_k:              int = Field(default=10, ge=1, le=100)
    tiers:              list[MemoryTier] = Field(default_factory=list)  # empty = all
    min_importance:     float = Field(default=0.0, ge=0.0, le=1.0)
    include_federated:  bool = False
    filters:            dict[str, Any] = Field(default_factory=dict)


class ReadResponse(BaseModel):
    results:      list[RankedMemory]
    latency_us:   int
    tier_counts:  dict[str, int] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Federation Policy
# ─────────────────────────────────────────────────────────────────────────────

class FederationPolicy(BaseModel):
    policy_id:      str = Field(default_factory=lambda: str(uuid.uuid4()))
    owner_agent_id: str
    allowed_agents: list[str] = Field(default_factory=list)
    allowed_teams:  list[str] = Field(default_factory=list)
    public:         bool = False
    redact_fields:  list[str] = Field(default_factory=list)
    created_at:     datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─────────────────────────────────────────────────────────────────────────────
# Consolidation Result
# ─────────────────────────────────────────────────────────────────────────────

class ConsolidationResult(BaseModel):
    agent_id:             str
    clusters_found:       int
    nodes_created:        int
    episodes_archived:    int
    storage_freed_bytes:  int
    ran_at:               datetime = Field(default_factory=lambda: datetime.now(UTC))
    duration_seconds:     float


# ─────────────────────────────────────────────────────────────────────────────
# Ghost Entry  (tombstone after semantic-LRU eviction)
# ─────────────────────────────────────────────────────────────────────────────

class GhostEntry(BaseModel):
    ghost_id:       str = Field(default_factory=lambda: f"ghost:{uuid.uuid4()}")
    original_id:    str
    agent_id:       str
    content_hash:   str   # SHA-256 of evicted content — no PII
    cold_path:      str   # s3://bucket/key
    evicted_at:     datetime = Field(default_factory=lambda: datetime.now(UTC))
    tier:           MemoryTier
