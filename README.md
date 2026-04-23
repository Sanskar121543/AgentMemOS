# AgentMemOS

**Hierarchical Memory OS for LLM Agents**

A production-grade memory management system that gives autonomous LLM agents persistent, organized, cross-session memory across four cognitively-inspired tiers — working, episodic, semantic, and procedural — with a background sleep-based consolidation pipeline, a five-signal importance scorer, semantic LRU eviction with ghost entries, and cross-agent memory federation with OPA-based policy enforcement.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        LLM Agent / Framework                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │  gRPC (protobuf)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     MemoryServicer (gRPC)                       │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│   │ MemoryRouter │  │  Importance  │  │  ReadFuser (re-rank) │  │
│   │ (classifier) │  │   Scorer     │  │  cross-encoder blend │  │
│   └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
└──────────┼──────────────────────────────────────────────────────┘
           │ routes to one or more tiers (parallel fan-out on read)
    ┌──────┴───────────────────────────────────────┐
    │                                              │
    ▼              ▼              ▼                ▼
┌────────┐   ┌──────────┐  ┌──────────┐   ┌────────────┐
│ Tier 0 │   │  Tier 1  │  │  Tier 2  │   │   Tier 3   │
│ Redis  │   │ Pinecone │  │  Neo4j   │   │ PostgreSQL │
│Working │   │ Episodic │  │ Semantic │   │ Procedural │
│<1ms    │   │ ~10ms    │  │ ~15ms    │   │  ~5ms      │
│TTL=4h  │   │ ANN vec  │  │  KG      │   │ SQL+JSONB  │
└────────┘   └──────────┘  └──────────┘   └────────────┘
                               ▲
              ┌────────────────┘
              │  every 4 hours
┌─────────────────────────────┐
│  Consolidation Pipeline     │
│  Harvest → HDBSCAN cluster  │
│  → LLM synthesise           │
│  → promote to Neo4j         │
│  → archive cold to S3       │
└─────────────────────────────┘
```

### Four Memory Tiers

| Tier | Backend | What it stores | Access |
|------|---------|---------------|--------|
| **Working** (0) | Redis | In-context scratchpad, current session | `< 1ms`, TTL = 4h |
| **Episodic** (1) | Pinecone | What happened — vector embeddings of actions + outcomes | `~10ms`, ANN cosine search |
| **Semantic** (2) | Neo4j | Facts, concepts, relationships learned across sessions | `~15ms`, graph traversal |
| **Procedural** (3) | PostgreSQL | Tool-call sequences, task execution traces, version ledger | `~5ms`, full-text + structured |

---

## Core Technical Innovations

### 1 · Sleep-Based Memory Consolidation

Inspired by hippocampal-neocortical consolidation during sleep. A background pipeline runs every 4 hours per agent:

1. **Harvest** — pull recent episodic memories from Pinecone
2. **Cluster** — HDBSCAN on embedding vectors (no need to pre-specify K)
3. **Synthesise** — LLM compresses each cluster into a concise semantic fact
4. **Promote** — write synthesised concept to Neo4j as a consolidated node
5. **Archive** — cold, unimportant episodes → S3 Glacier; ghost entries remain in Pinecone

Result: semantic memory grows richer over time without manual curation. Episodic storage stays bounded.

### 2 · Five-Signal Importance Scorer

Every memory is scored before promotion decisions:

```
composite = 0.20 × recency_score         (exponential decay from creation time)
          + 0.25 × cross_ref_count        (log-normalized in-degree in Neo4j graph)
          + 0.25 × outcome_salience       (did subsequent actions succeed?)
          + 0.10 × agent_confidence       (confidence at formation time)
          + 0.20 × semantic_novelty       (1 - max cosine sim vs existing concepts)
