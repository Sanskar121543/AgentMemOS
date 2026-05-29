"""
tests/test_property_based.py
────────────────────────────
Property-based tests (Hypothesis) over the pure-logic core: importance
scoring, the eviction score, model invariants and the router. These assert
mathematical properties that must hold for *all* inputs, not just examples.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hypothesis import assume, given
from hypothesis import strategies as st

from agentmemos.core.importance import (
    ImportanceScorer,
    _cross_ref_score,
    _outcome_salience,
    _recency_score,
)
from agentmemos.core.models import (
    MemoryEntry,
    MemoryTier,
    MemoryType,
    WriteRequest,
)
from agentmemos.core.router import MemoryRouter
from agentmemos.eviction.semantic_lru import _centrality_score

UTC = timezone.utc


# ── Importance signal bounds ────────────────────────────────────────────────

@given(age_hours=st.floats(min_value=0, max_value=10_000))
def test_recency_score_always_in_unit_interval(age_hours):
    created = datetime.now(UTC) - timedelta(hours=age_hours)
    s = _recency_score(created)
    assert 0.0 <= s <= 1.0


@given(age_a=st.floats(min_value=0, max_value=1000),
       age_b=st.floats(min_value=0, max_value=1000))
def test_recency_is_monotonic_decreasing(age_a, age_b):
    assume(abs(age_a - age_b) > 1e-6)
    now = datetime.now(UTC)
    sa = _recency_score(now - timedelta(hours=age_a))
    sb = _recency_score(now - timedelta(hours=age_b))
    # older (larger age) must score no higher
    if age_a < age_b:
        assert sa >= sb
    else:
        assert sb >= sa


@given(refs=st.integers(min_value=0, max_value=100_000))
def test_cross_ref_score_bounded(refs):
    assert 0.0 <= _cross_ref_score(refs) <= 1.0


@given(deg=st.integers(min_value=0, max_value=100_000))
def test_centrality_score_bounded(deg):
    assert 0.0 <= _centrality_score(deg) <= 1.0


@given(success=st.sampled_from([True, False, None]),
       magnitude=st.floats(min_value=-5, max_value=5))
def test_outcome_salience_bounded(success, magnitude):
    assert 0.0 <= _outcome_salience(success, magnitude) <= 1.0


# ── Composite score invariant ───────────────────────────────────────────────

@given(
    content=st.text(min_size=1, max_size=200),
    ref_count=st.integers(min_value=0, max_value=500),
    success=st.sampled_from([True, False, None]),
)
def test_composite_importance_in_unit_interval(content, ref_count, success):
    assume(content.strip())
    entry = MemoryEntry(
        agent_id="a", session_id="s", content=content,
        type=MemoryType.OBSERVATION, tier=MemoryTier.WORKING,
    )
    _, score = ImportanceScorer().score(entry, ref_count=ref_count, success=success)
    assert 0.0 <= score <= 1.0
    assert entry.importance == score


# ── Model invariants ────────────────────────────────────────────────────────

@given(content=st.text(min_size=1, max_size=100))
def test_content_is_stripped(content):
    assume(content.strip())
    e = MemoryEntry(agent_id="a", session_id="s", content=f"  {content}  ",
                    type=MemoryType.FACT, tier=MemoryTier.SEMANTIC)
    assert e.content == content.strip()


@given(content=st.text(min_size=1, max_size=100))
def test_updated_never_before_created(content):
    assume(content.strip())
    e = MemoryEntry(agent_id="a", session_id="s", content=content,
                    type=MemoryType.FACT, tier=MemoryTier.SEMANTIC)
    assert e.updated_at >= e.created_at


# ── Router total-function property ──────────────────────────────────────────

@given(
    content=st.text(min_size=1, max_size=300),
    mtype=st.sampled_from(list(MemoryType)),
    importance=st.floats(min_value=0.0, max_value=1.0),
)
def test_router_always_returns_valid_tier(content, mtype, importance):
    assume(content.strip())
    req = WriteRequest(agent_id="a", session_id="s", content=content, type=mtype)
    routing = MemoryRouter().route_write(req, importance)
    assert routing.tier in set(MemoryTier)
    assert routing.reason
