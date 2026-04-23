"""
tests/test_core.py
──────────────────
Unit tests for MemoryRouter, ImportanceScorer, and core models.
No external dependencies required — all tests run in-process.

Run with:  pytest tests/test_core.py -v
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

import pytest

from agentmemos.core.importance import (
    ImportanceScorer,
    ImportanceWeights,
    _recency_score,
    _cross_ref_score,
    _outcome_salience,
    _semantic_novelty,
    DEFAULT_WEIGHTS,
)
from agentmemos.core.models import (
    MemoryEntry,
    MemoryTier,
    MemoryType,
    ImportanceSignals,
    ReadRequest,
    WriteRequest,
)
from agentmemos.core.router import MemoryRouter, RouterConfig


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def router() -> MemoryRouter:
    return MemoryRouter()


@pytest.fixture
def scorer() -> ImportanceScorer:
    return ImportanceScorer()


def make_entry(
    content: str = "test content",
    mtype: MemoryType = MemoryType.OBSERVATION,
    tier: MemoryTier = MemoryTier.WORKING,
    importance: float = 0.5,
    age_hours: float = 0.0,
) -> MemoryEntry:
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return MemoryEntry(
        agent_id="agent-test",
        session_id="session-001",
        content=content,
        type=mtype,
        tier=tier,
        importance=importance,
        created_at=created,
        updated_at=created,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Importance Scorer — individual signals
# ─────────────────────────────────────────────────────────────────────────────

class TestRecencyScore:
    def test_fresh_entry_scores_high(self):
        dt = datetime.now(timezone.utc)
        score = _recency_score(dt, half_life_hours=24.0)
        assert score > 0.95

    def test_one_half_life_decays_to_half(self):
        dt = datetime.now(timezone.utc) - timedelta(hours=24)
        score = _recency_score(dt, half_life_hours=24.0)
        assert math.isclose(score, 0.5, abs_tol=0.01)

    def test_old_entry_scores_low(self):
        dt = datetime.now(timezone.utc) - timedelta(days=7)
        score = _recency_score(dt, half_life_hours=24.0)
        assert score < 0.05

    def test_score_bounded(self):
        for hours in [0, 1, 6, 24, 72, 168]:
            dt = datetime.now(timezone.utc) - timedelta(hours=hours)
            s = _recency_score(dt)
            assert 0.0 <= s <= 1.0


class TestCrossRefScore:
    def test_zero_refs_returns_zero(self):
        assert _cross_ref_score(0) == 0.0

    def test_max_expected_returns_one(self):
        score = _cross_ref_score(50, max_expected=50)
        assert math.isclose(score, 1.0, abs_tol=0.01)

    def test_log_scale(self):
        s1 = _cross_ref_score(1)
        s5 = _cross_ref_score(5)
        s50 = _cross_ref_score(50)
        assert s1 < s5 < s50

    def test_saturates_above_max(self):
        s_at_max  = _cross_ref_score(50)
        s_over_max = _cross_ref_score(200)
        assert s_over_max <= 1.0
        assert s_over_max >= s_at_max


class TestOutcomeSalience:
    def test_success_returns_high(self):
        assert _outcome_salience(True) == 1.0

    def test_failure_returns_slightly_less(self):
        s = _outcome_salience(False)
        assert 0.7 < s < 1.0

    def test_none_returns_neutral(self):
        s = _outcome_salience(None)
        assert math.isclose(s, 0.3, abs_tol=0.01)

    def test_magnitude_scales(self):
        s_full = _outcome_salience(True, magnitude=1.0)
        s_half = _outcome_salience(True, magnitude=0.5)
        assert s_full > s_half

    def test_bounded(self):
        for success in [True, False, None]:
            for mag in [0.0, 0.5, 1.0]:
                s = _outcome_salience(success, mag)
                assert 0.0 <= s <= 1.0


class TestSemanticNovelty:
    def test_no_neighbours_returns_one(self):
        emb = [0.1, 0.2, 0.3]
        assert _semantic_novelty(emb, []) == 1.0

    def test_identical_embedding_returns_zero(self):
        emb = [1.0, 0.0, 0.0]
        score = _semantic_novelty(emb, [emb])
        assert score < 0.01

    def test_orthogonal_embedding_returns_high(self):
        emb  = [1.0, 0.0, 0.0]
        nb   = [0.0, 1.0, 0.0]
        score = _semantic_novelty(emb, [nb])
        assert score > 0.9

    def test_partial_similarity(self):
        emb = [0.7, 0.7, 0.0]
        nb  = [1.0, 0.0, 0.0]
        score = _semantic_novelty(emb, [nb])
        assert 0.2 < score < 0.8


# ─────────────────────────────────────────────────────────────────────────────
# Importance Scorer — composite
# ─────────────────────────────────────────────────────────────────────────────

class TestImportanceScorer:
    def test_fresh_high_confidence_scores_high(self, scorer):
        entry = make_entry(importance=0.0)
        entry.metadata["confidence"] = 0.95
        _, score = scorer.score(entry, ref_count=5, success=True)
        assert score > 0.5

    def test_stale_no_refs_scores_low(self, scorer):
        entry = make_entry(age_hours=168, importance=0.0)
        entry.metadata["confidence"] = 0.3
        _, score = scorer.score(entry, ref_count=0, success=False)
        assert score < 0.5

    def test_score_sets_entry_importance(self, scorer):
        entry = make_entry()
        assert entry.importance == 0.5  # initial
        _, score = scorer.score(entry)
        assert entry.importance == score

    def test_score_sets_signals(self, scorer):
        entry = make_entry()
        signals, _ = scorer.score(entry)
        assert isinstance(signals, ImportanceSignals)
        assert 0.0 <= signals.recency_score <= 1.0

    def test_batch_score(self, scorer):
        entries = [make_entry(content=f"content {i}") for i in range(5)]
        scores = scorer.score_batch(entries)
        assert len(scores) == 5
        for eid, score in scores.items():
            assert 0.0 <= score <= 1.0

    def test_threshold_adjustment(self, scorer):
        agent_id = "agent-x"
        base = scorer.threshold_for(agent_id)
        scorer.adjust_threshold(agent_id, +0.1)
        assert scorer.threshold_for(agent_id) > base
        scorer.adjust_threshold(agent_id, -0.5)
        assert scorer.threshold_for(agent_id) >= 0.1  # clamped

    def test_custom_weights(self):
        weights = ImportanceWeights(
            recency=0.5, cross_ref=0.1, salience=0.2, confidence=0.1, novelty=0.1
        )
        scorer = ImportanceScorer(weights=weights)
        entry = make_entry()
        _, score = scorer.score(entry)
        assert 0.0 <= score <= 1.0

    def test_invalid_weights_raise(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            ImportanceWeights(recency=0.9, cross_ref=0.1, salience=0.5,
                              confidence=0.1, novelty=0.1)


# ─────────────────────────────────────────────────────────────────────────────
# MemoryRouter — write routing
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryRouterWrite:
    def test_fact_type_routes_to_semantic(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="The capital of France is Paris",
            type=MemoryType.FACT,
        )
        routing = router.route_write(req)
        assert routing.tier == MemoryTier.SEMANTIC

    def test_procedure_type_routes_to_procedural(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="How to call the weather API",
            type=MemoryType.PROCEDURE,
        )
        routing = router.route_write(req)
        assert routing.tier == MemoryTier.PROCEDURAL

    def test_caller_override_respected(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="anything",
            type=MemoryType.OBSERVATION,
            target_tier=MemoryTier.EPISODIC,
        )
        routing = router.route_write(req)
        assert routing.tier == MemoryTier.EPISODIC
        assert routing.reason == "caller_override"

    def test_episodic_heuristic(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="The action failed and returned an error earlier in this session",
            type=MemoryType.OUTCOME,
        )
        routing = router.route_write(req, importance=0.6)
        assert routing.tier == MemoryTier.EPISODIC

    def test_low_signal_defaults_to_working(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="ok",
            type=MemoryType.OBSERVATION,
        )
        routing = router.route_write(req, importance=0.1)
        assert routing.tier == MemoryTier.WORKING

    def test_procedural_heuristic(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="Steps to authenticate: first call the token endpoint, then attach the bearer token",
            type=MemoryType.OBSERVATION,
        )
        routing = router.route_write(req, importance=0.7)
        assert routing.tier == MemoryTier.PROCEDURAL


# ─────────────────────────────────────────────────────────────────────────────
# MemoryRouter — read routing
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryRouterRead:
    def test_default_includes_working(self, router):
        req = ReadRequest(
            agent_id="a1",
            session_id="s1",
            query="anything",
        )
        routing = router.route_read(req)
        assert MemoryTier.WORKING in routing.tiers

    def test_caller_specified_tiers_respected(self, router):
        req = ReadRequest(
            agent_id="a1",
            session_id="s1",
            query="facts",
            tiers=[MemoryTier.SEMANTIC],
        )
        routing = router.route_read(req)
        assert routing.tiers == [MemoryTier.SEMANTIC]

    def test_deep_recall_fans_out_to_all(self, router):
        req = ReadRequest(
            agent_id="a1",
            session_id="s1",
            query="remember everything I learned about authentication",
        )
        routing = router.route_read(req)
        assert len(routing.tiers) == 4

    def test_weights_sum_to_one(self, router):
        req = ReadRequest(
            agent_id="a1",
            session_id="s1",
            query="what happened when I called the API",
        )
        routing = router.route_read(req)
        total = sum(routing.weights.values())
        assert math.isclose(total, 1.0, abs_tol=0.01)

    def test_explain_write(self, router):
        req = WriteRequest(
            agent_id="a1",
            session_id="s1",
            content="I called the API and it returned 200",
            type=MemoryType.OUTCOME,
        )
        explanation = router.explain_write(req, importance=0.6)
        assert "routed_to" in explanation
        assert "signals" in explanation
        assert all(0.0 <= v <= 1.0 for v in explanation["signals"].values()
                   if isinstance(v, float))


# ─────────────────────────────────────────────────────────────────────────────
# Core Models
# ─────────────────────────────────────────────────────────────────────────────

class TestMemoryEntry:
    def test_id_auto_generated(self):
        e1 = make_entry()
        e2 = make_entry()
        assert e1.id != e2.id

    def test_content_stripped(self):
        e = MemoryEntry(
            agent_id="a", session_id="s",
            content="  hello world  ",
            type=MemoryType.OBSERVATION,
            tier=MemoryTier.WORKING,
        )
        assert e.content == "hello world"

    def test_namespace_key(self):
        e = make_entry()
        key = e.namespace_key()
        assert e.agent_id in key
        assert e.id in key

    def test_model_roundtrip(self):
        e = make_entry(content="roundtrip test")
        json_str = e.model_dump_json()
        restored = MemoryEntry.model_validate_json(json_str)
        assert restored.id == e.id
        assert restored.content == e.content
