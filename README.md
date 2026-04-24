# AgentMemOS

**A prototype hierarchical memory operating system for persistent LLM agents.**

AgentMemOS is a distributed memory management system for autonomous AI agents. It separates the control plane from the memory data plane and organizes memory into four cognitively inspired tiers: **working**, **episodic**, **semantic**, and **procedural** memory.

Hot-path memory operations run over **gRPC (port 50051)**, while administrative and observability endpoints run over **FastAPI (port 8000)**.

---

## Why AgentMemOS?

Most LLM systems are stateless by default. Once a session ends, valuable context is often lost.

AgentMemOS explores how long-running agents can:

* retain active context
* retrieve prior interactions
* build structured knowledge
* preserve reusable workflows
* support policy-controlled memory sharing

---

## Architecture

```text
LLM Agent
   │
   ▼
gRPC Memory Data Plane (50051)
   │
   ├── Working Memory     → Redis
   ├── Episodic Memory    → Pinecone
   ├── Semantic Memory    → Neo4j
   └── Procedural Memory  → PostgreSQL

Control Plane
   │
   ▼
FastAPI Admin API (8000)

Supporting Services:
- Kafka Write Log
- Open Policy Agent
- Prometheus
- Grafana
```

---

## Memory Tiers

| Tier       | Backend    | Purpose                         |
| ---------- | ---------- | ------------------------------- |
| Working    | Redis      | Fast short-term session context |
| Episodic   | Pinecone   | Prior interaction retrieval     |
| Semantic   | Neo4j      | Concepts and relationships      |
| Procedural | PostgreSQL | Workflows and reusable traces   |

---

## Core Features

### Multi-Tier Memory Routing

Memories are routed to the best storage tier depending on usage pattern, latency needs, and persistence value.

Examples:

* Temporary context → Redis
* Interaction summaries → Pinecone
* Learned facts → Neo4j
* Reusable workflows → PostgreSQL

---

### Background Consolidation Pipeline

Short-term experiences can be promoted into long-term memory.

Typical flow:

1. Retrieve recent episodic memories
2. Cluster related entries
3. Summarize recurring patterns
4. Promote useful concepts to semantic memory
5. Archive stale entries

---

### Policy-Based Federation

Supports controlled memory sharing across multiple agents.

Examples:

* Public memory pools
* Team-scoped access
* Allowlists
* Redacted fields

---

### Observability

Integrated monitoring includes:

* Prometheus metrics
* Grafana dashboards
* Health endpoints
* Tier status inspection

---

## Interfaces

### REST Admin API

* `GET /health`
* `GET /ready`
* `GET /metrics`
* `GET /agents/{agent_id}/stats`
* `GET /agents/{agent_id}/versions`
* `POST /consolidate`
* `POST /rollback`
* `POST /policy`

### gRPC Memory API

* Write memory
* Read memory
* Delete memory
* Consolidate memory
* Federated access checks

---

## Tech Stack

### Backend

* Python 3.11
* FastAPI
* gRPC
* AsyncIO

### Datastores

* Redis
* Pinecone
* Neo4j
* PostgreSQL

### Infrastructure

* Docker Compose
* Kafka
* Open Policy Agent
* Prometheus
* Grafana

---

## Benchmark Snapshot

Local Docker smoke benchmark of lightweight infrastructure endpoints:

| Metric          |        Result |
| --------------- | ------------: |
| Throughput      | 5,537 req/sec |
| Average Latency |       1.66 ms |
| P95 Latency     |       2.23 ms |
| P99 Latency     |      13.55 ms |

*These reflect infra responsiveness, not full memory-path write/search workloads.*

---

## Screenshots

## Swagger API Docs

<p align="center">
  <img src="assets/screenshots/Swagger.png" width="950">
</p>

Interactive FastAPI-generated REST documentation.

---

## Docker Services Running

<p align="center">
  <img src="assets/screenshots/docker-status.png" width="950">
</p>

Healthy multi-container environment running Redis, Kafka, Neo4j, PostgreSQL, Prometheus, Grafana, and API services.

---

## Grafana Dashboard

<p align="center">
  <img src="assets/screenshots/Grafana.png" width="950">
</p>

Metrics visualization for system health and operational monitoring.

---

## Neo4j Graph View

<p align="center">
  <img src="assets/screenshots/Neo4j.jpeg" width="950">
</p>

Semantic memory graph storing concepts and relationships.

---

## Benchmark Results

<p align="center">
  <img src="assets/screenshots/BenchmarkResults.jpeg" width="950">
</p>

Concurrent benchmark results for the infrastructure benchmark suite.

---

## Quick Start

### Clone Repository

```bash
git clone https://github.com/Sanskar121543/AgentMemOS.git
cd AgentMemOS
```

### Configure `.env`

```env
OPENAI_API_KEY=your_key
PINECONE_API_KEY=your_key
JWT_SECRET=your_secret
```

### Start Services

```bash
docker compose up -d
```

### Open Docs

```text
http://localhost:8000/docs
```

### Open Grafana

```text
http://localhost:3001
```

---

## Project Structure

```text
AgentMemOS/
├── agentmemos/
│   ├── core/
│   ├── tiers/
│   ├── consolidation/
│   ├── federation/
│   ├── eviction/
│   └── server/
├── assets/screenshots/
├── monitoring/
├── proto/
├── scripts/
├── tests/
├── results/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## Example Use Cases

### Personal AI Assistant

Retains preferences and recurring context across sessions.

### Multi-Agent Research System

Shares memory selectively using policy controls.

### Coding Agent

Stores bug fixes, workflows, and reusable traces.

### Enterprise Knowledge Assistant

Builds searchable institutional memory over time.

---

## Engineering Highlights

* Built a multi-tier memory architecture across cache, vector, graph, and relational stores
* Implemented asynchronous APIs using FastAPI + gRPC
* Designed long-term memory consolidation workflows
* Added monitoring with Prometheus + Grafana
* Dockerized full multi-service local deployment
* Explored persistent memory systems for long-running LLM agents

---

## Future Improvements

* Real write/search benchmark suite
* Kubernetes deployment
* Retrieval ranking improvements
* Cost-aware storage routing
* Local embedding providers
* Fine-grained retention policies

---

## License

MIT
