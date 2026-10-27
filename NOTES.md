# MCP SmartCache — Architectural Invariants & Boundary Rules

This document outlines the non-negotiable architectural invariants and module boundary rules governing the MCP SmartCache codebase.

## Core Architectural Invariants

1. **Process & Interface Separation**
   - The Gateway (`gateway/`) and Agent Runtime (`runtime/`) are distinct, decoupled components.
   - The agent runtime must communicate with tools **exclusively** through the gateway's MCP interface.
   - The runtime must never have a private or direct network/process connection to upstream MCP servers.
   - The runtime must never implement its own caching layer.

2. **Deterministic Policy Enforcement**
   - Security policies, cache eligibility, rate limits, concurrency caps, and write approvals must be enforced deterministically in application code.
   - Policies must never rely on LLM self-policing or instructions in system prompts.

3. **Strict Caching Discipline**
   - Only explicitly allowlisted read-only tools are cacheable.
   - Side-effecting operations (writes) are never cached, never replayed, and require explicit approval.
   - Cache keys must strictly incorporate: `tenant_id`, `server_name`, `tool_name`, `tool_schema_version`, and canonical JSON-serialized `arguments`.
   - Complete multi-tenant cache isolation: cached results for one tenant must never be served to another.

4. **Single Source of Truth for State**
   - **Redis**: Ephemeral tool cache, singleflight coalescence locks, distributed concurrency locks.
   - **PostgreSQL**: Durable configuration, server registrations, tenant auth, audit trails, and completed run summaries.
   - **LangGraph Checkpoints**: In-flight agent workflow state during execution.

5. **Graceful Degradation**
   - An outage of the cache store (Redis) must degrade to live execution without caching; it must not fail the entire request or take down the gateway.
   - Upstream MCP server failures must return structured JSON-RPC error responses rather than crashing the gateway.

6. **Zero Secrets in Caches and Logs**
   - Upstream credentials, authorization headers, and sensitive tokens must never appear in cache keys, cached payloads, or execution logs.
