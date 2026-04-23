"""
agentmemos.tiers.working
─────────────────────────
Tier 0 — Working Memory backed by Redis.

Properties
----------
  - TTL scoped to session (default 4 h)
  - Sub-millisecond read (local Redis cluster)
  - Acts as the hot-path scratchpad; agent context is hydrated from here first
  - Namespace isolation: keys are prefixed agent_id:session_id:
  - Pub/sub channel per agent for cross-agent federation (read-only view)
"""

from __future__ import annotations

import json
import os
import time
from typing import AsyncIterator

import redis.asyncio as aioredis

from agentmemos.core.models import GhostEntry, MemoryEntry, MemoryTier


REDIS_URL          = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECS   = int(os.getenv("WORKING_MEMORY_TTL", str(4 * 3600)))   # 4 h
GHOST_TTL_SECS     = int(os.getenv("GHOST_TTL_SECS", str(7 * 24 * 3600))) # 7 d
MAX_ENTRIES        = int(os.getenv("WORKING_MAX_ENTRIES", "512"))


def _entry_key(agent_id: str, session_id: str, memory_id: str) -> str:
    return f"mem:w:{agent_id}:{session_id}:{memory_id}"


def _index_key(agent_id: str, session_id: str) -> str:
    """Sorted set key: score = insertion timestamp."""
    return f"mem:w:idx:{agent_id}:{session_id}"


def _ghost_key(agent_id: str, ghost_id: str) -> str:
    return f"mem:ghost:{agent_id}:{ghost_id}"


class WorkingMemoryTier:
    """
    Redis-backed working memory tier.
    Thread-safe via aioredis connection pool.
    """

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._redis = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_keepalive=True,
        )

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    @property
    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            raise RuntimeError("WorkingMemoryTier not connected — call connect() first.")
        return self._redis

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, entry: MemoryEntry) -> None:
        pipe = self.redis.pipeline(transaction=True)

        key = _entry_key(entry.agent_id, entry.session_id, entry.id)
        idx = _index_key(entry.agent_id, entry.session_id)

        payload = entry.model_dump_json()
        pipe.setex(key, SESSION_TTL_SECS, payload)
        pipe.zadd(idx, {entry.id: time.time()})
        pipe.expire(idx, SESSION_TTL_SECS)

        await pipe.execute()

        # Trim to MAX_ENTRIES — evict oldest
        count = await self.redis.zcard(idx)
        if count > MAX_ENTRIES:
            oldest_ids = await self.redis.zrange(idx, 0, count - MAX_ENTRIES - 1)
            if oldest_ids:
                await self._evict(entry.agent_id, entry.session_id, oldest_ids)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def read(
        self,
        agent_id: str,
        session_id: str,
        memory_id: str,
    ) -> MemoryEntry | None:
        key = _entry_key(agent_id, session_id, memory_id)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return MemoryEntry.model_validate_json(raw)

    async def get_recent(
        self,
        agent_id: str,
        session_id: str,
        n: int = 20,
    ) -> list[MemoryEntry]:
        """Return N most-recently written entries for this session."""
        idx = _index_key(agent_id, session_id)
        # ZRANGE ... REV LIMIT 0 N (newest first)
        ids = await self.redis.zrange(idx, 0, n - 1, desc=True)
        if not ids:
            return []

        pipe = self.redis.pipeline()
        for mid in ids:
            pipe.get(_entry_key(agent_id, session_id, mid))
        raws = await pipe.execute()

        entries: list[MemoryEntry] = []
        for raw in raws:
            if raw:
                entries.append(MemoryEntry.model_validate_json(raw))
        return entries

    async def search(
        self,
        agent_id: str,
        session_id: str,
        query: str,          # simple substring match in working tier
        top_k: int = 10,
    ) -> list[MemoryEntry]:
        """
        Linear scan over session entries.
        Working memory is small (≤512 entries) so this is acceptable.
        Production upgrade: use RedisSearch FT.SEARCH on content field.
        """
        all_entries = await self.get_recent(agent_id, session_id, n=MAX_ENTRIES)
        q = query.lower()
        matched = [e for e in all_entries if q in e.content.lower()]
        return matched[:top_k]

    # ── Delete / Evict ────────────────────────────────────────────────────────

    async def delete(
        self,
        agent_id: str,
        session_id: str,
        memory_id: str,
        ghost: bool = False,
        cold_path: str | None = None,
        content_hash: str | None = None,
    ) -> bool:
        key = _entry_key(agent_id, session_id, memory_id)
        idx = _index_key(agent_id, session_id)

        pipe = self.redis.pipeline()
        pipe.delete(key)
        pipe.zrem(idx, memory_id)
        results = await pipe.execute()
        deleted = bool(results[0])

        if deleted and ghost and cold_path and content_hash:
            g = GhostEntry(
                original_id=memory_id,
                agent_id=agent_id,
                content_hash=content_hash,
                cold_path=cold_path,
                tier=MemoryTier.WORKING,
            )
            ghost_key = _ghost_key(agent_id, g.ghost_id)
            await self.redis.setex(
                ghost_key, GHOST_TTL_SECS, g.model_dump_json()
            )

        return deleted

    async def _evict(
        self,
        agent_id: str,
        session_id: str,
        memory_ids: list[str],
    ) -> None:
        idx = _index_key(agent_id, session_id)
        pipe = self.redis.pipeline()
        for mid in memory_ids:
            pipe.delete(_entry_key(agent_id, session_id, mid))
            pipe.zrem(idx, mid)
        await pipe.execute()

    # ── Ghost lookup ──────────────────────────────────────────────────────────

    async def get_ghost(self, agent_id: str, ghost_id: str) -> GhostEntry | None:
        key = _ghost_key(agent_id, ghost_id)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        return GhostEntry.model_validate_json(raw)

    async def ghost_exists(self, agent_id: str, content_hash: str) -> str | None:
        """
        Scan ghost namespace for a matching content_hash.
        Returns the cold_path if found, else None.
        """
        pattern = _ghost_key(agent_id, "ghost:*")
        async for key in self.redis.scan_iter(pattern, count=100):
            raw = await self.redis.get(key)
            if raw:
                ghost = GhostEntry.model_validate_json(raw)
                if ghost.content_hash == content_hash:
                    return ghost.cold_path
        return None

    # ── Session management ────────────────────────────────────────────────────

    async def flush_session(self, agent_id: str, session_id: str) -> int:
        """Delete all working memory for a completed session."""
        idx = _index_key(agent_id, session_id)
        ids = await self.redis.zrange(idx, 0, -1)

        if not ids:
            return 0

        pipe = self.redis.pipeline()
        for mid in ids:
            pipe.delete(_entry_key(agent_id, session_id, mid))
        pipe.delete(idx)
        await pipe.execute()
        return len(ids)

    # ── Health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            return await self.redis.ping()
        except Exception:
            return False

    async def stats(self, agent_id: str, session_id: str) -> dict:
        idx = _index_key(agent_id, session_id)
        count = await self.redis.zcard(idx)
        info = await self.redis.info("memory")
        return {
            "session_entry_count": count,
            "redis_used_memory": info.get("used_memory_human"),
            "tier": "working",
        }
