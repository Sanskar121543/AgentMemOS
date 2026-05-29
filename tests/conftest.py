"""
tests/conftest.py
─────────────────
Offline test infrastructure.

Provides in-memory fakes for every storage tier so the gRPC servicer and
the FastAPI admin API can be exercised end-to-end without Redis, Neo4j,
Pinecone, PostgreSQL or Kafka. This is what lets the full suite run in CI.

The fakes implement only the surface the MemoryServicer / REST layer touch,
but they implement it faithfully (ordering, importance gating, version refs)
so the tests assert real behaviour rather than mock call-counts.
"""

from __future__ import annotations

import uuid

import pytest

from agentmemos.core.embeddings import EmbeddingService
from agentmemos.core.importance import ImportanceScorer
from agentmemos.core.models import MemoryEntry, MemoryTier
from agentmemos.core.router import MemoryRouter
from agentmemos.federation.policy import FederationPolicyEngine, PolicyStore


# ── Deterministic, offline embedding service ────────────────────────────────

class FakeEmbeddingService(EmbeddingService):
    """Hash-seeded deterministic embeddings; no network, no model download."""

    DIM = 16

    def __init__(self) -> None:  # noqa: D401 - intentionally bypass super().__init__
        self._cache = {}
        self._cache_order = []
        self._openai = None
        self._local = None

    async def initialise(self) -> None:
        return None

    async def embed(self, text: str) -> list[float]:
        h = abs(hash(text))
        vec = [((h >> (i * 3)) & 0xFF) / 255.0 for i in range(self.DIM)]
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    async def embed_batch(self, texts, quantize: bool = False):
        return [await self.embed(t) for t in texts]


# ── Tier fakes ──────────────────────────────────────────────────────────────

class FakeWorking:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], list[MemoryEntry]] = {}
        self.healthy = True

    async def write(self, entry: MemoryEntry) -> None:
        self.store.setdefault((entry.agent_id, entry.session_id), []).append(entry)

    async def get_recent(self, agent_id, session_id, n=10):
        return self.store.get((agent_id, session_id), [])[-n:]

    async def search(self, agent_id, session_id, query, top_k):
        return self.store.get((agent_id, session_id), [])[:top_k]

    async def ping(self) -> bool:
        return self.healthy

    async def stats(self, agent_id, session_id) -> dict:
        return {"tier": "working", "size": len(self.store.get((agent_id, session_id), []))}


class FakeEpisodic:
    def __init__(self) -> None:
        self.store: dict[str, list[MemoryEntry]] = {}
        self.healthy = True

    async def write(self, entry: MemoryEntry) -> None:
        self.store.setdefault(entry.agent_id, []).append(entry)

    async def search(self, agent_id, query, top_k=10, min_importance=0.0):
        out = [(e, e.importance) for e in self.store.get(agent_id, [])
               if e.importance >= min_importance]
        return out[:top_k]

    def stats(self, agent_id) -> dict:
        return {"tier": "episodic", "vector_count": len(self.store.get(agent_id, []))}

    async def ping(self) -> bool:
        return self.healthy


class FakeSemantic:
    def __init__(self) -> None:
        self.store: dict[str, list[MemoryEntry]] = {}
        self.versions: dict[str, list[dict]] = {}
        self.healthy = True

    async def write(self, entry: MemoryEntry) -> str:
        self.store.setdefault(entry.agent_id, []).append(entry)
        vref = f"sem-v{uuid.uuid4().hex[:8]}"
        self.versions.setdefault(entry.agent_id, []).append({"version_ref": vref})
        return vref

    async def search(self, agent_id, query, top_k=10, min_importance=0.0):
        out = [(e, e.importance) for e in self.store.get(agent_id, [])
               if e.importance >= min_importance]
        return out[:top_k]

    async def list_versions(self, agent_id, limit=20):
        return self.versions.get(agent_id, [])[:limit]

    async def rollback(self, agent_id, version_ref) -> int:
        return 1 if any(v["version_ref"] == version_ref
                        for v in self.versions.get(agent_id, [])) else 0

    async def stats(self, agent_id) -> dict:
        return {"tier": "semantic", "nodes": len(self.store.get(agent_id, []))}

    async def ping(self) -> bool:
        return self.healthy


class FakeProcedural:
    def __init__(self) -> None:
        self.store: dict[str, list[MemoryEntry]] = {}
        self.versions: dict[str, list[dict]] = {}
        self.healthy = True

    async def write(self, entry: MemoryEntry) -> str:
        self.store.setdefault(entry.agent_id, []).append(entry)
        vref = f"proc-v{uuid.uuid4().hex[:8]}"
        self.versions.setdefault(entry.agent_id, []).append(
            {"version_ref": vref, "tier": entry.tier.name}
        )
        return vref

    async def search(self, agent_id, query, top_k=10, min_importance=0.0):
        out = [(e, e.importance) for e in self.store.get(agent_id, [])
               if e.importance >= min_importance]
        return out[:top_k]

    async def list_versions(self, agent_id, tier=None, limit=20):
        vs = self.versions.get(agent_id, [])
        if tier is not None:
            vs = [v for v in vs if v.get("tier") == tier.name]
        return vs[:limit]

    async def rollback(self, agent_id, version_ref, tier) -> int:
        return 1 if any(v["version_ref"] == version_ref
                        for v in self.versions.get(agent_id, [])) else 0

    async def stats(self, agent_id) -> dict:
        return {"tier": "procedural", "rows": len(self.store.get(agent_id, []))}

    async def ping(self) -> bool:
        return self.healthy


class FakeWAL:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, entry_dict: dict) -> None:
        self.published.append(entry_dict)

    async def close(self) -> None:
        return None


class FakeConsolidation:
    async def run(self, agent_id, dry_run=False):
        from agentmemos.core.models import ConsolidationResult
        return ConsolidationResult(
            agent_id=agent_id,
            clusters_found=2,
            nodes_created=0 if dry_run else 2,
            episodes_archived=0 if dry_run else 5,
            storage_freed_bytes=0 if dry_run else 4096,
            duration_seconds=0.01,
        )


# ── Composite servicer fixture ──────────────────────────────────────────────

@pytest.fixture
def fake_servicer():
    from agentmemos.server.grpc_server import MemoryServicer

    return MemoryServicer(
        working=FakeWorking(),
        episodic=FakeEpisodic(),
        semantic=FakeSemantic(),
        procedural=FakeProcedural(),
        embed=FakeEmbeddingService(),
        scorer=ImportanceScorer(),
        router=MemoryRouter(),
        policy_engine=FederationPolicyEngine(PolicyStore()),
        consolidation=FakeConsolidation(),
        wal=FakeWAL(),
    )


@pytest.fixture
def api_client(fake_servicer, monkeypatch):
    """TestClient wired to the fake servicer; real startup is bypassed."""
    from fastapi.testclient import TestClient

    from agentmemos.server import rest_api

    async def _fake_build():
        return fake_servicer

    monkeypatch.setattr(rest_api, "build_servicer", _fake_build)
    with TestClient(rest_api.app) as client:
        yield client
