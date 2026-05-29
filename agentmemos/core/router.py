"""
agentmemos.core.router
──────────────────────
MemoryRouter: lightweight classifier that decides which memory tier(s)
to read from or write to based on query intent, recency bias, and
the importance score of the incoming memory.

Write path
----------
Every write goes through classify_write() → MemoryTier.
Writes are always non-blocking async — the caller gets a WriteResponse
immediately; actual persistence is fire-and-forget to the target tier.

Read path
---------
classify_read() returns an ordered list of tiers to fan out to.
Results from all tiers are fused by the cross-encoder re-ranker in the
ReadFuser before being returned to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agentmemos.core.models import (
    MemoryTier,
    MemoryType,
    ReadRequest,
    RouterIntent,
    WriteRequest,
)

# ─────────────────────────────────────────────────────────────────────────────
# Heuristic rule-sets
# ─────────────────────────────────────────────────────────────────────────────

_EPISODIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(happened|occurred|did|performed|executed|ran|called|returned|failed)\b", re.I),
    re.compile(r"\b(yesterday|earlier|last session|previously|before)\b", re.I),
    re.compile(r"\b(action|step|attempt|try|result|outcome)\b", re.I),
]

_SEMANTIC_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(is|are|means|defined as|know that|fact|concept|principle)\b", re.I),
    re.compile(r"\b(always|never|generally|typically|usually)\b", re.I),
    re.compile(r"\b(relationship|connected|linked|associated)\b", re.I),
]

_PROCEDURAL_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(how to|steps to|procedure|workflow|pipeline|sequence|tool call)\b", re.I),
    re.compile(r"\b(repeat|reuse|template|pattern)\b", re.I),
]

_RECALL_DEEP_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(everything|all|comprehensive|complete|full context)\b", re.I),
    re.compile(r"\b(remember|recall|know|learned|experienced)\b", re.I),
]


def _pattern_score(text: str, patterns: list[re.Pattern]) -> float:
    """Returns fraction of patterns matched (0.0 – 1.0)."""
    if not patterns:
        return 0.0
    hits = sum(1 for p in patterns if p.search(text))
    return hits / len(patterns)


# ─────────────────────────────────────────────────────────────────────────────
# RouterConfig
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RouterConfig:
    # Minimum importance score required to route to EPISODIC (else WORKING)
    episodic_min_importance: float = 0.30
    # Minimum importance score required to route to SEMANTIC
    semantic_min_importance: float = 0.55
    # Fan-out read: include PROCEDURAL if query looks like a how-to
    procedural_read_threshold: float = 0.25
    # Weight of heuristic vs importance in write routing decision
    heuristic_weight: float = 0.6
    importance_weight: float = 0.4


# ─────────────────────────────────────────────────────────────────────────────
# Routing Result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WriteRouting:
    tier:    MemoryTier
    intent:  RouterIntent
    reason:  str


@dataclass
class ReadRouting:
    tiers:   list[MemoryTier]
    intent:  RouterIntent
    weights: dict[MemoryTier, float] = field(default_factory=dict)
    """Per-tier relevance weight used during result fusion."""


# ─────────────────────────────────────────────────────────────────────────────
# MemoryRouter
# ─────────────────────────────────────────────────────────────────────────────

class MemoryRouter:
    """
    Stateless, synchronous classifier.  All async work happens in the
    tier clients after routing is decided.
    """

    def __init__(self, config: RouterConfig | None = None) -> None:
        self._cfg = config or RouterConfig()

    # ── Write Routing ─────────────────────────────────────────────────────────

    def route_write(
        self,
        request: WriteRequest,
        importance: float | None = None,
    ) -> WriteRouting:
        """
        Decide which tier to write to.

        Priority order:
        1. Caller-specified target_tier (override)
        2. MemoryType hints
        3. Heuristic pattern matching on content
        4. Importance score gating
        """
        # Hard override from caller
        if request.target_tier is not None:
            return WriteRouting(
                tier=request.target_tier,
                intent=RouterIntent.SHORT_TERM_STORE,
                reason="caller_override",
            )

        # Type-based fast path
        type_routing = self._type_based_write(request.type)
        if type_routing is not None:
            return type_routing

        # Heuristic + importance blend
        return self._heuristic_write(request, importance or 0.5)

    def _type_based_write(self, mtype: MemoryType) -> WriteRouting | None:
        mapping = {
            MemoryType.FACT:      (MemoryTier.SEMANTIC,   RouterIntent.LEARN_FACT),
            MemoryType.PROCEDURE: (MemoryTier.PROCEDURAL, RouterIntent.LEARN_PROCEDURE),
            MemoryType.REFLECTION:(MemoryTier.SEMANTIC,   RouterIntent.LEARN_FACT),
        }
        if mtype in mapping:
            tier, intent = mapping[mtype]
            return WriteRouting(tier=tier, intent=intent, reason="type_hint")
        return None

    def _heuristic_write(
        self,
        request: WriteRequest,
        importance: float,
    ) -> WriteRouting:
        content = request.content
        cfg = self._cfg

        ep_score  = _pattern_score(content, _EPISODIC_PATTERNS)
        sem_score = _pattern_score(content, _SEMANTIC_PATTERNS)
        pro_score = _pattern_score(content, _PROCEDURAL_PATTERNS)

        best = max(ep_score, sem_score, pro_score)

        # Fallback to WORKING if heuristics are weak
        if best < 0.15:
            return WriteRouting(
                tier=MemoryTier.WORKING,
                intent=RouterIntent.SHORT_TERM_STORE,
                reason="low_signal_fallback",
            )

        # Procedural wins if it's the strongest signal
        if pro_score == best:
            return WriteRouting(
                tier=MemoryTier.PROCEDURAL,
                intent=RouterIntent.LEARN_PROCEDURE,
                reason="procedural_heuristic",
            )

        # Semantic: strong pattern AND high importance
        if sem_score == best and importance >= cfg.semantic_min_importance:
            return WriteRouting(
                tier=MemoryTier.SEMANTIC,
                intent=RouterIntent.LEARN_FACT,
                reason="semantic_heuristic+importance",
            )

        # Episodic: pattern match AND importance above floor
        if ep_score >= 0.2 and importance >= cfg.episodic_min_importance:
            return WriteRouting(
                tier=MemoryTier.EPISODIC,
                intent=RouterIntent.EPISODE_STORE,
                reason="episodic_heuristic+importance",
            )

        # Default: stash in working memory
        return WriteRouting(
            tier=MemoryTier.WORKING,
            intent=RouterIntent.SHORT_TERM_STORE,
            reason="default",
        )

    # ── Read Routing ──────────────────────────────────────────────────────────

    def route_read(self, request: ReadRequest) -> ReadRouting:
        """
        Decide which tier(s) to fan out to for a read query.
        Returns tiers in priority order with per-tier weights.
        """
        # Caller has already specified tiers
        if request.tiers:
            return ReadRouting(
                tiers=request.tiers,
                intent=RouterIntent.RECALL_RECENT,
                weights={t: 1.0 / len(request.tiers) for t in request.tiers},
            )

        query = request.query
        cfg   = self._cfg

        deep_score = _pattern_score(query, _RECALL_DEEP_PATTERNS)
        pro_score  = _pattern_score(query, _PROCEDURAL_PATTERNS)
        sem_score  = _pattern_score(query, _SEMANTIC_PATTERNS)
        ep_score   = _pattern_score(query, _EPISODIC_PATTERNS)

        # Deep recall → fan out to all tiers
        if deep_score >= 0.3:
            tiers = [
                MemoryTier.WORKING,
                MemoryTier.EPISODIC,
                MemoryTier.SEMANTIC,
                MemoryTier.PROCEDURAL,
            ]
            weights = {
                MemoryTier.WORKING:    0.15,
                MemoryTier.EPISODIC:   0.35,
                MemoryTier.SEMANTIC:   0.35,
                MemoryTier.PROCEDURAL: 0.15,
            }
            return ReadRouting(tiers=tiers, intent=RouterIntent.RECALL_DEEP, weights=weights)

        tiers: list[MemoryTier] = []
        weights: dict[MemoryTier, float] = {}

        # Working memory is always included (recent context)
        tiers.append(MemoryTier.WORKING)
        weights[MemoryTier.WORKING] = 0.20

        if ep_score >= 0.15:
            tiers.append(MemoryTier.EPISODIC)
            weights[MemoryTier.EPISODIC] = 0.40

        if sem_score >= 0.15:
            tiers.append(MemoryTier.SEMANTIC)
            weights[MemoryTier.SEMANTIC] = 0.30

        if pro_score >= cfg.procedural_read_threshold:
            tiers.append(MemoryTier.PROCEDURAL)
            weights[MemoryTier.PROCEDURAL] = 0.25

        # Re-normalise weights
        total = sum(weights.values())
        if total > 0:
            weights = {t: w / total for t, w in weights.items()}

        intent = (
            RouterIntent.RECALL_RECENT
            if len(tiers) <= 2
            else RouterIntent.RECALL_DEEP
        )

        return ReadRouting(tiers=tiers, intent=intent, weights=weights)

    # ── Utility ───────────────────────────────────────────────────────────────

    def explain_write(self, request: WriteRequest, importance: float = 0.5) -> dict:
        """Debug helper — returns routing decision with signal breakdown."""
        routing = self.route_write(request, importance)
        content = request.content
        return {
            "routed_to":    routing.tier.name,
            "intent":       routing.intent.name,
            "reason":       routing.reason,
            "signals": {
                "episodic":   _pattern_score(content, _EPISODIC_PATTERNS),
                "semantic":   _pattern_score(content, _SEMANTIC_PATTERNS),
                "procedural": _pattern_score(content, _PROCEDURAL_PATTERNS),
                "importance": importance,
            },
        }
