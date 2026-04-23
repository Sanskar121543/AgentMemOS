#!/usr/bin/env bash
# Run the full test suite with coverage.
# Usage: ./scripts/run_tests.sh [--fast]
set -euo pipefail

FAST="${1:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$FAST" == "--fast" ]]; then
    pytest tests/test_core.py tests/test_eviction_federation.py \
        -v --tb=short -x
else
    pytest tests/ \
        -v --tb=short \
        --cov=agentmemos \
        --cov-report=term-missing \
        --cov-report=html:htmlcov \
        --cov-fail-under=75
fi
