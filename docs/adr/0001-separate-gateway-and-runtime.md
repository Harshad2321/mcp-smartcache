# ADR 0001: Separation of MCP Gateway and Agent Runtime

## Status
Accepted

## Context
MCP SmartCache coordinates tool executions for LLM agents using the Model Context Protocol (MCP). We must decide whether the gateway (registry, routing, caching, deduplication, policy enforcement) and the runtime (LangGraph workflow planning, dependency graph execution) should reside in a single process or exist as separate, independently deployable processes/modules.

## Decision
We enforce a strict physical and logical boundary between the **Gateway** and the **Runtime**:
1. **Separate Processes and Modules**: `gateway/` and `runtime/` are distinct packages.
2. **Interface Boundary**: The runtime interacts with upstream tools **exclusively** via the gateway's MCP client interface (JSON-RPC over standard MCP transports: stdio or SSE/HTTP).
3. **No Direct Upstream Access**: The runtime has no knowledge of, or direct connectivity to, upstream MCP servers.
4. **No Agent-Side Caching**: The runtime has no cache implementation; caching is purely a gateway infrastructure concern.

## Rejected Alternatives & Rationale
- **Embedded Gateway / Monolithic Runtime**: Embedding tool caching and routing directly into the LangGraph runtime process.
  - *Why rejected*: Coupling caching to the agent runtime couples cache lifetime to agent workflow execution, prevents cache sharing across heterogeneous agents/sessions, compromises security isolation (giving LLM code direct access to upstream secrets and connections), and prevents independent scaling of tool traffic from reasoning loops.
- **Shared In-Memory Broker**: Allowing the runtime to invoke gateway methods via Python in-memory imports while running in the same process.
  - *Why rejected*: Violates module boundaries in practice, creates implicit dependencies, leaks memory across runs, and makes it trivial for agent code to bypass execution policies or access un-cached internal handles.
