"""
agentmemos.core.importance
──────────────────────────
Five-signal importance scorer that decides which memories are worth
promoting to long-term storage.

Signals
-------
1. recency_score    — exponential decay from creation time
2. cross_ref_count  — PageRank-style graph reference count (Neo4j)
3. outcome_salience — did subsequent actions succeed?
4. agent_confidence — confidence at formation time
5. semantic_novelty — cosine distance from nearest semantic neighbour

The composite score uses learned weights stored in the agent's
procedural tier. Agents without calibrated weights fall back to
the defaults defined in DEFAULT_WEIGHTS.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from agentmemos.core.models import ImportanceSignals, MemoryEntry

# ─────────────────────────────────────────────────────────────────────────────
# Weight Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ImportanceWeights:
    recency:     float = 0.20
    cross_ref:   float = 0.25
    salience:    float = 0.25
    confidence:  float = 0.10
    novelty:     float = 0.20

    def __post_init__(self) -> None:
        total = (
            self.recency + self.cross_ref + self.salience
            + self.confidence + self.novelty
        )
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(f"Weights must sum to 1.0, got {total:.4f}")


DEFAULT_WEIGHTS = ImportanceWeights()

# Dynamic threshold per agent — memories below this are not promoted.
# Adjusted up/down based on storage pressure reported by each tier.
DEFAULT_THRESHOLD: float = 0.45


# ─────────────────────────────────────────────────────────────────────────────
# Individual Signal Computations
# ─────────────────────────────────────────────────────────────────────────────

def _recency_score(created_at: datetime, half_life_hours: float = 24.0) -> float:
    """
    Exponential decay: score = exp(-λ·t)
    where λ = ln(2) / half_life and t is age in hours.
    """
    now = datetime.now(UTC)
    age_hours = (now - created_at).total_seconds() / 3600.0
    lam = math.log(2) / half_life_hours
    return math.exp(-lam * age_hours)


def _cross_ref_score(ref_count: int, max_expected: int = 50) -> float:
    """
    Normalised log scale: score = log(1 + ref_count) / log(1 + max_expected)
    Saturates smoothly so highly-referenced memories don't dominate.
    """
    if ref_count <= 0:
        return 0.0
    return math.log1p(ref_count) / math.log1p(max(max_expected, ref_count))


def _outcome_salience(
    success: bool | None,
    magnitude: float = 1.0,
) -> float:
    """
    Outcome signal:
      success=True   → 1.0 × magnitude   (remember what worked)
      success=False  → 0.8 × magnitude   (remember failures slightly less, but still important)
      success=None   → 0.3               (neutral / not yet evaluated)
    """
    if success is None:
        return 0.3
    base = 1.0 if success else 0.8
    return min(base * max(0.0, min(magnitude, 1.0)), 1.0)


def _semantic_novelty(
    embedding: list[float] | np.ndarray,
    neighbour_embeddings: Sequence[list[float] | np.ndarray],
) -> float:
    """
    1 - max_cosine_similarity(embedding, neighbour_embeddings).
    High value → the memory is novel vs existing semantic knowledge.
    If no neighbours exist yet, return 1.0 (fully novel).
    """
    if not neighbour_embeddings:
        return 1.0

    q = np.asarray(embedding, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return 0.5

    max_sim = 0.0
    for nb in neighbour_embeddings:
        n = np.asarray(nb, dtype=np.float32)
        n_norm = np.linalg.norm(n)
        if n_norm == 0:
            continue
        sim = float(np.dot(q, n) / (q_norm * n_norm))
        if sim > max_sim:
            max_sim = sim

    return 1.0 - max_sim


# ─────────────────────────────────────────────────────────────────────────────
# ImportanceScorer
# ─────────────────────────────────────────────────────────────────────────────

class ImportanceScorer:
    """
    Computes a composite importance score for a memory entry.

    Usage
    -----
    scorer = ImportanceScorer(weights=DEFAULT_WEIGHTS)
    signals, score = scorer.score(
        entry=entry,
        ref_count=3,
        success=True,
        magnitude=0.9,
        neighbour_embeddings=[...],
    )
    promoted = score >= scorer.threshold_for(agent_id)
    """

    def __init__(
        self,
        weights: ImportanceWeights = DEFAULT_WEIGHTS,
        half_life_hours: float = 24.0,
        base_threshold: float = DEFAULT_THRESHOLD,
    ) -> None:
        self._weights = weights
        self._half_life = half_life_hours
        self._base_threshold = base_threshold
        # Per-agent dynamic thresholds: agent_id → float
        self._agent_thresholds: dict[str, float] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def score(
        self,
        entry: MemoryEntry,
        ref_count: int = 0,
        success: bool | None = None,
        magnitude: float = 1.0,
        neighbour_embeddings: Sequence[list[float]] | None = None,
    ) -> tuple[ImportanceSignals, float]:
        """
        Returns (ImportanceSignals, composite_score).
        Mutates entry.signals and entry.importance in place.
        """
        r = _recency_score(entry.created_at, self._half_life)
        c = _cross_ref_score(ref_count)
        o = _outcome_salience(success, magnitude)
        a = float(entry.metadata.get("confidence", 0.8))
        n = _semantic_novelty(
            entry.embedding or [],
            neighbour_embeddings or [],
        )

        signals = ImportanceSignals(
            recency_score=r,
            cross_ref_count=c,
            outcome_salience=o,
            agent_confidence=a,
            semantic_novelty=n,
        )

        w = self._weights
        composite = (
            w.recency    * r
            + w.cross_ref  * c
            + w.salience   * o
            + w.confidence * a
            + w.novelty    * n
        )
        composite = min(composite, 1.0)

        entry.signals   = signals
        entry.importance = composite
        return signals, composite

    def should_promote(self, agent_id: str, score: float) -> bool:
        return score >= self.threshold_for(agent_id)

    def threshold_for(self, agent_id: str) -> float:
        return self._agent_thresholds.get(agent_id, self._base_threshold)

    def adjust_threshold(self, agent_id: str, delta: float) -> None:
        """
        Called by the storage pressure monitor to raise/lower the threshold
        dynamically — higher pressure → higher threshold → fewer promotions.
        """
        current = self.threshold_for(agent_id)
        self._agent_thresholds[agent_id] = max(0.1, min(0.9, current + delta))

    def calibrate(
        self,
        agent_id: str,
        weights: ImportanceWeights,
    ) -> None:
        """Hot-swap per-agent weights (loaded from procedural tier)."""
        self._weights = weights   # NOTE: this is global; per-agent weights TBD in v2
        self._agent_thresholds.pop(agent_id, None)

    # ── Batch scoring (consolidation pipeline) ────────────────────────────────

    def score_batch(
        self,
        entries: list[MemoryEntry],
        ref_counts: dict[str, int] | None = None,
        outcomes: dict[str, tuple[bool | None, float]] | None = None,
        neighbour_map: dict[str, list[list[float]]] | None = None,
    ) -> dict[str, float]:
        """
        Score multiple entries efficiently.
        Returns {memory_id: composite_score}.
        """
        ref_counts    = ref_counts    or {}
        outcomes      = outcomes      or {}
        neighbour_map = neighbour_map or {}

        scores: dict[str, float] = {}
        for entry in entries:
            success, magnitude = outcomes.get(entry.id, (None, 1.0))
            _, score = self.score(
                entry=entry,
                ref_count=ref_counts.get(entry.id, 0),
                success=success,
                magnitude=magnitude,
                neighbour_embeddings=neighbour_map.get(entry.id),
            )
            scores[entry.id] = score
        return scores
