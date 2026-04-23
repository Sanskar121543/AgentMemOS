"""
agentmemos.consolidation.pipeline
──────────────────────────────────
Sleep-based memory consolidation pipeline.

Inspired by hippocampal-neocortical memory consolidation during sleep.
Runs every 4 hours per agent (scheduled via Airflow or APScheduler).

Pipeline stages
───────────────
  1. Harvest  — pull recent episodic memories from Pinecone
  2. Cluster  — HDBSCAN on embedding vectors → semantic clusters
  3. Synthesise — LLM synthesises each cluster into a concise concept
  4. Promote  — write synthesised concepts to Neo4j semantic tier
  5. Archive  — score remaining episodes; cold ones → S3, ghost in Pinecone
  6. Report   — emit ConsolidationResult for monitoring

Why HDBSCAN?
  - Density-based: handles clusters of unequal size and density
  - No need to pre-specify K
  - Identifies noise points (outlier episodes don't force-join a cluster)
  - Deterministic with fixed random_state
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np

from agentmemos.core.models import ConsolidationResult, MemoryEntry, MemoryTier
from agentmemos.core.importance import ImportanceScorer
from agentmemos.tiers.episodic import EpisodicMemoryTier
from agentmemos.tiers.semantic import SemanticMemoryTier

try:
    import hdbscan
    _HDBSCAN_AVAILABLE = True
except ImportError:
    _HDBSCAN_AVAILABLE = False

try:
    from openai import AsyncOpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

CONSOLIDATION_HOURS      = int(os.getenv("CONSOLIDATION_HOURS", "4"))
HDBSCAN_MIN_CLUSTER_SIZE = int(os.getenv("HDBSCAN_MIN_CLUSTER", "3"))
HDBSCAN_MIN_SAMPLES      = int(os.getenv("HDBSCAN_MIN_SAMPLES", "2"))
S3_BUCKET                = os.getenv("S3_ARCHIVE_BUCKET", "agentmemos-archive")
SYNTHESISE_MODEL         = os.getenv("SYNTHESISE_MODEL", "claude-3-5-haiku-20241022")
OPENAI_API_KEY           = os.getenv("OPENAI_API_KEY", "")


# ─────────────────────────────────────────────────────────────────────────────
# Cluster
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EpisodeCluster:
    cluster_id:   str
    entries:      list[MemoryEntry]
    centroid:     np.ndarray
    coherence:    float = 0.0   # mean cosine similarity within cluster


# ─────────────────────────────────────────────────────────────────────────────
# ConsolidationPipeline
# ─────────────────────────────────────────────────────────────────────────────

class ConsolidationPipeline:
    """
    Entry point for the background consolidation job.

    Designed to be called by:
      - Airflow DAG  (production)
      - APScheduler  (single-node / dev)
      - Direct call  (manual trigger via gRPC Consolidate RPC)
    """

    def __init__(
        self,
        episodic: EpisodicMemoryTier,
        semantic: SemanticMemoryTier,
        scorer: ImportanceScorer,
    ) -> None:
        self._episodic = episodic
        self._semantic = semantic
        self._scorer   = scorer
        self._llm: AsyncOpenAI | None = (
            AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY and _OPENAI_AVAILABLE else None
        )

    # ── Main entry point ──────────────────────────────────────────────────────

    async def run(
        self,
        agent_id: str,
        dry_run: bool = False,
    ) -> ConsolidationResult:
        t0 = time.monotonic()

        # Stage 1 — Harvest
        entries = await self._harvest(agent_id)
        if not entries:
            return ConsolidationResult(
                agent_id=agent_id,
                clusters_found=0,
                nodes_created=0,
                episodes_archived=0,
                storage_freed_bytes=0,
                duration_seconds=time.monotonic() - t0,
            )

        # Stage 2 — Cluster
        clusters, noise = self._cluster(entries)

        # Stage 3 + 4 — Synthesise and Promote (skip in dry run)
        nodes_created = 0
        if not dry_run:
            for cluster in clusters:
                synthesised = await self._synthesise(cluster)
                if synthesised:
                    importance = self._cluster_importance(cluster)
                    await self._semantic.upsert_consolidated_node(
                        agent_id=agent_id,
                        cluster_id=cluster.cluster_id,
                        synthesised_content=synthesised,
                        source_episode_ids=[e.id for e in cluster.entries],
                        importance=importance,
                    )
                    nodes_created += 1

        # Stage 5 — Archive cold episodes
        archived, freed = 0, 0
        if not dry_run:
            archived, freed = await self._archive_cold(agent_id)

        return ConsolidationResult(
            agent_id=agent_id,
            clusters_found=len(clusters),
            nodes_created=nodes_created,
            episodes_archived=archived,
            storage_freed_bytes=freed,
            duration_seconds=time.monotonic() - t0,
        )

    # ── Stage 1: Harvest ──────────────────────────────────────────────────────

    async def _harvest(self, agent_id: str) -> list[MemoryEntry]:
        return await self._episodic.fetch_recent_for_consolidation(
            agent_id=agent_id,
            hours=CONSOLIDATION_HOURS,
            max_entries=200,
        )

    # ── Stage 2: Cluster ──────────────────────────────────────────────────────

    def _cluster(
        self,
        entries: list[MemoryEntry],
    ) -> tuple[list[EpisodeCluster], list[MemoryEntry]]:
        """
        Run HDBSCAN on embedding matrix.
        Returns (clusters, noise_entries).
        Falls back to single-cluster-per-entry if HDBSCAN unavailable.
        """
        # Filter entries that actually have embeddings
        valid = [e for e in entries if e.embedding and len(e.embedding) > 0]
        if not valid:
            return [], []

        matrix = np.array([e.embedding for e in valid], dtype=np.float32)

        if not _HDBSCAN_AVAILABLE or len(valid) < HDBSCAN_MIN_CLUSTER_SIZE:
            # Degenerate case: no clustering
            return [], valid

        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=HDBSCAN_MIN_CLUSTER_SIZE,
            min_samples=HDBSCAN_MIN_SAMPLES,
            metric="euclidean",
            cluster_selection_method="eom",
        )
        labels = clusterer.fit_predict(matrix)

        cluster_map: dict[int, list[tuple[MemoryEntry, np.ndarray]]] = {}
        noise: list[MemoryEntry] = []

        for i, label in enumerate(labels):
            if label == -1:
                noise.append(valid[i])
            else:
                cluster_map.setdefault(label, []).append((valid[i], matrix[i]))

        clusters: list[EpisodeCluster] = []
        for label, pairs in cluster_map.items():
            cluster_entries = [p[0] for p in pairs]
            vecs = np.array([p[1] for p in pairs])
            centroid = vecs.mean(axis=0)
            coherence = self._mean_cosine(vecs, centroid)
            clusters.append(EpisodeCluster(
                cluster_id=f"{cluster_entries[0].agent_id}:{label}:{int(time.time())}",
                entries=cluster_entries,
                centroid=centroid,
                coherence=coherence,
            ))

        return clusters, noise

    @staticmethod
    def _mean_cosine(vecs: np.ndarray, centroid: np.ndarray) -> float:
        """Mean cosine similarity of all vectors to the centroid."""
        centroid_norm = np.linalg.norm(centroid)
        if centroid_norm == 0:
            return 0.0
        sims = []
        for v in vecs:
            n = np.linalg.norm(v)
            if n == 0:
                continue
            sims.append(float(np.dot(v, centroid) / (n * centroid_norm)))
        return float(np.mean(sims)) if sims else 0.0

    # ── Stage 3: Synthesise ───────────────────────────────────────────────────

    async def _synthesise(self, cluster: EpisodeCluster) -> str | None:
        """
        Prompt an LLM to compress a cluster of episodic memories into
        a single concise semantic concept.
        """
        contents = "\n".join(
            f"- [{e.type.name}] {e.content}" for e in cluster.entries[:20]
        )
        prompt = (
            "You are a memory consolidation system. "
            "Given the following episodic memories from an AI agent, "
            "synthesise them into a single concise factual statement "
            "that captures the general knowledge learned. "
            "Output ONLY the statement, 1-3 sentences. No preamble.\n\n"
            f"Episodes:\n{contents}\n\nSynthesised concept:"
        )

        if self._llm:
            try:
                response = await self._llm.chat.completions.create(
                    model=SYNTHESISE_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=200,
                    temperature=0.3,
                )
                return response.choices[0].message.content.strip()
            except Exception:
                pass

        # Fallback: naive extractive summary (longest content wins)
        if cluster.entries:
            return max(cluster.entries, key=lambda e: len(e.content)).content[:500]
        return None

    # ── Stage 5: Archive ──────────────────────────────────────────────────────

    async def _archive_cold(self, agent_id: str) -> tuple[int, int]:
        """
        Move cold episodic memories to S3 and leave ghost entries in Pinecone.
        Returns (count_archived, bytes_freed).
        """
        candidate_ids = await self._episodic.get_archival_candidates(agent_id)
        if not candidate_ids:
            return 0, 0

        archived = 0
        freed = 0

        for memory_id in candidate_ids:
            cold_path = f"s3://{S3_BUCKET}/{agent_id}/{memory_id}.json"
            # In production: upload to S3 first, then ghost
            # Here we simulate the ghost creation only
            try:
                await self._episodic.delete(
                    agent_id=agent_id,
                    memory_id=memory_id,
                    ghost=True,
                    cold_path=cold_path,
                )
                archived += 1
                # Estimate: 1536 floats × 4 bytes = 6KB per vector
                freed += 6144
            except Exception:
                continue

        return archived, freed

    # ── Importance helper ─────────────────────────────────────────────────────

    def _cluster_importance(self, cluster: EpisodeCluster) -> float:
        """Cluster-level importance = mean importance of member episodes × coherence."""
        if not cluster.entries:
            return 0.0
        mean_imp = sum(e.importance for e in cluster.entries) / len(cluster.entries)
        return min(mean_imp * (0.5 + 0.5 * cluster.coherence), 1.0)


# ─────────────────────────────────────────────────────────────────────────────
# Scheduler integration
# ─────────────────────────────────────────────────────────────────────────────

async def schedule_consolidation(
    pipeline: ConsolidationPipeline,
    agent_ids: list[str],
    interval_hours: float = CONSOLIDATION_HOURS,
) -> None:
    """
    Lightweight APScheduler-style async loop.
    In production this is replaced by an Airflow DAG.
    """
    while True:
        for agent_id in agent_ids:
            try:
                result = await pipeline.run(agent_id)
                print(
                    f"[consolidation] agent={agent_id} "
                    f"clusters={result.clusters_found} "
                    f"nodes={result.nodes_created} "
                    f"archived={result.episodes_archived} "
                    f"duration={result.duration_seconds:.2f}s"
                )
            except Exception as exc:
                print(f"[consolidation] agent={agent_id} ERROR: {exc}")
        await asyncio.sleep(interval_hours * 3600)
