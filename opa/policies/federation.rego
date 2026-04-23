package agentmemos.federation

# ─────────────────────────────────────────────────────────────────────────────
# AgentMemOS Federation Policy
#
# Evaluated by OPA for every cross-agent memory read.
# Input shape:
#   {
#     "requesting_agent": "agent-A",
#     "requesting_team":  "team-alpha",   (optional)
#     "policy": {
#       "owner_agent_id": "agent-B",
#       "allowed_agents": ["agent-A"],
#       "allowed_teams":  ["team-alpha"],
#       "public":         false,
#       "redact_fields":  ["session_id"]
#     }
#   }
#
# Output shape:
#   {
#     "allow":         true | false,
#     "reason":        "...",
#     "redact_fields": [...]
#   }
# ─────────────────────────────────────────────────────────────────────────────

default allow = false
default reason = "denied_no_matching_rule"
default redact_fields = []

# Self-access is always allowed
allow {
    input.requesting_agent == input.policy.owner_agent_id
}

reason = "self_access" {
    input.requesting_agent == input.policy.owner_agent_id
}

# Public memory is readable by anyone
allow {
    input.policy.public == true
}

reason = "policy_public" {
    input.policy.public == true
    input.requesting_agent != input.policy.owner_agent_id
}

# Agent is in the explicit allow-list
allow {
    input.requesting_agent == input.policy.allowed_agents[_]
}

reason = "agent_allowed" {
    input.requesting_agent == input.policy.allowed_agents[_]
    input.requesting_agent != input.policy.owner_agent_id
    not input.policy.public
}

# Requesting agent's team is in the allow-list
allow {
    input.requesting_team != null
    input.requesting_team == input.policy.allowed_teams[_]
}

reason = "team_allowed" {
    input.requesting_team != null
    input.requesting_team == input.policy.allowed_teams[_]
    not input.requesting_agent == input.policy.owner_agent_id
    not input.policy.public
    not agent_explicitly_allowed
}

agent_explicitly_allowed {
    input.requesting_agent == input.policy.allowed_agents[_]
}

# Fields to redact from the response
redact_fields = input.policy.redact_fields {
    allow
    count(input.policy.redact_fields) > 0
}