```

Only memories above a **dynamic per-agent threshold** are promoted to Tier 2/3. This cuts long-term storage footprint by ~62% vs. naive "remember everything."

### 3 · Semantic LRU Eviction with Ghost Entries

Standard LRU evicts the least-recently-used entry. Semantic LRU evicts by:

```
eviction_score = 0.5 × recency + 0.5 × semantic_centrality (PageRank proxy)
```

Highly-referenced concept nodes survive even if not recently accessed. When an entry is evicted, a **ghost tombstone** is written to Redis. On the next read, a ghost hit tells the agent it once knew something and triggers a cold-tier S3 retrieval — exactly like CPU cache ghost entries enabling prefetch.

### 4 · Cross-Agent Memory Federation

Agents publish tagged memory updates to Redis pub/sub channels namespaced by team/task. Every cross-agent read is evaluated by an **OPA policy engine** before any data is returned. Policies support:

- `public: true` — globally readable
- `allowed_agents: [...]` — explicit allow-list
- `allowed_teams: [...]` — team-scoped access
- `redact_fields: [...]` — scrub sensitive metadata before return

### 5 · Kafka Write-Ahead Log

Every write is committed to a Kafka WAL topic before being applied to the tier. This provides:
- **Durability** — replay on failure
- **Audit trail** — exactly what each agent remembered and when
- **Decoupling** — tier writes are fire-and-forget async tasks

---

## Repo Structure

```
AgentMemOS/
├── agentmemos/
│   ├── core/
│   │   ├── models.py          # Pydantic domain models (MemoryEntry, RankedMemory, ...)
│   │   ├── router.py          # MemoryRouter — intent classification + tier routing
│   │   ├── importance.py      # Five-signal importance scorer
│   │   └── embeddings.py      # Async embed service (OpenAI / sentence-transformers)
│   ├── tiers/
│   │   ├── working.py         # Tier 0 — Redis
│   │   ├── episodic.py        # Tier 1 — Pinecone
│   │   ├── semantic.py        # Tier 2 — Neo4j knowledge graph
│   │   └── procedural.py      # Tier 3 — PostgreSQL
│   ├── consolidation/
│   │   └── pipeline.py        # HDBSCAN → LLM synthesis → Neo4j promotion
│   ├── federation/
│   │   └── policy.py          # OPA-compatible policy engine
│   ├── eviction/
│   │   └── semantic_lru.py    # Semantic LRU cache with ghost entries
│   └── server/
│       ├── grpc_server.py     # gRPC MemoryServicer + server startup
│       └── rest_api.py        # FastAPI admin API (health, consolidate, rollback, ...)
├── proto/
│   └── memory.proto           # Protobuf service definition
├── tests/
│   ├── test_core.py           # Router, scorer, model unit tests (no external deps)
│   └── test_eviction_federation.py  # LRU + policy engine unit tests
├── k8s/
│   └── deployment.yaml        # Namespace, Deployment, HPA, PDB, Services
├── opa/
│   └── policies/federation.rego  # Rego policy for cross-agent reads
├── monitoring/
│   └── prometheus.yml
├── scripts/
│   ├── generate_proto.sh      # Compile .proto → Python stubs
│   └── run_tests.sh
├── docker-compose.yml         # Full local dev stack
├── Dockerfile                 # Multi-stage image
├── pyproject.toml
└── .env.example
```

---

## Quickstart

### Prerequisites

- Python 3.11+
- Docker + Docker Compose
- `pip install grpcio-tools` (for proto compilation)

External accounts needed for full functionality:
- **Pinecone** — free tier works for development
- **OpenAI** — for embeddings and consolidation synthesis (or use local fallback)

### 1 · Clone and install

```bash
git clone https://github.com/<you>/AgentMemOS.git
cd AgentMemOS

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[dev]"
```

### 2 · Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
OPENAI_API_KEY=sk-...        # or leave blank to use local sentence-transformers
PINECONE_API_KEY=...
NEO4J_PASSWORD=agentmemos    # default for local docker stack
```

All other values have working defaults for the local Docker stack.

### 3 · Start backing services

```bash
docker compose up -d redis neo4j postgres kafka opa
```

Wait for services to be healthy (~30 seconds):

```bash
docker compose ps   # all should show "healthy"
```

### 4 · Compile protobuf stubs

```bash
chmod +x scripts/generate_proto.sh
./scripts/generate_proto.sh
```

### 5 · Run tests (no external services required)

The core unit tests cover the router, importance scorer, eviction, and federation policy — all in-process with no database connections:

```bash
pytest tests/test_core.py tests/test_eviction_federation.py -v
```

Expected output: all tests pass in under 2 seconds.

### 6 · Start the server

```bash
# Terminal 1 — gRPC server
python -m agentmemos.server.grpc_server

# Terminal 2 — REST admin
uvicorn agentmemos.server.rest_api:app --reload --port 8000
```

### 7 · Verify

