export type NavigationSection =
  | 'architecture'
  | 'workflows'
  | 'dag'
  | 'traces'
  | 'cache'
  | 'policies'
  | 'reconciliation'
  | 'servers'
  | 'settings'
  | 'overview'
  | 'audit';

export type WorkflowStatus = 'pending' | 'running' | 'completed' | 'failed' | 'interrupted';
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'quarantined';

export interface WorkflowItem {
  workflow_id: string;
  tenant_id: string;
  status: WorkflowStatus;
  request_prompt: string;
  created_at: string;
  duration_ms: number;
  tasks_count: number;
  completed_tasks: number;
  current_step?: string;
  checkpoint_id?: string;
  resumable: boolean;
  [key: string]: any;
}

export interface DAGTaskNode {
  id: string;
  label: string;
  tool: string;
  status: TaskStatus;
  duration_ms?: number;
  dependencies: string[];
  input_args: Record<string, any>;
  output_result?: Record<string, any>;
  error?: string;
  retries: number;
  is_quarantined?: boolean;
  [key: string]: any;
}

export interface TraceEntry {
  id: string;
  timestamp: string;
  workflow_id: string;
  task_id?: string;
  event_type: 'task_start' | 'cache_hit' | 'cache_miss' | 'upstream_call' | 'checkpoint_saved' | 'policy_eval' | 'task_complete' | 'interrupted';
  message: string;
  duration_ms?: number;
  metadata?: Record<string, any>;
  [key: string]: any;
}

export interface UpstreamServerItem {
  id: string;
  name: string;
  transport: 'stdio' | 'sse' | 'http';
  command_or_url: string;
  tools_count: number;
  status: 'healthy' | 'degraded' | 'offline';
  latency_ms: number;
  uptime_pct: number;
  tags: string[];
  tools: {
    name: string;
    description: string;
    read_only: boolean;
    cached: boolean;
  }[];
  [key: string]: any;
}

export interface CacheMetricItem {
  hits: number;
  misses: number;
  hit_ratio: number;
  leaders_total: number;
  waiters_total: number;
  coordination_savings_ms: number;
  avg_upstream_ms: number;
  avg_cache_ms: number;
  [key: string]: any;
}

export interface TenantPolicyItem {
  policy_id: string;
  tenant_id: string;
  version: number;
  is_active: boolean;
  rpm_limit: number;
  burst_limit: number;
  cache_default_ttl: number;
  max_concurrent_workflows: number;
  allowed_tools: string[];
  denied_tools: string[];
  created_at: string;
  author: string;
  change_summary: string;
  [key: string]: any;
}

export interface ReconciliationItem {
  id: string;
  workflow_id: string;
  task_id: string;
  tool_name: string;
  operation_class: 'read' | 'write' | 'payment' | 'infrastructure';
  known_outcome: 'unknown' | 'partial' | 'confirmed_failed';
  quarantined_at: string;
  status: 'pending' | 'approved_retry' | 'skipped' | 'aborted';
  reason: string;
  raw_payload: Record<string, any>;
  [key: string]: any;
}

// ── Backwards-Compatible Legacy Types (for api.ts and older views) ───────────

export type Workflow = WorkflowItem;
export interface WorkflowTask {
  task_id: string;
  workflow_id: string;
  tool_name: string;
  status: string;
  input_args: Record<string, any>;
  output_result?: Record<string, any>;
  error_message?: string;
  duration_ms?: number;
  attempt_count: number;
  [key: string]: any;
}
export type TraceEvent = TraceEntry;
export interface AuditEvent {
  event_id: string;
  timestamp: string;
  tenant_id: string;
  event_type: string;
  actor: string;
  resource_id: string;
  status: string;
  details: Record<string, any>;
  [key: string]: any;
}
export interface UpstreamServer {
  server_id: string;
  name: string;
  transport: string;
  connection_config: Record<string, any>;
  is_enabled: boolean;
  status: string;
  tags: string[];
  tools: any[];
  [key: string]: any;
}
export interface CoordinationMetrics {
  cache_hits: number;
  cache_misses: number;
  distributed_singleflight: {
    distributed_leaders_total: number;
    distributed_waiters_total: number;
    coordination_fallbacks_total: number;
    waiter_timeouts_total: number;
  };
  metrics?: Record<string, any>;
  [key: string]: any;
}
export interface WorkflowResumability {
  workflow_id: string;
  resumable: boolean;
  reason?: string;
  recommended_action?: string;
  checkpoint_id?: string;
  [key: string]: any;
}
export interface WorkflowCheckpoint {
  checkpoint_id: string;
  step_number: number;
  created_at: string;
  state_summary?: string;
  [key: string]: any;
}
export interface TenantPolicy {
  policy_id: string;
  tenant_id: string;
  policy_version: number;
  is_active: boolean;
  description: string;
  tool_rules: {
    allowed_tools: string[];
    denied_tools: string[];
    read_only_tools: string[];
    default_deny: boolean;
  };
  cache_rules: {
    enabled: boolean;
    default_ttl_seconds: number;
    max_ttl_seconds: number;
  };
  resource_limits: {
    rate_limit_rpm: number;
    burst_limit: number;
    max_concurrent_workflows: number;
  };
  [key: string]: any;
}
export interface WorkflowReconciliation {
  reconciliation_id: string;
  workflow_id: string;
  task_id: string;
  tool_name: string;
  operation_class: string;
  known_outcome: string;
  status: string;
  reason?: string;
  action_taken?: string;
  reviewer?: string;
  decided_at?: string;
  decision_notes?: string;
  raw_payload?: Record<string, any>;
  [key: string]: any;
}
export interface SystemHealth {
  gateway: { status: string; latency_ms?: number; latencyMs?: number; [key: string]: any };
  controlApi: { status: string; latency_ms?: number; latencyMs?: number; [key: string]: any };
  runtime: { status: string; latency_ms?: number; latencyMs?: number; [key: string]: any };
  postgres: { status: string; [key: string]: any };
  redis: { status: string; [key: string]: any };
  [key: string]: any;
}
