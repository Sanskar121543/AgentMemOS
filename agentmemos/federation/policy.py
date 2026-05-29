"""
agentmemos.federation.policy
─────────────────────────────
OPA-compatible policy engine for cross-agent memory federation.

Every cross-agent memory read is evaluated against a policy before
any data is returned. This prevents session leakage between agents
with different trust levels or team boundaries.

Policy evaluation order
───────────────────────
  1. Public  — memory is shared globally
  2. Team    — requesting agent is in allowed_teams
  3. Allow   — requesting agent is in allowed_agents list
  4. Deny    — default

Field redaction
───────────────
  Policies can specify fields to scrub from returned entries.
  Currently redacts from MemoryEntry.metadata keys.

OPA integration
───────────────
  In production, policy evaluation delegates to an OPA sidecar via HTTP.
  This module provides a local evaluator that mirrors OPA's Rego rules
  and falls back to it if OPA_URL is not configured.
"""

from __future__ import annotations

import os

import httpx

from agentmemos.core.models import FederationPolicy, RankedMemory

OPA_URL     = os.getenv("OPA_URL", "")   # e.g. http://opa:8181
OPA_POLICY  = os.getenv("OPA_POLICY_PATH", "agentmemos/federation/allow")
OPA_TIMEOUT = float(os.getenv("OPA_TIMEOUT_SECS", "0.5"))


# ─────────────────────────────────────────────────────────────────────────────
# Policy Store (in-memory, backed by PostgreSQL in production)
# ─────────────────────────────────────────────────────────────────────────────

class PolicyStore:
    """
    Lightweight in-process policy store.
    Production: load from PostgreSQL on startup and sync on write.
    """

    def __init__(self) -> None:
        self._policies: dict[str, FederationPolicy] = {}

    def register(self, policy: FederationPolicy) -> None:
        self._policies[policy.owner_agent_id] = policy

    def get(self, owner_agent_id: str) -> FederationPolicy | None:
        return self._policies.get(owner_agent_id)

    def all_policies(self) -> list[FederationPolicy]:
        return list(self._policies.values())


# ─────────────────────────────────────────────────────────────────────────────
# Policy Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class PolicyDecision:
    def __init__(
        self,
        allow: bool,
        reason: str,
        redact_fields: list[str] | None = None,
    ) -> None:
        self.allow = allow
        self.reason = reason
        self.redact_fields = redact_fields or []


class FederationPolicyEngine:
    """
    Evaluates whether agent A can read agent B's memories.

    Tries OPA sidecar first (if OPA_URL is set);
    falls back to local Rego-equivalent logic.
    """

    def __init__(self, store: PolicyStore) -> None:
        self._store = store
        self._http: httpx.AsyncClient | None = None

    async def initialise(self) -> None:
        if OPA_URL:
            self._http = httpx.AsyncClient(timeout=OPA_TIMEOUT)

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    async def evaluate(
        self,
        requesting_agent_id: str,
        target_agent_id: str,
        requesting_team: str | None = None,
    ) -> PolicyDecision:
        """
        Returns PolicyDecision for a cross-agent read request.
        """
        if requesting_agent_id == target_agent_id:
            # Agent always has access to its own memories
            return PolicyDecision(allow=True, reason="self_access")

        policy = self._store.get(target_agent_id)
        if policy is None:
            return PolicyDecision(allow=False, reason="no_policy_registered")

        # Try OPA sidecar
        if self._http and OPA_URL:
            try:
                return await self._opa_evaluate(
                    requesting_agent_id,
                    requesting_team,
                    policy,
                )
            except Exception:
                pass  # Fall through to local evaluation

        # Local evaluation
        return self._local_evaluate(
            requesting_agent_id,
            requesting_team,
            policy,
        )

    async def _opa_evaluate(
        self,
        requesting_agent_id: str,
        requesting_team: str | None,
        policy: FederationPolicy,
    ) -> PolicyDecision:
        assert self._http is not None

        input_doc = {
            "input": {
                "requesting_agent": requesting_agent_id,
                "requesting_team":  requesting_team,
                "policy": policy.model_dump(mode="json"),
            }
        }
        url = f"{OPA_URL}/v1/data/{OPA_POLICY}"
        response = await self._http.post(url, json=input_doc)
        response.raise_for_status()

        result = response.json().get("result", {})
        allow  = bool(result.get("allow", False))
        reason = result.get("reason", "opa_decision")
        redact = result.get("redact_fields", [])

        return PolicyDecision(allow=allow, reason=reason, redact_fields=redact)

    @staticmethod
    def _local_evaluate(
        requesting_agent_id: str,
        requesting_team: str | None,
        policy: FederationPolicy,
    ) -> PolicyDecision:
        """
        Mirrors the Rego rules in opa/policies/federation.rego
        """
        if policy.public:
            return PolicyDecision(
                allow=True,
                reason="policy_public",
                redact_fields=policy.redact_fields,
            )

        if requesting_team and requesting_team in policy.allowed_teams:
            return PolicyDecision(
                allow=True,
                reason="team_allowed",
                redact_fields=policy.redact_fields,
            )

        if requesting_agent_id in policy.allowed_agents:
            return PolicyDecision(
                allow=True,
                reason="agent_allowed",
                redact_fields=policy.redact_fields,
            )

        return PolicyDecision(
            allow=False,
            reason="denied_no_matching_rule",
        )

    # ── Field Redaction ───────────────────────────────────────────────────────

    @staticmethod
    def redact(
        memories: list[RankedMemory],
        fields: list[str],
    ) -> list[RankedMemory]:
        """
        Remove specified metadata fields from returned memories.
        Creates copies — never mutates in place.
        """
        if not fields:
            return memories

        result = []
        for ranked in memories:
            entry = ranked.entry.model_copy(deep=True)
            for f in fields:
                entry.metadata.pop(f, None)
            result.append(
                RankedMemory(
                    entry=entry,
                    relevance=ranked.relevance,
                    recency=ranked.recency,
                    final_score=ranked.final_score,
                    source_tier=ranked.source_tier,
                    from_federation=True,
                )
            )
        return result
