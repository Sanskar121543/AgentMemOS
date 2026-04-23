# ─────────────────────────────────────────────────────────────────────────────
# AgentMemOS — Multi-stage Dockerfile
#
# Stage 1: builder — installs all dependencies into a venv
# Stage 2: runtime — minimal image, copies venv from builder
#
# Build:  docker build -t agentmemos:latest .
# Run:    docker run -p 50051:50051 -p 8000:8000 --env-file .env agentmemos:latest
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
COPY README.md .
COPY agentmemos/ ./agentmemos/
COPY proto/ ./proto/

# Create venv and install
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

RUN pip install --upgrade pip wheel
RUN pip install -e ".[dev]"

# Compile protobuf stubs
RUN mkdir -p agentmemos/proto && \
    python -m grpc_tools.protoc \
      -I proto \
      --python_out=agentmemos/proto \
      --grpc_python_out=agentmemos/proto \
      proto/memory.proto && \
    touch agentmemos/proto/__init__.py

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy source
COPY --from=builder /build/agentmemos ./agentmemos
COPY --from=builder /build/proto ./proto

# Non-root user
RUN addgroup --gid 1001 agentmemos && \
    adduser --uid 1001 --gid 1001 --no-create-home --disabled-password agentmemos
USER agentmemos

EXPOSE 50051 8000

# Health check via REST /health endpoint
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Start both gRPC and REST servers
CMD ["sh", "-c", "python -m agentmemos.server.grpc_server & uvicorn agentmemos.server.rest_api:app --host 0.0.0.0 --port 8000 --workers 2"]