# AgentMemOS

**Prototype Hierarchical Memory Operating System for Persistent LLM Agents**

AgentMemOS is a distributed memory management prototype for autonomous AI agents that enables persistent, cross-session memory across four cognitively inspired tiers: **working**, **episodic**, **semantic**, and **procedural** memory.

It explores how long-running LLM systems can retain context, remember prior interactions, build structured knowledge, and preserve reusable workflows over time.

---

# Why AgentMemOS?

Most LLM applications are stateless by default. Once a session ends, valuable context is often lost unless an external memory layer is added.

AgentMemOS investigates a structured memory architecture inspired by human cognition:

* **Working Memory** → short-term active context
* **Episodic Memory** → prior interactions and experiences
* **Semantic Memory** → facts, concepts, relationships
* **Procedural Memory** → workflows, tools, reusable processes

This architecture helps agents maintain continuity, improve retrieval quality, and support richer long-term behavior.

---

# Architecture

```text
LLM Agent
   │
   ▼
FastAPI / gRPC API Layer
   │
   ▼
Memory Router
   │
   ├── Tier 0: Redis        (Working Memory)
   ├── Tier 1: Pinecone     (Episodic Memory)
   ├── Tier 2: Neo4j        (Semantic Memory)
   └── Tier 3: PostgreSQL   (Procedural Memory)

Supporting Services:
- Consolidation Pipeline
- Metrics Exporter
- Policy Engine
- Kafka Write Log
```

---

# Memory Tiers

| Tier       | Backend    | Purpose                                   |
| ---------- | ---------- | ----------------------------------------- |
| Working    | Redis      | Fast short-term session context           |
| Episodic   | Pinecone   | Vector retrieval over prior sessions      |
| Semantic   | Neo4j      | Structured concepts and relationships     |
| Procedural | PostgreSQL | Tool traces, workflows, versioned records |

---

# Core Features

## 1. Multi-Tier Memory Routing

Memories are routed to the most appropriate storage tier based on type, latency needs, and retrieval strategy.

Examples:

* Temporary task context → Redis
* Interaction summaries → Pinecone
* Learned preferences → Neo4j
* Reusable workflows → PostgreSQL

---

## 2. Background Consolidation Pipeline

A scheduled consolidation workflow transforms short-term experiences into durable long-term knowledge.

Typical stages:

1. Retrieve recent episodic memories
2. Cluster related memories
3. Summarize recurring patterns
4. Promote useful concepts into semantic memory
5. Archive stale or low-value entries

This helps control storage growth while improving organization and recall quality.

---

## 3. Importance-Based Retention

Memories can be prioritized using signals such as:

* Recency
* Frequency of reuse
* Relevance
* Novelty
* Successful outcomes

Higher-value memories can be retained longer or promoted across tiers.

---

## 4. Cross-Agent Federation

Supports policy-based memory sharing for multi-agent systems.

Example access models:

* Public memory pools
* Team-scoped access
* Agent allowlists
* Field redaction rules

---

## 5. Observability

Integrated monitoring stack includes:

* Prometheus metrics
* Grafana dashboards
* Health endpoints
* Tier status inspection

---

# Tech Stack

## Backend

* Python 3.11
* FastAPI
* gRPC
* AsyncIO

## Datastores

* Redis
* Pinecone
* Neo4j
* PostgreSQL

## Infrastructure

* Docker Compose
* Kafka
* Open Policy Agent
* Prometheus
* Grafana

---

# Local Benchmark Results

Measured locally in Docker using concurrent synthetic requests against the lightweight `/health` endpoint.

| Metric          | Result        |
| --------------- | ------------- |
| Throughput      | 5,537 req/sec |
| Average Latency | 1.66 ms       |
| P95 Latency     | 2.23 ms       |
| P99 Latency     | 13.55 ms      |

*These figures reflect local health-check performance, not full write/search memory workloads.*

---

# Screenshots

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

Concurrent benchmark results for the `/health` endpoint.

---

# API Surface

## REST Endpoints

* `GET /health`
* `GET /metrics`
* `GET /agents/{id}/stats`
* `POST /consolidate`
* `POST /rollback`
* `POST /policy`

## gRPC Operations

* Write memory
* Read memory
* Delete memory
* Consolidate memory
* Federated access

---

# Project Structure

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
├── tests/
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

# Quick Start

## Clone Repository

```bash
git clone https://github.com/Sanskar121543/AgentMemOS.git
cd AgentMemOS
```

## Configure Environment

Create a `.env` file:

```env
OPENAI_API_KEY=your_key
PINECONE_API_KEY=your_key
JWT_SECRET=your_secret
```

## Launch Services

```bash
docker compose up -d
```

## Open API Docs

```text
http://localhost:8000/docs
```

---

# Example Use Cases

## Personal AI Assistant

Retains preferences, tasks, and recurring context across sessions.

## Multi-Agent Research System

Shares memory selectively using policy controls.

## Coding Agent

Stores bug fixes, workflows, and reusable execution traces.

## Enterprise Knowledge Assistant

Builds searchable institutional memory over time.

---

# Engineering Highlights

* Built a multi-tier memory architecture spanning cache, vector, graph, and relational systems
* Implemented asynchronous APIs with FastAPI + gRPC
* Designed a memory consolidation workflow for long-term promotion
* Added observability with Prometheus + Grafana
* Dockerized a complete multi-service local environment
* Explored persistent memory systems for long-running LLM agents

---

# Future Improvements

* Kubernetes deployment
* Retrieval ranking improvements
* Cost-aware storage routing
* Local embedding providers
* Fine-grained TTL / retention policies

---

# License

MIT
