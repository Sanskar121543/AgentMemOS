<div align="center">
<br/>

```
 █████╗  ██████╗ ███████╗███╗   ██╗████████╗    ███╗   ███╗███████╗███╗   ███╗ ██████╗ ███████╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝    ████╗ ████║██╔════╝████╗ ████║██╔═══██╗██╔════╝
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║       ██╔████╔██║█████╗  ██╔████╔██║██║   ██║███████╗
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║       ██║╚██╔╝██║██╔══╝  ██║╚██╔╝██║██║   ██║╚════██║
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║       ██║ ╚═╝ ██║███████╗██║ ╚═╝ ██║╚██████╔╝███████║
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝       ╚═╝     ╚═╝╚══════╝╚═╝     ╚═╝ ╚═════╝ ╚══════╝
```

### Hierarchical Memory Operating System for Persistent LLM Agents
*Working memory · Episodic recall · Semantic graphs · Procedural traces · Policy-gated federation*

<br/>

[![CI](https://github.com/Sanskar121543/AgentMemOS/actions/workflows/ci.yml/badge.svg)](https://github.com/Sanskar121543/AgentMemOS/actions)
[![Tests](https://img.shields.io/badge/tests-100_passing-2ea44f?style=flat-square&logo=pytest&logoColor=white)](tests/)
[![Coverage](https://img.shields.io/badge/coverage-58%25-yellow?style=flat-square)](tests/)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-Admin_API-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![gRPC](https://img.shields.io/badge/gRPC-Data_Plane-244c5a?style=flat-square&logo=grpc&logoColor=white)](https://grpc.io)
[![Redis](https://img.shields.io/badge/Redis-Working_Memory-DC382D?style=flat-square&logo=redis&logoColor=white)](https://redis.io)
[![Neo4j](https://img.shields.io/badge/Neo4j-Semantic_Graph-008CC1?style=flat-square&logo=neo4j&logoColor=white)](https://neo4j.com)
[![Kafka](https://img.shields.io/badge/Kafka-Write_Log-231F20?style=flat-square&logo=apache-kafka&logoColor=white)](https://kafka.apache.org)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docker.com)
[![License](https://img.shields.io/badge/License-MIT-6366F1?style=flat-square)](LICENSE)

<br/>

> **LLM agents are stateless by default.**
> AgentMemOS gives them a brain that persists.

<br/>
</div>

---

## Demo

<p align="center">
  <img src="assets/demo 1.gif" width="950">
</p>

## Benchmark Results

> Local Docker smoke benchmark · infrastructure endpoints · concurrent load

| Metric | Result |
|--------|--------|
| Throughput | **5,537 req/sec** |
| Average Latency | **1.66 ms** |
| P95 Latency | **2.23 ms** |
| P99 Latency | **13.55 ms** |

*Reflects control-plane responsiveness. Full memory-path write/search benchmarks are on the roadmap.*

---

## The Problem

Every time an LLM session ends, the agent forgets everything. Preferences, prior interactions, learned facts, solved workflows — gone.

Building a persistent agent means solving four distinct memory problems simultaneously, each with different latency requirements, data structures, and retention semantics:

| Memory Type | The Problem | The Wrong Solution |
|-------------|------------|-------------------|
| **Working** | Need sub-millisecond access to active context | Storing it in a DB |
| **Episodic** | Need semantic similarity search over past interactions | Exact key-value lookup |
| **Semantic** | Need to traverse concept relationships | Flat document storage |
| **Procedural** | Need to replay and reuse structured workflows | Storing as raw text |

AgentMemOS solves each with the right tool — and routes between them automatically.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                         LLM AGENT                                │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│              gRPC MEMORY DATA PLANE  :50051                      │
│         Hot-path operations · <2ms p95 · async-native            │
│                                                                  │
│   write() · read() · delete() · consolidate() · federate()       │
└──────┬────────────┬─────────────┬──────────────┬────────────────┘
       │            │             │              │
       ▼            ▼             ▼              ▼
  ┌─────────┐  ┌──────────┐  ┌────────┐  ┌───────────┐
  │  Redis  │  │ Pinecone │  │ Neo4j  │  │ Postgres  │
  │         │  │          │  │        │  │           │
  │ Working │  │ Episodic │  │Semantic│  │Procedural │
  │ Memory  │  │ Memory   │  │ Memory │  │  Memory   │
  │         │  │          │  │        │  │           │
  │ Session │  │ Vector   │  │ Graph  │  │ Workflow  │
  │ context │  │ retrieval│  │  KG    │  │  traces   │
  └─────────┘  └──────────┘  └────────┘  └───────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│            KAFKA WRITE LOG  (Durable audit trail)                │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│               CONTROL PLANE  ·  FastAPI :8000                    │
│                                                                  │
│  /health  /metrics  /consolidate  /rollback  /policy             │
│  Prometheus ──► Grafana    ·    Open Policy Agent                │
└──────────────────────────────────────────────────────────────────┘
```

**Two planes. One principle:** hot-path memory operations never touch the control plane.

---

## How a Write Travels Through the System

Every write is embedded, scored, and routed automatically — the caller never picks a tier.

```
write(content)
     │
     ▼
 1. Embed          → OpenAI / local model, int8-quantized (4× smaller)
 2. Score          → 5-signal ImportanceScorer (recency, cross-refs,
     │                outcome salience, confidence, semantic novelty)
 3. Route          → MemoryRouter blends heuristics + importance
     │                to pick Working / Episodic / Semantic / Procedural
 4. WAL            → Kafka write-ahead log (durable, replayable)
 5. Persist        → target tier, with promotion if score clears threshold
```

Reads fan out across the routed tiers **in parallel**, fuse results by
relevance + recency, deduplicate by id, and return the top-k.

---

## Memory Tiers

### Working Memory — Redis
Active session context. Millisecond read/write. Automatically evicted when a session closes. The agent's RAM.

### Episodic Memory — Pinecone
Semantic vector search over prior interactions. "What did we discuss last Tuesday about the budget?" Retrieves by meaning, not by key.

### Semantic Memory — Neo4j
A living knowledge graph of concepts and their relationships. Grows over time as the agent learns. Supports traversal queries that flat stores can't answer.

### Procedural Memory — PostgreSQL
Structured workflow traces and reusable task templates. When an agent solves a problem once, the solution can be stored and replayed.

---

## Core Features

### Background Consolidation Pipeline
Experiences don't stay in episodic memory forever — they get promoted into structured knowledge.

```
Recent episodic memories
        │
        ▼
   Cluster related entries  (HDBSCAN)
        │
        ▼
   Summarize recurring patterns
        │
        ▼
   Promote → Semantic Memory (Neo4j)
        │
        ▼
   Archive stale entries
```

Trigger manually via `POST /consolidate` or schedule it.

### Semantic-LRU Eviction with Ghost Entries
Eviction isn't plain LRU — entries are scored on recency **and** semantic
centrality (graph in-degree). Low-scoring entries are evicted and leave a
**ghost tombstone** behind, so the next time the agent looks for that memory
it knows the data exists in cold storage and can trigger a prefetch — a
cache-miss signal borrowed from CPU victim caches.

### Policy-Based Memory Federation
Multiple agents sharing memory without sharing everything. Open Policy Agent (Rego) enforces boundaries, with a local evaluator fallback that mirrors the same rules.

| Policy Mode | Use Case |
|------------|----------|
| **Public pool** | Shared knowledge accessible to all agents |
| **Team-scoped** | Memory visible only within a defined agent group |
| **Allowlist** | Explicit per-agent access grants |
| **Redacted fields** | Sensitive fields stripped before federation |

### Dual-Interface Design

| Interface | Port | Purpose |
|-----------|------|---------|
| **gRPC** | 50051 | Memory data plane — low-latency read/write |
| **FastAPI** | 8000 | Control plane — admin, health, policy, metrics |

Operations that need speed use gRPC. Operations that need visibility use REST.

---

## Testing & Quality

The suite runs **fully offline** — every storage tier (Redis, Pinecone, Neo4j,
PostgreSQL, Kafka) is replaced by faithful in-memory fakes, so the entire gRPC
servicer and REST API can be exercised end-to-end in CI with **zero backend
infrastructure**.

```
100 passing · property-based + concurrency + contract tests · CI-gated (ruff + pytest-cov)
```

| Suite | What it verifies |
|-------|------------------|
| `test_core.py` | Router, importance scorer, domain models |
| `test_eviction_federation.py` | Semantic-LRU eviction, ghost entries, OPA policy logic |
| `test_servicer_flow.py` | Write→route→promote pipeline, parallel read fan-out, dedup, WAL |
| `test_rest_api.py` | Admin endpoints, error paths, Prometheus exposition |
| `test_property_based.py` | Hypothesis invariants — score bounds, monotonic decay, router totality |
| `test_concurrency.py` | LRU capacity under churn, parallel-write id safety |
| `test_proto_contract.py` | Generated gRPC stubs stay in sync with `memory.proto` |
| `test_health.py` / `test_memory_pipeline.py` | Liveness + request-pipeline smoke tests |

```bash
# Run everything with coverage
pytest tests/ --cov=agentmemos --cov-report=term-missing

# Fast path (no coverage)
./scripts/run_tests.sh --fast
```

Continuous integration ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs
ruff lint, the full suite, and a coverage gate on every push and pull request.

---

## Screenshots

### Swagger API Docs
<p align="center">
  <img src="assets/screenshots/Swagger.png" width="950">
</p>

### Docker Services
<p align="center">
  <img src="assets/screenshots/docker-status.png" width="950">
</p>

### Grafana Dashboard
<p align="center">
  <img src="assets/screenshots/Grafana.png" width="950">
</p>

### Neo4j Semantic Graph
<p align="center">
  <img src="assets/screenshots/Neo4j.jpeg" width="950">
</p>

### Benchmark Results
<p align="center">
  <img src="assets/screenshots/BenchmarkResults.jpeg" width="950">
</p>

---

## API Reference

### REST — Control Plane `:8000`

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Liveness check (fails closed if any tier is down) |
| `GET` | `/ready` | Readiness check |
| `GET` | `/metrics` | Prometheus scrape endpoint |
| `GET` | `/agents/{agent_id}/stats` | Per-agent memory statistics |
| `GET` | `/agents/{agent_id}/versions` | Memory version history |
| `POST` | `/consolidate` | Trigger episodic → semantic promotion |
| `POST` | `/rollback` | Revert memory to a prior version |
| `POST` | `/policy` | Register / update federation policy rules |
| `GET` | `/policy/{agent_id}` | Fetch an agent's federation policy |

### gRPC — Data Plane `:50051`

`Write` · `Read` · `Delete` · `Consolidate` · `Health`

See [`proto/memory.proto`](proto/memory.proto) for full schema definitions.

---

## Quick Start

```bash
# 1. Clone
git clone https://github.com/Sanskar121543/AgentMemOS.git
cd AgentMemOS

# 2. Configure
cp .env.example .env
# Set OPENAI_API_KEY, PINECONE_API_KEY, JWT_SECRET

# 3. Start all services
docker compose up -d

# 4. Explore
open http://localhost:8000/docs   # Swagger UI
open http://localhost:3001        # Grafana
```

All services — Redis, Kafka, Neo4j, PostgreSQL, Pinecone proxy, Prometheus, Grafana, OPA — start via a single Compose file.

**Run the tests (no Docker required):**

```bash
pip install -e ".[dev]"
pytest tests/
```

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| **API** | Python 3.11 · FastAPI · gRPC · AsyncIO |
| **Working Memory** | Redis |
| **Episodic Memory** | Pinecone |
| **Semantic Memory** | Neo4j |
| **Procedural Memory** | PostgreSQL |
| **Write Log** | Apache Kafka |
| **Access Control** | Open Policy Agent (Rego) |
| **Observability** | Prometheus · Grafana · OpenTelemetry |
| **Testing** | pytest · pytest-asyncio · Hypothesis · pytest-cov |
| **CI / Quality** | GitHub Actions · ruff · coverage gate |
| **Infra** | Docker Compose · Kubernetes-ready |

---

## Project Structure

```
AgentMemOS/
├── agentmemos/
│   ├── core/           # Memory router, importance scorer, embeddings, models
│   ├── tiers/          # Redis, Pinecone, Neo4j, PostgreSQL adapters
│   ├── consolidation/  # Episodic → semantic promotion pipeline (HDBSCAN)
│   ├── federation/     # OPA / Rego policy enforcement
│   ├── eviction/       # Semantic-LRU + ghost-entry retention logic
│   └── server/         # gRPC server + FastAPI app
├── proto/              # gRPC service definitions + generated stubs
├── monitoring/         # Prometheus config + Grafana dashboards
├── opa/                # Rego federation policies
├── k8s/                # Kubernetes deployment manifest
├── scripts/            # Test runner + benchmark scripts
├── tests/              # 100-test offline suite (unit · property · concurrency · contract)
├── results/            # Benchmark output
├── assets/screenshots/
├── .github/workflows/  # CI pipeline
├── docker-compose.yml
└── pyproject.toml
```

---

## Use Cases

**Personal AI Assistant** — Remembers preferences, recurring topics, and past conversations across sessions without any user re-prompting.

**Multi-Agent Research System** — Agents share discovered knowledge through the semantic graph while keeping session context private via policy controls.

**Coding Agent** — Stores resolved bugs and reusable fix patterns in procedural memory. Retrieves them when similar issues recur.

**Enterprise Knowledge Assistant** — Builds a queryable institutional memory over months of interactions, surfacing relevant history through semantic search.

---

## Roadmap

- [ ] Full memory-path write/search benchmark suite
- [ ] Raise coverage past the tier-adapter integration layer (live-backend tests)
- [ ] Retrieval ranking and re-ranking improvements
- [ ] Cost-aware storage tier routing
- [ ] Local embedding providers (no Pinecone dependency)
- [ ] Fine-grained per-field retention policies

---

<div align="center">

**LLM agents that forget everything aren't agents. They're chatbots.**

*AgentMemOS is the infrastructure layer that makes the difference.*

<br/>

[![Star this repo](https://img.shields.io/github/stars/Sanskar121543/AgentMemOS?style=social)](https://github.com/Sanskar121543/AgentMemOS)

</div>
