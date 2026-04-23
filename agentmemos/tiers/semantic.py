"""
agentmemos.tiers.semantic
─────────────────────────
Tier 2 — Semantic Memory backed by Neo4j.

Graph schema
────────────
  (:Agent {id})
      -[:KNOWS]->
  (:Concept {id, content, importance, agent_id, created_at})
      -[:RELATED_TO {weight, relationship_type}]->
  (:Concept {...})

  (:MemoryVersion {ref, agent_id, tier, snapshot_at})
      -[:SNAPSHOT_OF]->
  (:Concept {...})

Capabilities
────────────
  - Versioned snapshots for rollback
  - PageRank-based centrality (used by ImportanceScorer cross_ref_count)
  - Relationship-aware retrieval (1-hop neighbourhood expansion)
  - Consolidated nodes created by the sleep-consolidation pipeline
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from agentmemos.core.models import MemoryEntry, MemoryTier, MemoryType

try:
    from neo4j import AsyncGraphDatabase, AsyncDriver
    _NEO4J_AVAILABLE = True
except ImportError:
    _NEO4J_AVAILABLE = False


NEO4J_URI      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "agentmemos")


class SemanticMemoryTier:
    """
    Neo4j-backed semantic (knowledge graph) memory tier.
    Uses async driver; all methods are coroutines.
    """

    def __init__(self) -> None:
        self._driver: AsyncDriver | None = None

    async def connect(self) -> None:
        if not _NEO4J_AVAILABLE:
            raise RuntimeError("neo4j Python driver not installed.")
        self._driver = AsyncGraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
        await self._ensure_constraints()

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()

    @property
    def driver(self) -> AsyncDriver:
        if self._driver is None:
            raise RuntimeError("SemanticMemoryTier not connected.")
        return self._driver

    # ── Schema Setup ──────────────────────────────────────────────────────────

    async def _ensure_constraints(self) -> None:
        async with self.driver.session() as session:
            await session.run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (c:Concept) REQUIRE c.id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT IF NOT EXISTS FOR (a:Agent) REQUIRE a.id IS UNIQUE"
            )
            await session.run(
                "CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.agent_id)"
            )
            await session.run(
                "CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.importance)"
            )

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, entry: MemoryEntry) -> str:
        """
        Upsert a Concept node and create a version snapshot.
        Returns the version_ref.
        """
        version_ref = f"v:{uuid.uuid4()}"
        now_ts = int(datetime.now(timezone.utc).timestamp())

        async with self.driver.session() as session:
            # Ensure Agent node exists
            await session.run(
                "MERGE (a:Agent {id: $agent_id})",
                agent_id=entry.agent_id,
            )

            # Upsert Concept node
            await session.run(
                """
                MERGE (c:Concept {id: $id})
                ON CREATE SET
                    c.agent_id   = $agent_id,
                    c.content    = $content,
                    c.type       = $type,
                    c.importance = $importance,
                    c.created_at = $created_at,
                    c.updated_at = $updated_at,
                    c.session_id = $session_id
                ON MATCH SET
                    c.content    = $content,
                    c.importance = $importance,
                    c.updated_at = $updated_at
                WITH c
                MATCH (a:Agent {id: $agent_id})
                MERGE (a)-[:KNOWS]->(c)
                """,
                id=entry.id,
                agent_id=entry.agent_id,
                content=entry.content,
                type=entry.type.value,
                importance=entry.importance,
                created_at=int(entry.created_at.timestamp()),
                updated_at=now_ts,
                session_id=entry.session_id,
            )

            # Create version snapshot
            await session.run(
                """
                CREATE (v:MemoryVersion {
                    ref: $version_ref,
                    agent_id: $agent_id,
                    tier: $tier,
                    snapshot_at: $snapshot_at,
                    content_hash: $content_hash
                })
                WITH v
                MATCH (c:Concept {id: $id})
                CREATE (v)-[:SNAPSHOT_OF]->(c)
                """,
                version_ref=version_ref,
                agent_id=entry.agent_id,
                tier=MemoryTier.SEMANTIC.value,
                snapshot_at=now_ts,
                content_hash=hashlib.sha256(entry.content.encode()).hexdigest(),
                id=entry.id,
            )

            # Link related concepts
            for related_id in entry.related_ids:
                await session.run(
                    """
                    MATCH (c1:Concept {id: $id}), (c2:Concept {id: $related_id})
                    MERGE (c1)-[r:RELATED_TO]->(c2)
                    ON CREATE SET r.weight = 1.0, r.created_at = $now
                    ON MATCH SET r.weight = r.weight + 0.1
                    """,
                    id=entry.id,
                    related_id=related_id,
                    now=now_ts,
                )

        return version_ref

    # ── Read ──────────────────────────────────────────────────────────────────

    async def search(
        self,
        agent_id: str,
        query: str,
        top_k: int = 10,
        min_importance: float = 0.0,
        hop_depth: int = 1,
    ) -> list[tuple[MemoryEntry, float]]:
        """
        Full-text search on content + 1-hop relationship expansion.
        Score = importance × (1 + 0.5 × hop_penalty).
        """
        async with self.driver.session() as session:
            # Full-text match (uses Neo4j CONTAINS — upgrade to FT index in prod)
            result = await session.run(
                """
                MATCH (a:Agent {id: $agent_id})-[:KNOWS]->(c:Concept)
                WHERE toLower(c.content) CONTAINS toLower($query)
                  AND c.importance >= $min_importance
                OPTIONAL MATCH (c)-[r:RELATED_TO]-(neighbor:Concept)
                RETURN c, collect({node: neighbor, weight: r.weight}) AS neighbors
                ORDER BY c.importance DESC
                LIMIT $top_k
                """,
                agent_id=agent_id,
                query=query,
                min_importance=min_importance,
                top_k=top_k * 2,  # fetch extra for re-ranking
            )

            entries: list[tuple[MemoryEntry, float]] = []
            seen: set[str] = set()

            async for record in result:
                node = record["c"]
                entry = self._node_to_entry(node, agent_id)
                if entry.id not in seen:
                    seen.add(entry.id)
                    score = float(node["importance"])
                    entries.append((entry, score))

                if hop_depth >= 1:
                    for nb_data in record["neighbors"]:
                        nb_node = nb_data["node"]
                        if nb_node is None:
                            continue
                        nb_entry = self._node_to_entry(nb_node, agent_id)
                        if nb_entry.id not in seen:
                            seen.add(nb_entry.id)
                            nb_score = float(nb_node.get("importance", 0)) * 0.7
                            entries.append((nb_entry, nb_score))

        # Re-rank by score, trim to top_k
        entries.sort(key=lambda x: x[1], reverse=True)
        return entries[:top_k]

    async def get_by_id(self, memory_id: str) -> MemoryEntry | None:
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH (c:Concept {id: $id}) RETURN c",
                id=memory_id,
            )
            record = await result.single()
            if record is None:
                return None
            node = record["c"]
            return self._node_to_entry(node, node["agent_id"])

    # ── PageRank / centrality ─────────────────────────────────────────────────

    async def get_pagerank_scores(
        self,
        agent_id: str,
        top_n: int = 50,
    ) -> dict[str, float]:
        """
        Run a lightweight in-graph PageRank for the agent's subgraph.
        Used by ImportanceScorer.cross_ref_count signal.
        Requires GDS plugin in prod; falls back to in-degree here.
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Agent {id: $agent_id})-[:KNOWS]->(c:Concept)
                OPTIONAL MATCH (other:Concept)-[:RELATED_TO]->(c)
                RETURN c.id AS id, count(other) AS in_degree
                ORDER BY in_degree DESC
                LIMIT $top_n
                """,
                agent_id=agent_id,
                top_n=top_n,
            )
            scores: dict[str, float] = {}
            async for record in result:
                max_degree = 20.0  # normalise
                scores[record["id"]] = min(record["in_degree"] / max_degree, 1.0)
        return scores

    # ── Versioning / Rollback ─────────────────────────────────────────────────

    async def list_versions(self, agent_id: str, limit: int = 20) -> list[dict]:
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (v:MemoryVersion {agent_id: $agent_id})
                RETURN v.ref AS ref, v.snapshot_at AS snapshot_at, v.content_hash AS hash
                ORDER BY v.snapshot_at DESC
                LIMIT $limit
                """,
                agent_id=agent_id,
                limit=limit,
            )
            rows = []
            async for record in result:
                rows.append({
                    "ref":         record["ref"],
                    "snapshot_at": record["snapshot_at"],
                    "hash":        record["hash"],
                })
        return rows

    async def rollback(self, agent_id: str, version_ref: str) -> int:
        """
        Delete all Concept nodes created after version_ref's snapshot_at.
        Returns count of nodes removed.
        """
        async with self.driver.session() as session:
            # Get snapshot_at of target version
            result = await session.run(
                "MATCH (v:MemoryVersion {ref: $ref}) RETURN v.snapshot_at AS ts",
                ref=version_ref,
            )
            record = await result.single()
            if record is None:
                raise ValueError(f"Version ref {version_ref!r} not found.")
            cutoff_ts = record["ts"]

            # Delete newer concepts
            result = await session.run(
                """
                MATCH (a:Agent {id: $agent_id})-[:KNOWS]->(c:Concept)
                WHERE c.created_at > $cutoff_ts
                DETACH DELETE c
                RETURN count(c) AS deleted
                """,
                agent_id=agent_id,
                cutoff_ts=cutoff_ts,
            )
            record = await result.single()
            return record["deleted"] if record else 0

    # ── Consolidation support ─────────────────────────────────────────────────

    async def upsert_consolidated_node(
        self,
        agent_id: str,
        cluster_id: str,
        synthesised_content: str,
        source_episode_ids: list[str],
        importance: float,
    ) -> str:
        """
        Called by the consolidation pipeline to write a synthesised
        semantic concept derived from episodic cluster.
        Returns the new concept's ID.
        """
        concept_id = f"consolidated:{cluster_id}"
        now_ts = int(datetime.now(timezone.utc).timestamp())

        async with self.driver.session() as session:
            await session.run(
                """
                MERGE (c:Concept {id: $id})
                ON CREATE SET
                    c.agent_id   = $agent_id,
                    c.content    = $content,
                    c.importance = $importance,
                    c.created_at = $now,
                    c.updated_at = $now,
                    c.type       = 'consolidated',
                    c.session_id = 'consolidation'
                ON MATCH SET
                    c.content    = $content,
                    c.importance = $importance,
                    c.updated_at = $now
                WITH c
                MATCH (a:Agent {id: $agent_id})
                MERGE (a)-[:KNOWS]->(c)
                """,
                id=concept_id,
                agent_id=agent_id,
                content=synthesised_content,
                importance=importance,
                now=now_ts,
            )
            # Link source episodes as DERIVED_FROM
            for ep_id in source_episode_ids:
                await session.run(
                    """
                    MATCH (c:Concept {id: $concept_id})
                    MERGE (ep:EpisodeRef {id: $ep_id})
                    MERGE (c)-[:DERIVED_FROM]->(ep)
                    """,
                    concept_id=concept_id,
                    ep_id=ep_id,
                )

        return concept_id

    # ── Health ────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        try:
            async with self.driver.session() as session:
                await session.run("RETURN 1")
            return True
        except Exception:
            return False

    async def stats(self, agent_id: str) -> dict:
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Agent {id: $agent_id})-[:KNOWS]->(c:Concept)
                RETURN count(c) AS node_count
                """,
                agent_id=agent_id,
            )
            record = await result.single()
        return {
            "tier": "semantic",
            "agent_id": agent_id,
            "concept_count": record["node_count"] if record else 0,
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _node_to_entry(node: Any, agent_id: str) -> MemoryEntry:
        mtype_val = node.get("type", 4)  # default FACT
        if isinstance(mtype_val, str):
            type_map = {
                "consolidated": MemoryType.REFLECTION,
                "fact": MemoryType.FACT,
            }
            mtype = type_map.get(mtype_val, MemoryType.FACT)
        else:
            mtype = MemoryType(int(mtype_val))

        created_ts = node.get("created_at", 0)
        return MemoryEntry(
            id=node["id"],
            agent_id=agent_id,
            session_id=node.get("session_id", ""),
            content=node["content"],
            type=mtype,
            tier=MemoryTier.SEMANTIC,
            importance=float(node.get("importance", 0.0)),
            created_at=datetime.fromtimestamp(created_ts, tz=timezone.utc),
        )
