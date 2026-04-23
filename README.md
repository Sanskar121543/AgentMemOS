# AgentMemOS

**Hierarchical Memory System for LLM Agents**

AgentMemOS is a distributed memory management system for autonomous AI agents that provides persistent, organized, cross-session memory across four cognitively inspired tiers: **working**, **episodic**, **semantic**, and **procedural** memory.

It is designed to help long-running LLM agents remember relevant context, retain learned knowledge, preserve workflows, and retrieve information efficiently across sessions.

---

# Why AgentMemOS?

Most LLM agents are stateless by default. Once a session ends, memory is lost unless external systems are added.

AgentMemOS solves this by introducing a structured memory architecture:

* **Working Memory** → short-term active context
* **Episodic Memory** → past events and interactions
* **Semantic Memory** → facts and concepts learned over time
* **Procedural Memory** → workflows, tool usage, and execution history

This enables agents to behave more consistently and improve over repeated usage.

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

Background Services:
- Consolidation Pipeline
- Metrics Exporter
- Policy Engine
- Kafka Write Log
```

---

# Memory Tiers

| Tier       | Backend    | Purpose                                  |
| ---------- | ---------- | ---------------------------------------- |
| Working    | Redis      | Fast short-term context storage          |
| Episodic   | Pinecone   | Vector search over previous interactions |
| Semantic   | Neo4j      | Facts, concepts, relationships           |
| Procedural | PostgreSQL | Workflows, tool traces, version history  |

---

# Core Features

## 1. Multi-Tier Memory Routing

Incoming memories are classified and stored in the most relevant tier.

Examples:

* Temporary task context → Redis
* Conversation outcome → Pinecone
* Learned user preference → Neo4j
* Successful tool chain → PostgreSQL

---

## 2. Background Memory Consolidation

A scheduled consolidation pipeline processes episodic memories:

1. Fetch recent memories
2. Cluster related memories
3. Summarize recurring patterns
4. Promote long-term knowledge into semantic memory
5. Archive stale low-value entries

This reduces storage growth while improving knowledge quality.

---

## 3. Memory Importance Scoring

Each memory can be ranked using signals such as:

* Recency
* Reuse frequency
* Relevance
* Novelty
* Outcome success

Higher-value memories are retained longer.

---

## 4. Cross-Agent Federation

Multiple agents can selectively share memory through policy-based access rules.

Supports:

* Public memories
* Team-scoped access
* Agent allowlists
* Field redaction

---

## 5. Observability

Integrated monitoring stack:

* Prometheus metrics
* Grafana dashboards
* Health endpoints
* Tier status checks

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

## Infra

* Docker Compose
* Kafka
* Open Policy Agent
* Prometheus
* Grafana

---

# Local Benchmark Results

Measured on local Docker environment.

| Endpoint             | Result        |
| -------------------- | ------------- |
| `/health` throughput | 5,537 req/sec |
| Average latency      | 1.66 ms       |
| P95 latency          | 2.23 ms       |
| P99 latency          | 13.55 ms      |

---

# API Endpoints

## REST

* `GET /health`
* `GET /metrics`
* `GET /agents/{id}/stats`
* `POST /consolidate`
* `POST /rollback`
* `POST /policy`

## gRPC

* Write memory
* Read memory
* Delete memory
* Consolidate memory
* Federated memory access

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
├── monitoring/
├── proto/
├── tests/
├── docker-compose.yml
├── Dockerfile
└── README.md
```

---

# Quick Start

## 1. Clone Repository

```bash
git clone https://github.com/Sanskar121543/AgentMemOS.git
cd AgentMemOS
```

## 2. Configure Environment

Create `.env`

```env
OPENAI_API_KEY=your_key
PINECONE_API_KEY=your_key
JWT_SECRET=your_secret
```

## 3. Start Services

```bash
docker compose up -d
```

## 4. Run API

```bash
http://localhost:8000/docs
```

---

# Example Use Cases

## Personal AI Assistant

Remembers preferences, tasks, habits.

## Multi-Agent Research System

Agents share findings while preserving access control.

## Long-Horizon Coding Agent

Stores bug fixes, workflows, deployment history.

## Enterprise Knowledge Agent

Builds searchable institutional memory.

---

# Engineering Highlights

* Built distributed multi-tier memory architecture
* Integrated vector DB + graph DB + SQL + cache layers
* Implemented async Python APIs with FastAPI + gRPC
* Added monitoring and health instrumentation
* Dockerized full local microservice stack
* Designed memory consolidation workflow

---

# Future Improvements

* Kubernetes production deployment
* Streaming retrieval ranking
* Fine-grained memory TTL policies
* Cost-aware storage routing
* Local embedding backends

---

# License

MIT
