"""
agentmemos.tiers.procedural
────────────────────────────
Tier 3 — Procedural Memory backed by PostgreSQL.

Stores structured task execution traces and reusable tool-call sequences.
Also acts as the canonical version ledger for memory rollback across all tiers.

Schema
------
  memory_entries    — full entry records with JSONB metadata
  tool_call_traces  — step-by-step tool invocation sequences
  memory_versions   — immutable WAL of all writes (audit + rollback)
  agent_thresholds  — per-agent dynamic importance thresholds
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from agentmemos.core.models import MemoryEntry, MemoryTier, MemoryType

try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False


PG_DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://agentmemos:agentmemos@localhost:5432/agentmemos",
)


# ─────────────────────────────────────────────────────────────────────────────
# DDL
# ─────────────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id           TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    content      TEXT NOT NULL,
    type         INTEGER NOT NULL,
    tier         INTEGER NOT NULL,
    importance   REAL NOT NULL DEFAULT 0.0,
    metadata     JSONB NOT NULL DEFAULT '{}',
    related_ids  JSONB NOT NULL DEFAULT '[]',
    version_ref  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_me_agent_tier
    ON memory_entries (agent_id, tier);
CREATE INDEX IF NOT EXISTS idx_me_importance
    ON memory_entries (importance DESC);
CREATE INDEX IF NOT EXISTS idx_me_created
    ON memory_entries (created_at DESC);

CREATE TABLE IF NOT EXISTS tool_call_traces (
    trace_id     TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    agent_id     TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    task_desc    TEXT NOT NULL,
    steps        JSONB NOT NULL,   -- ordered list of {tool, args, result, success}
    outcome      BOOLEAN,
    importance   REAL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tct_agent
    ON tool_call_traces (agent_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_versions (
    version_ref  TEXT PRIMARY KEY,
    agent_id     TEXT NOT NULL,
    memory_id    TEXT NOT NULL,
    tier         INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot     JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mv_agent_tier
    ON memory_versions (agent_id, tier, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_thresholds (
    agent_id     TEXT PRIMARY KEY,
    threshold    REAL NOT NULL DEFAULT 0.45,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


# ─────────────────────────────────────────────────────────────────────────────
# Tool Call Step Model
# ─────────────────────────────────────────────────────────────────────────────

class ToolCallStep:
    def __init__(
        self,
        tool: str,
        args: dict[str, Any],
        result: Any,
        success: bool,
        duration_ms: float = 0.0,
    ) -> None:
        self.tool = tool
        self.args = args
        self.result = result
        self.success = success
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        return {
            "tool":        self.tool,
            "args":        self.args,
            "result":      self.result,
            "success":     self.success,
            "duration_ms": self.duration_ms,
        }


# ─────────────────────────────────────────────────────────────────────────────
# ProceduralMemoryTier
# ─────────────────────────────────────────────────────────────────────────────

class ProceduralMemoryTier:
    """
    PostgreSQL-backed procedural memory tier using asyncpg connection pool.
    """

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if not _ASYNCPG_AVAILABLE:
            raise RuntimeError("asyncpg not installed.")
        self._pool = await asyncpg.create_pool(
            dsn=PG_DSN,
            min_size=2,
            max_size=20,
            command_timeout=30,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("ProceduralMemoryTier not connected.")
        return self._pool

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, entry: MemoryEntry) -> str:
        """
        Upsert entry and create an immutable version snapshot.
        Returns version_ref.
        """
        import hashlib
        version_ref = f"v:{uuid.uuid4()}"
        content_hash = hashlib.sha256(entry.content.encode()).hexdigest()
        snapshot = json.loads(entry.model_dump_json(exclude={"embedding"}))

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO memory_entries
                        (id, agent_id, session_id, content, type, tier,
                         importance, metadata, related_ids, version_ref,
                         created_at, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (id) DO UPDATE SET
                        content     = EXCLUDED.content,
                        importance  = EXCLUDED.importance,
                        metadata    = EXCLUDED.metadata,
                        updated_at  = EXCLUDED.updated_at,
                        version_ref = EXCLUDED.version_ref
                    """,
                    entry.id,
                    entry.agent_id,
                    entry.session_id,
                    entry.content,
                    entry.type.value,
                    entry.tier.value,
                    entry.importance,
                    json.dumps(entry.metadata),
                    json.dumps(entry.related_ids),
                    version_ref,
                    entry.created_at,
                    entry.updated_at,
                )
                await conn.execute(
                    """
                    INSERT INTO memory_versions
                        (version_ref, agent_id, memory_id, tier, content_hash, snapshot)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    """,
                    version_ref,
                    entry.agent_id,
                    entry.id,
                    entry.tier.value,
                    content_hash,
                    json.dumps(snapshot),
                )

        return version_ref

    # ── Tool-call trace ───────────────────────────────────────────────────────

    async def write_trace(
        self,
        agent_id: str,
        session_id: str,
        task_desc: str,
        steps: list[ToolCallStep],
        outcome: bool | None = None,
        importance: float = 0.0,
    ) -> str:
        trace_id = str(uuid.uuid4())
        steps_json = json.dumps([s.to_dict() for s in steps])

        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO tool_call_traces
                    (trace_id, agent_id, session_id, task_desc, steps, outcome, importance)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                """,
                trace_id,
                agent_id,
                session_id,
                task_desc,
                steps_json,
                outcome,
                importance,
            )
        return trace_id

    # ── Read ──────────────────────────────────────────────────────────────────

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 10,
        min_importance: float = 0.0,
    ) -> list[tuple[MemoryEntry, float]]:
        """Full-text search over procedural entries using pg ilike."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM memory_entries
                WHERE agent_id = $1
                  AND tier = $2
                  AND importance >= $3
                  AND content ILIKE $4
                ORDER BY importance DESC, created_at DESC
                LIMIT $5
                """,
                agent_id,
                MemoryTier.PROCEDURAL.value,
                min_importance,
                f"%{query}%",
                top_k,
            )
        return [(self._row_to_entry(r), r["importance"]) for r in rows]

    async def search_traces(
        self,
        agent_id: str,
        task_desc_query: str,
        top_k: int = 5,
        successful_only: bool = True,
    ) -> list[dict]:
        """Find previously successful tool-call sequences for a task."""
        async with self.pool.acquire() as conn:
            query = """
                SELECT trace_id, task_desc, steps, outcome, importance
                FROM tool_call_traces
                WHERE agent_id = $1
                  AND task_desc ILIKE $2
                ORDER BY importance DESC, created_at DESC
                LIMIT $3
            """
            params = [agent_id, f"%{task_desc_query}%", top_k]
            if successful_only:
                query = """
                    SELECT trace_id, task_desc, steps, outcome, importance
                    FROM tool_call_traces
                    WHERE agent_id = $1
                      AND task_desc ILIKE $2
                      AND outcome = true
                    ORDER BY importance DESC, created_at DESC
                    LIMIT $3
                """
            rows = await conn.fetch(query, *params)

        return [
            {
                "trace_id":  r["trace_id"],
                "task_desc": r["task_desc"],
                "steps":     json.loads(r["steps"]),
                "outcome":   r["outcome"],
                "importance":r["importance"],
            }
            for r in rows
        ]

    async def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM memory_entries WHERE id = $1", memory_id
            )
        return self._row_to_entry(row) if row else None

    # ── Versioning ────────────────────────────────────────────────────────────

    async def list_versions(
        self,
        agent_id: str,
        tier: MemoryTier | None = None,
        limit: int = 50,
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            if tier:
                rows = await conn.fetch(
                    """
                    SELECT version_ref, memory_id, tier, content_hash, created_at
                    FROM memory_versions
                    WHERE agent_id = $1 AND tier = $2
                    ORDER BY created_at DESC LIMIT $3
                    """,
                    agent_id, tier.value, limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT version_ref, memory_id, tier, content_hash, created_at
                    FROM memory_versions
                    WHERE agent_id = $1
                    ORDER BY created_at DESC LIMIT $2
                    """,
                    agent_id, limit,
                )
        return [dict(r) for r in rows]

    async def rollback(self, agent_id: str, version_ref: str, tier: MemoryTier) -> int:
        """
        Delete all memory_entries for agent/tier created after version_ref's timestamp.
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT created_at FROM memory_versions WHERE version_ref = $1",
                version_ref,
            )
            if row is None:
                raise ValueError(f"Version {version_ref!r} not found.")
            cutoff = row["created_at"]

            result = await conn.execute(
                """
                DELETE FROM memory_entries
                WHERE agent_id = $1 AND tier = $2 AND created_at > $3
                """,
                agent_id, tier.value, cutoff,
            )
        deleted = int(result.split()[-1])
        return deleted

    # ── Agent thresholds ──────────────────────────────────────────────────────

    async def get_threshold(self, agent_id: str) -> float:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT threshold FROM agent_thresholds WHERE agent_id = $1",
                agent_id,
            )
        return row["threshold"] if row else 0.45

    async def set_threshold(self, agent_id: str, threshold: float) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO agent_thresholds (agent_id, threshold)
                VALUES ($1, $2)
                ON CONFLICT (agent_id) DO UPDATE SET threshold=$2, updated_at=NOW()
                """,
                agent_id, threshold,
            )

    # ── Health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    async def stats(self, agent_id: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE tier = $2) AS proc_count,
                    COUNT(*) AS total_count
                FROM memory_entries WHERE agent_id = $1
                """,
                agent_id, MemoryTier.PROCEDURAL.value,
            )
        return {
            "tier": "procedural",
            "agent_id": agent_id,
            "procedural_entries": row["proc_count"] if row else 0,
            "total_entries": row["total_count"] if row else 0,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_entry(row: Any) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            content=row["content"],
            type=MemoryType(row["type"]),
            tier=MemoryTier(row["tier"]),
            importance=row["importance"],
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"]),
            related_ids=json.loads(row["related_ids"]) if isinstance(row["related_ids"], str) else list(row["related_ids"]),
            version_ref=row["version_ref"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
