import {
  Workflow,
  WorkflowTask,
  TraceEvent,
  AuditEvent,
  UpstreamServer,
  CoordinationMetrics,
  WorkflowResumability,
  WorkflowCheckpoint,
  TenantPolicy,
  WorkflowReconciliation,
  SystemHealth,
} from '../types';

class ApiService {
  private apiBase = '/api';
  private gatewayBase = '/gateway';
  private tenantId = 'tenant_a';
  private apiKey = 'dev-admin-secret-key-32chars-long';

  public setTenant(tenantId: string) {
    this.tenantId = tenantId;
  }

  public getTenant(): string {
    return this.tenantId;
  }

  public setApiKey(apiKey: string) {
    this.apiKey = apiKey;
  }

  public getApiKey(): string {
    return this.apiKey;
  }

  private getHeaders(): Record<string, string> {
    return {
      Authorization: `Bearer ${this.apiKey}`,
      'X-Tenant-ID': this.tenantId,
      'Content-Type': 'application/json',
    };
  }

  private async request<T>(url: string, options: RequestInit = {}): Promise<T> {
    const headers = {
      ...this.getHeaders(),
      ...(options.headers as Record<string, string> || {}),
    };

    const res = await fetch(url, { ...options, headers });
    if (!res.ok) {
      let detail = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        if (body.detail) {
          detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
        }
      } catch {
        // ignore json parse error
      }
      throw new Error(detail);
    }
    return res.json() as Promise<T>;
  }

  // ── Health Probes ──────────────────────────────────────────────────────────

  public async getSystemHealth(): Promise<SystemHealth> {
    const health: SystemHealth = {
      gateway: { status: 'unknown' },
      controlApi: { status: 'unknown' },
      runtime: { status: 'unknown' },
      redis: { status: 'unknown' },
      postgres: { status: 'unknown' },
    };

    // Check Control API & DB/Redis
    try {
      const t0 = performance.now();
      const ctrlRes = await fetch(`${this.apiBase}/health`);
      const latency = Math.round(performance.now() - t0);
      if (ctrlRes.ok) {
        const data = await ctrlRes.json();
        health.controlApi = { status: 'healthy', version: data.version, latencyMs: latency };
        health.postgres = { status: 'healthy', latencyMs: latency };
      } else {
        health.controlApi = { status: 'unhealthy' };
      }
    } catch {
      health.controlApi = { status: 'unhealthy' };
    }

    // Check Gateway & Redis
    try {
      const t0 = performance.now();
      const gwRes = await fetch(`${this.gatewayBase}/health`);
      const latency = Math.round(performance.now() - t0);
      if (gwRes.ok) {
        const data = await gwRes.json();
        health.gateway = { status: 'healthy', version: data.version, latencyMs: latency };
        health.redis = { status: 'healthy', latencyMs: latency };
      } else {
        health.gateway = { status: 'unhealthy' };
      }
    } catch {
      // Fallback: check coordination endpoint to determine redis status
      health.gateway = { status: 'unknown' };
    }

    // Check Coordination for Redis status
    try {
      const coord = await this.getCoordinationMetrics();
      if (coord.redis_connected) {
        health.redis = { status: 'healthy' };
      }
    } catch {
      // Ignore
    }

    // Assume runtime is connected if Control API is active
    health.runtime = health.controlApi.status === 'healthy' ? { status: 'healthy', version: '0.3.0' } : { status: 'unknown' };

    return health;
  }

  // ── Workflows ──────────────────────────────────────────────────────────────

  public async listWorkflows(statusFilter?: string, limit: number = 50): Promise<Workflow[]> {
    const query = statusFilter && statusFilter !== 'all' ? `?status=${encodeURIComponent(statusFilter)}&limit=${limit}` : `?limit=${limit}`;
    const data = await this.request<{ workflows: Workflow[] }>(`${this.apiBase}/workflows${query}`);
    return data.workflows || [];
  }

  public async getWorkflow(workflowId: string): Promise<Workflow> {
    return this.request<Workflow>(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}`);
  }

  public async getWorkflowTasks(workflowId: string): Promise<WorkflowTask[]> {
    const data = await this.request<{ tasks: WorkflowTask[] }>(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/tasks`);
    return data.tasks || [];
  }

  public async getWorkflowTrace(workflowId: string, limit: number = 200): Promise<TraceEvent[]> {
    const data = await this.request<{ trace_events: TraceEvent[] }>(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/trace?limit=${limit}`);
    return data.trace_events || [];
  }

  public async getWorkflowResumability(workflowId: string): Promise<WorkflowResumability> {
    return this.request<WorkflowResumability>(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/resumability`);
  }

  public async getWorkflowCheckpoints(workflowId: string): Promise<WorkflowCheckpoint[]> {
    const data = await this.request<{ checkpoints: WorkflowCheckpoint[] }>(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/checkpoints`);
    return data.checkpoints || [];
  }

  public async submitWorkflow(payload: { workflow_id?: string; tenant_id: string; prompt: string }): Promise<Workflow> {
    return this.request<Workflow>(`${this.apiBase}/workflows`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  public async resumeWorkflow(workflowId: string, action: string = 'resume'): Promise<any> {
    return this.request(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/resume`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
  }

  public async cancelWorkflow(workflowId: string): Promise<any> {
    return this.request(`${this.apiBase}/workflows/${encodeURIComponent(workflowId)}/cancel`, {
      method: 'POST',
      body: JSON.stringify({ reason: 'Operator requested cancellation from dashboard' }),
    });
  }

  // ── Servers & Tools ────────────────────────────────────────────────────────

  public async listServers(): Promise<UpstreamServer[]> {
    const data = await this.request<{ servers: UpstreamServer[] }>(`${this.apiBase}/servers`);
    return data.servers || [];
  }

  public async registerServer(serverData: Record<string, any>): Promise<any> {
    return this.request(`${this.apiBase}/servers`, {
      method: 'POST',
      body: JSON.stringify(serverData),
    });
  }

  public async refreshServer(serverId: string): Promise<any> {
    return this.request(`${this.apiBase}/servers/${encodeURIComponent(serverId)}/refresh`, {
      method: 'POST',
    });
  }

  public async deleteServer(serverId: string): Promise<any> {
    return this.request(`${this.apiBase}/servers/${encodeURIComponent(serverId)}`, {
      method: 'DELETE',
    });
  }

  public async getServerTools(serverId: string): Promise<any[]> {
    const data = await this.request<{ tools: any[] }>(`${this.apiBase}/servers/${encodeURIComponent(serverId)}/tools`);
    return data.tools || [];
  }

  // ── Cache & Performance ────────────────────────────────────────────────────

  public async getCoordinationMetrics(): Promise<CoordinationMetrics> {
    return this.request<CoordinationMetrics>(`${this.apiBase}/cache/coordination`);
  }

  public async invokeTool(toolName: string, args: Record<string, any> = {}): Promise<{ result: any; latencyMs: number; isCacheHit?: boolean }> {
    const t0 = performance.now();
    // Try via gateway directly, or fallback to gateway tool call
    const res = await fetch(`${this.gatewayBase}/call`, {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({ tool: toolName, arguments: args }),
    });
    const latencyMs = Math.round(performance.now() - t0);
    if (!res.ok) {
      let errText = `${res.status} ${res.statusText}`;
      try {
        const body = await res.json();
        errText = body.detail || errText;
      } catch {}
      throw new Error(errText);
    }
    const data = await res.json();
    return {
      result: data,
      latencyMs,
      isCacheHit: data.cached === true || latencyMs < 20,
    };
  }

  public async invalidateCache(scope: string = 'tenant', tag?: string): Promise<any> {
    return this.request(`${this.gatewayBase}/cache`, {
      method: 'DELETE',
      body: JSON.stringify({ scope, tag, tenant_id: this.tenantId }),
    });
  }

  // ── Policies & Quotas ──────────────────────────────────────────────────────

  public async getEffectivePolicy(tenantId: string): Promise<TenantPolicy> {
    return this.request<TenantPolicy>(`${this.apiBase}/policies/effective/${encodeURIComponent(tenantId)}`);
  }

  public async listPolicies(tenantId: string): Promise<TenantPolicy[]> {
    const data = await this.request<{ policies: TenantPolicy[] }>(`${this.apiBase}/policies?tenant_id=${encodeURIComponent(tenantId)}`);
    return data.policies || [];
  }

  public async validatePolicy(policy: Record<string, any>): Promise<{ valid: boolean; errors?: string[] }> {
    return this.request(`${this.apiBase}/policies/validate`, {
      method: 'POST',
      body: JSON.stringify(policy),
    });
  }

  public async createPolicy(policy: Record<string, any>): Promise<TenantPolicy> {
    return this.request<TenantPolicy>(`${this.apiBase}/policies`, {
      method: 'POST',
      body: JSON.stringify(policy),
    });
  }

  public async activatePolicy(policyId: string): Promise<any> {
    return this.request(`${this.apiBase}/policies/${encodeURIComponent(policyId)}/activate`, {
      method: 'POST',
    });
  }

  public async rollbackPolicy(policyId: string): Promise<any> {
    return this.request(`${this.apiBase}/policies/${encodeURIComponent(policyId)}/rollback`, {
      method: 'POST',
    });
  }

  // ── Reconciliations & HITL ─────────────────────────────────────────────────

  public async listReconciliations(tenantId: string): Promise<WorkflowReconciliation[]> {
    const data = await this.request<{ reconciliations: WorkflowReconciliation[] }>(`${this.apiBase}/reconciliations?tenant_id=${encodeURIComponent(tenantId)}`);
    return data.reconciliations || [];
  }

  public async decideReconciliation(
    reconciliationId: string,
    payload: { action: string; risk_acknowledged: boolean; decision_note?: string; idempotency_key: string }
  ): Promise<any> {
    return this.request(`${this.apiBase}/reconciliations/${encodeURIComponent(reconciliationId)}/decide`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }

  // ── Audit Logs ─────────────────────────────────────────────────────────────

  public async listAuditEvents(limit: number = 50): Promise<AuditEvent[]> {
    const data = await this.request<{ audit_events: AuditEvent[] }>(`${this.apiBase}/audit?limit=${limit}`);
    return data.audit_events || [];
  }
}

export const api = new ApiService();

