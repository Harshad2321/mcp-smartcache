# ADR 0003: Explicit Allowlist Caching Eligibility Model

## Status
Accepted

## Context
Agents issue tool calls ranging from idempotent read queries (e.g. fetching repository metadata) to state-mutating actions (e.g. pushing commits, creating issues, executing shell commands). Inappropriate caching of mutating operations or sensitive uncacheable reads causes severe data corruption, stale reads, or security vulnerabilities.

## Decision
We adopt a **strict, explicit allowlist model** for caching:
1. **Default Deny**: Tools are uncacheable by default. A tool call will never be looked up in or stored to the cache unless an explicit rule permits it.
2. **Explicit Tool Allowlist**: Only tools explicitly registered in the cache policy allowlist with an explicit positive TTL are eligible for caching.
3. **Deterministic Mutation Gating**: Tools identified or tagged as mutating/side-effecting (writes) can never be added to the cache allowlist. They always bypass cache lookup and storage.
4. **Cache Key Determinism**: The cache key is constructed strictly from: `tenant_id:server_name:tool_name:tool_schema_version:canonical_json(arguments)`.

## Rejected Alternatives & Rationale
- **Heuristic / Automatic Read-Only Detection (e.g. tool name matching `get_*`, `list_*`, `read_*`)**:
  - *Why rejected*: Naming conventions are unstandardized and dangerous. An upstream tool named `get_or_create_resource` or `read_and_ack` would be misclassified as read-only, silently replaying or suppressing side effects.
- **Model-Declared Cacheability (prompting the agent to indicate if a call is cacheable)**:
  - *Why rejected*: Violates Workspace Rule 3. LLMs hallucinate and cannot be trusted for deterministic security, isolation, or idempotency boundaries. Policy must be enforced in deterministic runtime code.
- **Cache-All With Invalidation Hooks**:
  - *Why rejected*: Impossible to reliably maintain across external, third-party MCP servers where downstream mutations occur outside the gateway's direct visibility.