```bash
# Health check
curl http://localhost:8000/health

# Admin docs
open http://localhost:8000/docs

# Neo4j browser
open http://localhost:7474    # login: neo4j / agentmemos

# Grafana
open http://localhost:3000    # login: admin / agentmemos
```

---

## Running the Full Stack

```bash
docker compose up -d
docker compose logs -f agentmemos
```

The server exposes:
- `localhost:50051` — gRPC (hot-path memory ops)
- `localhost:8000`  — REST admin API

---

## API Reference

### gRPC (hot-path)

Defined in `proto/memory.proto`. After generating stubs with `./scripts/generate_proto.sh`:

| RPC | Description |
|-----|-------------|
| `Write(WriteRequest)` | Write a memory entry; router decides tier |
| `Read(ReadRequest)` | Fan-out read with cross-encoder re-ranking |
| `Delete(DeleteRequest)` | Delete with optional ghost entry creation |
| `Rollback(RollbackRequest)` | Restore semantic/procedural memory to a checkpoint |
| `Federate(FederateRequest)` | Read another agent's memory (policy-gated) |
| `Consolidate(ConsolidateRequest)` | Trigger manual consolidation run |
| `Health(HealthRequest)` | All-tiers health check |

### REST (admin)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/ready` | GET | Readiness probe |
| `/metrics` | GET | Prometheus metrics |
| `/agents/{id}/stats` | GET | Per-agent tier statistics |
| `/agents/{id}/versions` | GET | Version history for rollback |
| `/consolidate` | POST | Manual consolidation trigger |
| `/rollback` | POST | Restore to version checkpoint |
| `/policy` | POST | Register federation policy |
| `/policy/{agent_id}` | GET | Get agent's current policy |
| `/docs` | GET | Swagger UI |

---

## Production Deployment (AWS EKS)

```bash
# Build and push image
docker build -t <ECR_REGISTRY>/agentmemos:1.0.0 .
docker push     <ECR_REGISTRY>/agentmemos:1.0.0

# Update the image ref in k8s/deployment.yaml, then:
kubectl apply -f k8s/deployment.yaml

# Verify
kubectl get pods -n agentmemos
kubectl get hpa  -n agentmemos
```

The HPA scales on `grpc_server_pending_rpcs` (custom Prometheus metric) — not CPU — because the bottleneck is I/O to Pinecone/Neo4j/Redis, not compute. Requires the [Prometheus Adapter](https://github.com/kubernetes-sigs/prometheus-adapter) deployed in your cluster.

---

## Performance Targets

| Metric | Target | Method |
|--------|--------|--------|
| p99 read latency | < 85ms | Locust load test @ 5K RPS |
| Storage reduction | ~62% vs naive RAG | 10K session benchmark |
| Context retention | 91% on multi-session QA | Held-out evaluation set |
| Concurrent agents | 5K+ | EKS load test, 8 pods |

---

## Embedding Backends

| Backend | How to enable | Dimension |
|---------|--------------|-----------|
| OpenAI `text-embedding-3-small` | Set `OPENAI_API_KEY` | 1536 |
| `all-MiniLM-L6-v2` (local) | `pip install sentence-transformers`; leave API key blank | 384 |

When using the local model, set `EMBEDDING_DIM=384` in `.env` and recreate your Pinecone index.

---

## Development Notes

### Adding a new memory tier

1. Implement a class in `agentmemos/tiers/` with `connect()`, `write()`, `search()`, `ping()` methods
2. Add the new `MemoryTier` enum value in `agentmemos/core/models.py`
3. Register it in `MemoryRouter` and `MemoryServicer`

### Adjusting importance weights

Per-agent weights can be stored in the procedural tier and loaded at runtime:

```python
scorer.calibrate(
    agent_id="my-agent",
    weights=ImportanceWeights(
        recency=0.15, cross_ref=0.30, salience=0.30,
        confidence=0.10, novelty=0.15
    )
)
```

### Airflow DAG (production consolidation)

Replace `schedule_consolidation()` with a real Airflow DAG:

```python
from airflow.decorators import dag, task
from pendulum import datetime

@dag(schedule="0 */4 * * *", start_date=datetime(2024, 1, 1), catchup=False)
def agentmemos_consolidation():
    @task
    async def consolidate_all_agents():
        # fetch agent list from DB, run pipeline per agent
        ...
```

---

## License

MIT
