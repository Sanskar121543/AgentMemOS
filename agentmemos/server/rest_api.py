"""
agentmemos.server.rest_api
───────────────────────────
FastAPI admin and dashboard REST API.

Hot-path memory operations (Read/Write) are NOT exposed here —
they run over gRPC. This API covers:
  - /health         — k8s liveness + readiness probes
  - /metrics        — Prometheus-compatible metrics
  - /agents/{id}    — per-agent stats
  - /consolidate    — trigger manual consolidation
  - /rollback       — restore a previous memory checkpoint
  - /policy         — register / update federation policies
  - /versions       — list memory version history
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from agentmemos.core.models import FederationPolicy, MemoryTier
from agentmemos.server.grpc_server import MemoryServicer, build_servicer


# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgentMemOS Admin API",
    version="1.0.0",
    description=(
        "Administrative REST interface for AgentMemOS. "
        "Hot-path memory ops (Read/Write) run over gRPC on port 50051."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_servicer: MemoryServicer | None = None
_start_time = time.time()

# Simple in-process counters (replace with Prometheus counters in prod)
_counters: dict[str, int] = {
    "grpc_writes": 0,
    "grpc_reads": 0,
    "consolidations": 0,
    "rollbacks": 0,
}


@app.on_event("startup")
async def startup() -> None:
    global _servicer
    _servicer = await build_servicer()


def get_servicer() -> MemoryServicer:
    if _servicer is None:
        raise HTTPException(503, "Service not ready.")
    return _servicer


# ─────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ─────────────────────────────────────────────────────────────────────────────

class ConsolidateRequest(BaseModel):
    agent_id: str
    dry_run:  bool = False


class RollbackRequest(BaseModel):
    agent_id:    str
    version_ref: str
    tier:        str = Field(description="working | episodic | semantic | procedural")


class PolicyRegisterRequest(BaseModel):
    owner_agent_id: str
    allowed_agents: list[str] = []
    allowed_teams:  list[str] = []
    public:         bool = False
    redact_fields:  list[str] = []


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
async def health() -> dict:
    svc = get_servicer()
    result = await svc.health()
    if not result["healthy"]:
        raise HTTPException(503, detail=result)
    return result


@app.get("/ready", tags=["ops"])
async def ready() -> dict:
    """Kubernetes readiness probe."""
    svc = get_servicer()
    h = await svc.health()
    if not h["healthy"]:
        raise HTTPException(503, "Not ready")
    return {"ready": True}


@app.get("/metrics", response_class=PlainTextResponse, tags=["ops"])
async def metrics() -> str:
    """
    Prometheus-compatible text exposition.
    In production: integrate prometheus_client and expose real counters.
    """
    uptime = time.time() - _start_time
    lines = [
        "# HELP agentmemos_uptime_seconds Time since server start",
        "# TYPE agentmemos_uptime_seconds counter",
        f"agentmemos_uptime_seconds {uptime:.2f}",
        "",
        "# HELP agentmemos_grpc_writes_total Total gRPC write requests",
        "# TYPE agentmemos_grpc_writes_total counter",
        f"agentmemos_grpc_writes_total {_counters['grpc_writes']}",
        "",
        "# HELP agentmemos_grpc_reads_total Total gRPC read requests",
        "# TYPE agentmemos_grpc_reads_total counter",
        f"agentmemos_grpc_reads_total {_counters['grpc_reads']}",
        "",
        "# HELP agentmemos_consolidations_total Total consolidation runs",
        "# TYPE agentmemos_consolidations_total counter",
        f"agentmemos_consolidations_total {_counters['consolidations']}",
    ]
    return "\n".join(lines)


@app.get("/agents/{agent_id}/stats", tags=["agents"])
async def agent_stats(
    agent_id: str = Path(description="Agent identifier"),
) -> dict:
    svc = get_servicer()
    working_stats    = await svc._working.stats(agent_id, "default")
    semantic_stats   = await svc._semantic.stats(agent_id)
    procedural_stats = await svc._procedural.stats(agent_id)
    episodic_stats   = svc._episodic.stats(agent_id)
    return {
        "agent_id": agent_id,
        "tiers": {
            "working":    working_stats,
            "episodic":   episodic_stats,
            "semantic":   semantic_stats,
            "procedural": procedural_stats,
        },
    }


@app.get("/agents/{agent_id}/versions", tags=["versioning"])
async def list_versions(
    agent_id: str = Path(description="Agent identifier"),
    tier: str | None = Query(default=None, description="Filter by tier"),
    limit: int = Query(default=20, le=100),
) -> dict:
    svc = get_servicer()
    tier_enum = None
    if tier:
        try:
            tier_enum = MemoryTier[tier.upper()]
        except KeyError:
            raise HTTPException(400, f"Unknown tier: {tier}")

    versions = await svc._procedural.list_versions(agent_id, tier_enum, limit)
    semantic_versions = await svc._semantic.list_versions(agent_id, limit)

    return {
        "agent_id": agent_id,
        "procedural_versions": versions,
        "semantic_versions":   semantic_versions,
    }


@app.post("/consolidate", tags=["consolidation"])
async def consolidate(req: ConsolidateRequest) -> dict:
    svc = get_servicer()
    result = await svc.consolidate(req.agent_id, dry_run=req.dry_run)
    _counters["consolidations"] += 1
    return result


@app.post("/rollback", tags=["versioning"])
async def rollback(req: RollbackRequest) -> dict:
    svc = get_servicer()
    try:
        tier = MemoryTier[req.tier.upper()]
    except KeyError:
        raise HTTPException(400, f"Unknown tier: {req.tier}")
    result = await svc.rollback(req.agent_id, req.version_ref, tier)
    _counters["rollbacks"] += 1
    return result


@app.post("/policy", tags=["federation"])
async def register_policy(req: PolicyRegisterRequest) -> dict:
    svc = get_servicer()
    policy = FederationPolicy(
        owner_agent_id=req.owner_agent_id,
        allowed_agents=req.allowed_agents,
        allowed_teams=req.allowed_teams,
        public=req.public,
        redact_fields=req.redact_fields,
    )
    svc._policy._store.register(policy)
    return {
        "policy_id": policy.policy_id,
        "owner":     policy.owner_agent_id,
        "public":    policy.public,
    }


@app.get("/policy/{agent_id}", tags=["federation"])
async def get_policy(agent_id: str) -> dict:
    svc = get_servicer()
    policy = svc._policy._store.get(agent_id)
    if not policy:
        raise HTTPException(404, f"No policy for agent {agent_id}")
    return policy.model_dump(mode="json")


@app.get("/", tags=["meta"])
async def root() -> dict:
    return {
        "service":     "AgentMemOS",
        "version":     "1.0.0",
        "grpc_port":   int(os.getenv("GRPC_PORT", "50051")),
        "admin_docs":  "/docs",
    }
