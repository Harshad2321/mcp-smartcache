import { useState, useEffect, useRef, FormEvent } from 'react';
import './index.css';

interface Workflow {
  workflow_id: string;
  tenant_id: string;
  correlation_id?: string;
  workflow_type: string;
  request_prompt?: string;
  status: string;
  plan?: any;
  summary?: any;
  final_answer?: string;
  error?: string;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
}

interface WorkflowTask {
  task_id: string;
  workflow_id: string;
  tenant_id: string;
  tool_name: string;
  status: string;
  dependencies: string[];
  attempt_count: number;
  result?: any;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  duration_ms?: number;
}

interface TraceEvent {
  id?: number;
  event_id: string;
  workflow_id: string;
  tenant_id: string;
  correlation_id?: string;
  event_type: string;
  task_id?: string;
  timestamp: string;
  details: Record<string, any>;
}

interface AuditEvent {
  id?: number;
  event_id: string;
  tenant_id?: string;
  actor: string;
  action: string;
  resource_type: string;
  resource_id?: string;
  timestamp: string;
  outcome: string;
  metadata: Record<string, any>;
}

interface UpstreamServer {
  server_id: string;
  name: string;
  transport: 'stdio' | 'sse';
  connection_config?: Record<string, any>;
  configuration?: Record<string, any>;
  is_enabled?: boolean;
  enabled?: boolean;
  tenant_id?: string;
  config_version: number;
  status: string;
  tools_discovered: number;
  last_health_check?: string;
  last_connected_at?: string;
  last_error?: string;
  sanitized_error?: string;
  tags?: string[];
  created_at: string;
  updated_at: string;
}

interface CoordinationMetrics {
  coordination_status?: string;
  distributed_coordination_enabled?: boolean;
  redis_connected?: boolean;
  cluster_replicas_coordinated?: number;
  distributed_singleflight?: {
    distributed_leaders_total: number;
    distributed_waiters_total: number;
    lock_acquisition_failures_total: number;
    lease_expirations_total: number;
    waiter_timeouts_total: number;
    coordination_fallbacks_total: number;
  };
  metrics?: {
    distributed_leaders_total: number;
    distributed_waiters_total: number;
    lock_acquisition_failures_total: number;
    lease_expirations_total: number;
    waiter_timeouts_total: number;
    coordination_fallbacks_total: number;
    local_singleflight_leaders_total: number;
    local_singleflight_waiters_total: number;
    cache_hits_total: number;
    cache_misses_total: number;
    cache_invalidations_total: number;
  };
}

interface WorkflowResumability {
  workflow_id: string;
  status: string;
  resumable: boolean;
  reason: string;
  latest_checkpoint_id?: string;
  checkpoint_timestamp?: string;
  graph_version: string;
  completed_tasks: string[];
  eligible_tasks: string[];
  blocked_unsafe_tasks: string[];
}

interface WorkflowCheckpoint {
  checkpoint_id: string;
  workflow_id: string;
  tenant_id: string;
  graph_version: string;
  execution_state: string;
  completed_tasks: string[];
  pending_tasks: string[];
  created_at: string;
}

interface ToolRules {
  allowed_tools?: string[];
  denied_tools?: string[];
  read_only_tools?: string[];
  default_deny?: boolean;
  allow_admin_override?: boolean;
}

interface CacheRules {
  enabled?: boolean;
  default_ttl_seconds?: number;
  max_ttl_seconds?: number;
  max_cacheable_bytes?: number;
  max_response_bytes?: number;
  tool_ttl_overrides?: Record<string, number>;
  tool_ttls?: Record<string, number>;
  bypass_tools?: string[];
  bypass_cache?: boolean;
}

interface ResourceLimits {
  max_concurrent_calls?: number;
  max_concurrent_upstream?: number;
  max_concurrent_workflows?: number;
  max_tasks_per_workflow?: number;
  max_execution_duration_seconds?: number;
  max_workflow_duration_seconds?: number;
  rate_limit_rpm?: number;
  burst_limit?: number;
  max_input_bytes?: number;
  max_input_payload_bytes?: number;
  max_output_bytes?: number;
  max_output_payload_bytes?: number;
}

interface TenantPolicy {
  policy_id: string;
  tenant_id: string;
  version?: number;
  policy_version?: number;
  is_active?: boolean;
  tool_rules?: ToolRules;
  cache_rules?: CacheRules;
  resource_limits?: ResourceLimits;
  created_at: string;
  updated_at: string;
  actor?: string;
  created_by?: string;
  description?: string;
}

interface WorkflowReconciliation {
  reconciliation_id: string;
  workflow_id: string;
  task_id: string;
  tenant_id: string;
  checkpoint_id?: string;
  tool_name: string;
  operation_class: string;
  reason: string;
  known_outcome: string;
  status: string;
  decision_notes?: string;
  reviewer?: string;
  action_taken?: string;
  decided_at?: string;
  created_at: string;
  audit_metadata?: Record<string, any>;
}

export default function App(): JSX.Element {
  const [activeTab, setActiveTab] = useState<'workflows' | 'detail' | 'servers' | 'cache' | 'audit' | 'policies' | 'reconciliations'>('workflows');
  const [tenantId, setTenantId] = useState<string>('tenant_a');
  const [apiKey] = useState<string>('dev-admin-secret-key-32chars-long');

  // Workflows List State
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [isLoadingWorkflows, setIsLoadingWorkflows] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Selected Workflow Detail State
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [tasks, setTasks] = useState<WorkflowTask[]>([]);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [selectedTask, setSelectedTask] = useState<WorkflowTask | null>(null);
  const [resumability, setResumability] = useState<WorkflowResumability | null>(null);
  const [checkpoints, setCheckpoints] = useState<WorkflowCheckpoint[]>([]);
  const [isResuming, setIsResuming] = useState<boolean>(false);
  const [resumeSuccessMsg, setResumeSuccessMsg] = useState<string | null>(null);

  // Live SSE Stream State
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const eventSourceRef = useRef<EventSource | null>(null);

  // Upstream Servers State (Phase 5 Feature A)
  const [servers, setServers] = useState<UpstreamServer[]>([]);
  const [isLoadingServers, setIsLoadingServers] = useState<boolean>(false);
  const [showRegisterModal, setShowRegisterModal] = useState<boolean>(false);
  const [inspectingServerTools, setInspectingServerTools] = useState<{ serverId: string; tools: any[] } | null>(null);
  const [regServerId, setRegServerId] = useState<string>('git_explorer');
  const [regServerName, setRegServerName] = useState<string>('Git Explorer Upstream');
  const [regTransport, setRegTransport] = useState<'stdio' | 'sse'>('stdio');
  const [regCommand, setRegCommand] = useState<string>('python');
  const [regArgs, setRegArgs] = useState<string>('-m, gateway.mock_servers.repository');
  const [regUrl, setRegUrl] = useState<string>('http://mock-upstream:8001/sse');
  const [regTags, setRegTags] = useState<string>('git, repository, code');
  const [isRegisteringServer, setIsRegisteringServer] = useState<boolean>(false);

  // Distributed Cache Metrics State (Phase 5 Feature B)
  const [coordination, setCoordination] = useState<CoordinationMetrics | null>(null);
  const [isLoadingCoordination, setIsLoadingCoordination] = useState<boolean>(false);
  const [cacheAutoRefresh, setCacheAutoRefresh] = useState<boolean>(true);

  // Modal State
  const [showSubmitModal, setShowSubmitModal] = useState<boolean>(false);
  const [promptInput, setPromptInput] = useState<string>(
    'Investigate the Google SmartCache repository. Retrieve metadata, commit history, and open issues.'
  );
  const [ownerInput, setOwnerInput] = useState<string>('google');
  const [repoInput, setRepoInput] = useState<string>('smartcache');
  const [concurrencyInput, setConcurrencyInput] = useState<number>(4);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);

  // Audit Logs State
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [isLoadingAudit, setIsLoadingAudit] = useState<boolean>(false);

  // Phase 6 Feature A/B: Tenant Policy State
  const [effectivePolicy, setEffectivePolicy] = useState<TenantPolicy | null>(null);
  const [policies, setPolicies] = useState<TenantPolicy[]>([]);
  const [isLoadingPolicies, setIsLoadingPolicies] = useState<boolean>(false);
  const [showPolicyModal, setShowPolicyModal] = useState<boolean>(false);
  const [policyEditorJson, setPolicyEditorJson] = useState<string>('');
  const [policyValidation, setPolicyValidation] = useState<{ valid: boolean; errors?: string[]; warnings?: string[] } | null>(null);
  const [isValidatingPolicy, setIsValidatingPolicy] = useState<boolean>(false);
  const [isSavingPolicy, setIsSavingPolicy] = useState<boolean>(false);
  const [policySuccessMsg, setPolicySuccessMsg] = useState<string | null>(null);

  // Phase 6 Feature D: Workflow Reconciliation State
  const [reconciliations, setReconciliations] = useState<WorkflowReconciliation[]>([]);
  const [isLoadingReconciliations, setIsLoadingReconciliations] = useState<boolean>(false);
  const [selectedReconciliation, setSelectedReconciliation] = useState<WorkflowReconciliation | null>(null);
  const [decisionAction, setDecisionAction] = useState<string>('approve_retry');
  const [decisionNotes, setDecisionNotes] = useState<string>('');
  const [idempotencyKey, setIdempotencyKey] = useState<string>('');
  const [riskAcknowledged, setRiskAcknowledged] = useState<boolean>(false);
  const [isSubmittingDecision, setIsSubmittingDecision] = useState<boolean>(false);
  const [decisionSuccessMsg, setDecisionSuccessMsg] = useState<string | null>(null);

  // Base API URL
  const API_BASE = '/api';

  const getHeaders = () => ({
    'Authorization': `Bearer ${apiKey}`,
    'X-Tenant-ID': tenantId,
    'Content-Type': 'application/json',
  });

  // ── Fetch Workflows ────────────────────────────────────────────────────────

  const fetchWorkflows = async () => {
    setIsLoadingWorkflows(true);
    setErrorMsg(null);
    try {
      const url = statusFilter === 'all'
        ? `${API_BASE}/workflows?limit=50`
        : `${API_BASE}/workflows?status=${statusFilter}&limit=50`;
      const res = await fetch(url, { headers: getHeaders() });
      if (!res.ok) {
        throw new Error(`Failed to load workflows: ${res.status} ${res.statusText}`);
      }
      const data = await res.json();
      setWorkflows(data.workflows || []);
    } catch (err: any) {
      setErrorMsg(err.message || 'Error fetching workflows');
    } finally {
      setIsLoadingWorkflows(false);
    }
  };

  useEffect(() => {
    fetchWorkflows();
  }, [tenantId, statusFilter]);

  // ── Fetch Workflow Details, Checkpoints & Resumability ─────────────────────

  const loadWorkflowDetails = async (wfId: string) => {
    setSelectedWorkflowId(wfId);
    setActiveTab('detail');
    setErrorMsg(null);
    setResumeSuccessMsg(null);

    try {
      const [wfRes, tasksRes, traceRes, resumeRes, cpRes] = await Promise.all([
        fetch(`${API_BASE}/workflows/${wfId}`, { headers: getHeaders() }),
        fetch(`${API_BASE}/workflows/${wfId}/tasks`, { headers: getHeaders() }),
        fetch(`${API_BASE}/workflows/${wfId}/trace?limit=200`, { headers: getHeaders() }),
        fetch(`${API_BASE}/workflows/${wfId}/resumability`, { headers: getHeaders() }),
        fetch(`${API_BASE}/workflows/${wfId}/checkpoints`, { headers: getHeaders() }),
      ]);

      if (wfRes.ok) {
        const wfData = await wfRes.json();
        setSelectedWorkflow(wfData);
      }
      if (tasksRes.ok) {
        const tasksData = await tasksRes.json();
        setTasks(tasksData.tasks || []);
      }
      if (traceRes.ok) {
        const traceData = await traceRes.json();
        setTraceEvents(traceData.events || []);
      }
      if (resumeRes.ok) {
        const rData = await resumeRes.json();
        setResumability(rData);
      }
      if (cpRes.ok) {
        const cpData = await cpRes.json();
        setCheckpoints(cpData.checkpoints || []);
      }
    } catch (err: any) {
      setErrorMsg(`Error loading workflow details: ${err.message}`);
    }
  };

  // ── Handle Safe Workflow Resume ─────────────────────────────────────────────

  const handleResumeWorkflow = async (wfId: string) => {
    setIsResuming(true);
    setErrorMsg(null);
    setResumeSuccessMsg(null);

    try {
      const res = await fetch(`${API_BASE}/workflows/${wfId}/resume`, {
        method: 'POST',
        headers: getHeaders(),
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `Resumption rejected (${res.status})`);
      }

      setResumeSuccessMsg(`Workflow successfully resumed! Safe eligible tasks scheduled; completed tasks reused.`);
      // Start live trace streaming for the resumed workflow
      startStream(wfId);
      // Reload details after brief delay
      setTimeout(() => loadWorkflowDetails(wfId), 500);
    } catch (err: any) {
      setErrorMsg(err.message || 'Workflow resumption failed');
    } finally {
      setIsResuming(false);
    }
  };

  // ── Upstream Server Management (Phase 5 Feature A) ──────────────────────────

  const fetchServers = async () => {
    setIsLoadingServers(true);
    setErrorMsg(null);
    try {
      const res = await fetch(`${API_BASE}/servers`, { headers: getHeaders() });
      if (!res.ok) throw new Error(`Failed to load upstream servers: ${res.statusText}`);
      const data = await res.json();
      setServers(Array.isArray(data) ? data : data.servers || []);
    } catch (err: any) {
      setErrorMsg(err.message || 'Error fetching servers');
    } finally {
      setIsLoadingServers(false);
    }
  };

  const handleToggleServerEnabled = async (serverId: string, currentEnabled: boolean) => {
    try {
      const res = await fetch(`${API_BASE}/servers/${serverId}`, {
        method: 'PATCH',
        headers: getHeaders(),
        body: JSON.stringify({ is_enabled: !currentEnabled }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to update server status');
      }
      await fetchServers();
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const handleRefreshServer = async (serverId: string) => {
    try {
      const res = await fetch(`${API_BASE}/servers/${serverId}/refresh`, {
        method: 'POST',
        headers: getHeaders(),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to refresh server');
      }
      await fetchServers();
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const handleInspectTools = async (serverId: string) => {
    try {
      const res = await fetch(`${API_BASE}/servers/${serverId}/tools`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setInspectingServerTools({ serverId, tools: data.tools || [] });
      }
    } catch (err: any) {
      setErrorMsg(`Failed to inspect tools: ${err.message}`);
    }
  };

  const handleRegisterServer = async (e: FormEvent) => {
    e.preventDefault();
    setIsRegisteringServer(true);
    setErrorMsg(null);
    try {
      let config: Record<string, any> = {};
      if (regTransport === 'stdio') {
        const argsList = regArgs.split(',').map((a) => a.trim()).filter(Boolean);
        config = { command: regCommand, args: argsList };
      } else {
        config = { url: regUrl };
      }

      const tagsList = regTags.split(',').map((t) => t.trim()).filter(Boolean);

      const payload = {
        server_id: regServerId,
        name: regServerName,
        transport: regTransport,
        connection_config: config,
        is_enabled: true,
        tags: tagsList,
      };

      const res = await fetch(`${API_BASE}/servers`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || 'Failed to register server');
      }

      setShowRegisterModal(false);
      await fetchServers();
    } catch (err: any) {
      setErrorMsg(err.message);
    } finally {
      setIsRegisteringServer(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'servers') {
      fetchServers();
    }
  }, [activeTab]);

  // ── Distributed Cache Metrics (Phase 5 Feature B) ───────────────────────────

  const fetchCoordinationMetrics = async () => {
    setIsLoadingCoordination(true);
    try {
      const res = await fetch(`${API_BASE}/cache/coordination`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setCoordination(data);
      }
    } catch {
      // ignore
    } finally {
      setIsLoadingCoordination(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'cache') {
      fetchCoordinationMetrics();
      if (cacheAutoRefresh) {
        const interval = setInterval(fetchCoordinationMetrics, 5000);
        return () => clearInterval(interval);
      }
    }
  }, [activeTab, cacheAutoRefresh]);

  // ── Live SSE Streaming ─────────────────────────────────────────────────────

  const startStream = (wfId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const streamUrl = `${API_BASE}/workflows/${wfId}/stream`;
    const es = new EventSource(streamUrl);
    eventSourceRef.current = es;
    setIsStreaming(true);

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data && data.event_type) {
          setTraceEvents((prev) => {
            if (prev.some((item) => item.event_id === data.event_id)) return prev;
            return [...prev, data];
          });
        }
      } catch {
        // ignore raw comments
      }
    };

    es.addEventListener('workflow_end', () => {
      setIsStreaming(false);
      es.close();
      loadWorkflowDetails(wfId);
    });

    es.onerror = () => {
      setIsStreaming(false);
      es.close();
    };
  };

  useEffect(() => {
    if (selectedWorkflowId && activeTab === 'detail') {
      const isRunning = selectedWorkflow?.status === 'running' || selectedWorkflow?.status === 'pending';
      if (isRunning) {
        startStream(selectedWorkflowId);
      }
    }
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        setIsStreaming(false);
      }
    };
  }, [selectedWorkflowId, selectedWorkflow?.status, activeTab]);

  // ── Submit Workflow ────────────────────────────────────────────────────────

  const handleSubmitWorkflow = async (e: FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMsg(null);
    try {
      const payload = {
        prompt: promptInput,
        owner: ownerInput,
        repo: repoInput,
        max_concurrency: concurrencyInput,
      };

      const res = await fetch(`${API_BASE}/workflows`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Submission failed (${res.status})`);
      }

      const data = await res.json();
      setShowSubmitModal(false);
      await fetchWorkflows();
      if (data.workflow_id) {
        loadWorkflowDetails(data.workflow_id);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Workflow submission failed');
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Fetch Audit Logs ───────────────────────────────────────────────────────

  const fetchAuditLogs = async () => {
    setIsLoadingAudit(true);
    try {
      const res = await fetch(`${API_BASE}/audit?limit=50`, { headers: getHeaders() });
      if (res.ok) {
        const data = await res.json();
        setAuditEvents(data.audit_events || []);
      }
    } catch {
      // ignore
    } finally {
      setIsLoadingAudit(false);
    }
  };

  // ── Phase 6: Fetch Policies ───────────────────────────────────────────────

  const fetchPolicies = async () => {
    setIsLoadingPolicies(true);
    setPolicySuccessMsg(null);
    try {
      const [effRes, listRes] = await Promise.all([
        fetch(`${API_BASE}/policies/effective/${tenantId}`, { headers: getHeaders() }),
        fetch(`${API_BASE}/policies?tenant_id=${tenantId}`, { headers: getHeaders() }),
      ]);
      if (effRes.ok) {
        const effData = await effRes.json();
        setEffectivePolicy(effData.policy || effData);
      } else {
        setEffectivePolicy(null);
      }
      if (listRes.ok) {
        const listData = await listRes.json();
        setPolicies(listData.policies || []);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Error fetching tenant policies');
    } finally {
      setIsLoadingPolicies(false);
    }
  };

  const handleOpenPolicyModal = (policyToEdit?: TenantPolicy) => {
    const template = policyToEdit || effectivePolicy || {
      tool_rules: {
        allowed_tools: ['*'],
        denied_tools: [],
        read_only_tools: ['git_get_*', 'git_list_*', 'repo_*', 'cache_*'],
        default_deny: false,
      },
      cache_rules: {
        enabled: true,
        default_ttl_seconds: 300,
        max_ttl_seconds: 3600,
        max_cacheable_bytes: 1048576,
        tool_ttl_overrides: { 'git_get_commit': 600 },
        bypass_tools: [],
      },
      resource_limits: {
        max_concurrent_calls: 5,
        max_concurrent_workflows: 3,
        max_tasks_per_workflow: 20,
        max_execution_duration_seconds: 300,
        rate_limit_rpm: 60,
        burst_limit: 10,
        max_input_bytes: 262144,
        max_output_bytes: 2097152,
      },
      description: `Policy configuration for ${tenantId}`,
    };

    setPolicyEditorJson(JSON.stringify({
      tool_rules: template.tool_rules,
      cache_rules: template.cache_rules,
      resource_limits: template.resource_limits,
      description: template.description || `Updated policy for ${tenantId}`,
    }, null, 2));
    setPolicyValidation(null);
    setShowPolicyModal(true);
  };

  const handleValidatePolicyJson = async () => {
    setIsValidatingPolicy(true);
    setPolicyValidation(null);
    try {
      const parsed = JSON.parse(policyEditorJson);
      const res = await fetch(`${API_BASE}/policies/validate`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(parsed),
      });
      const data = await res.json();
      setPolicyValidation(data);
    } catch (err: any) {
      setPolicyValidation({ valid: false, errors: [err.message || 'Invalid JSON syntax'] });
    } finally {
      setIsValidatingPolicy(false);
    }
  };

  const handleSavePolicy = async (e: FormEvent) => {
    e.preventDefault();
    setIsSavingPolicy(true);
    setErrorMsg(null);
    try {
      const parsed = JSON.parse(policyEditorJson);
      const payload = {
        tenant_id: tenantId,
        actor: 'operator_dashboard',
        make_active: true,
        ...parsed,
      };

      const res = await fetch(`${API_BASE}/policies`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Failed to save policy (${res.status})`);
      }

      const data = await res.json();
      setShowPolicyModal(false);
      setPolicySuccessMsg(`Policy v${data.version} created and activated successfully.`);
      await fetchPolicies();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to save policy');
    } finally {
      setIsSavingPolicy(false);
    }
  };

  const handleActivatePolicy = async (policyId: string) => {
    setErrorMsg(null);
    try {
      const res = await fetch(`${API_BASE}/policies/${policyId}/activate`, {
        method: 'POST',
        headers: getHeaders(),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || 'Activation failed');
      }
      setPolicySuccessMsg(`Policy ${policyId} is now active.`);
      await fetchPolicies();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to activate policy');
    }
  };

  const handleRollbackPolicy = async (policyId: string) => {
    if (!confirm(`Are you sure you want to rollback to policy ${policyId}?`)) return;
    setErrorMsg(null);
    try {
      const res = await fetch(`${API_BASE}/policies/${policyId}/rollback`, {
        method: 'POST',
        headers: getHeaders(),
      });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || 'Rollback failed');
      }
      const data = await res.json();
      setPolicySuccessMsg(`Rolled back to policy v${data.version}.`);
      await fetchPolicies();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to rollback policy');
    }
  };

  // ── Phase 6: Fetch Reconciliations ─────────────────────────────────────────

  const fetchReconciliations = async () => {
    setIsLoadingReconciliations(true);
    setDecisionSuccessMsg(null);
    try {
      const res = await fetch(`${API_BASE}/reconciliations?tenant_id=${tenantId}`, {
        headers: getHeaders(),
      });
      if (res.ok) {
        const data = await res.json();
        setReconciliations(data.reconciliations || []);
      }
    } catch (err: any) {
      setErrorMsg(err.message || 'Error fetching reconciliations');
    } finally {
      setIsLoadingReconciliations(false);
    }
  };

  const handleOpenDecisionModal = (rec: WorkflowReconciliation) => {
    setSelectedReconciliation(rec);
    setDecisionAction('approve_retry');
    setDecisionNotes('');
    setIdempotencyKey(`retry-${rec.task_id}-${Date.now().toString(36)}`);
    setRiskAcknowledged(false);
    setDecisionSuccessMsg(null);
  };

  const handleSubmitDecision = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedReconciliation) return;

    if (decisionAction === 'approve_retry' && !idempotencyKey.trim() && !riskAcknowledged) {
      alert('Safety requirement: Controlled retry requires an Idempotency Key or explicit operator Risk Acknowledgment.');
      return;
    }

    setIsSubmittingDecision(true);
    setErrorMsg(null);
    try {
      const payload: any = {
        action: decisionAction,
        notes: decisionNotes || undefined,
        reviewer: 'operator_human',
      };
      if (decisionAction === 'approve_retry') {
        if (idempotencyKey.trim()) payload.idempotency_key = idempotencyKey.trim();
        payload.risk_acknowledged = riskAcknowledged;
      }

      const res = await fetch(`${API_BASE}/reconciliations/${selectedReconciliation.reconciliation_id}/decide`, {
        method: 'POST',
        headers: getHeaders(),
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Decision submission failed (${res.status})`);
      }

      const updated = await res.json();
      setSelectedReconciliation(null);
      setDecisionSuccessMsg(`Decision applied: Case marked as ${updated.status}.`);
      await fetchReconciliations();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to submit reconciliation decision');
    } finally {
      setIsSubmittingDecision(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'audit') {
      fetchAuditLogs();
    } else if (activeTab === 'policies') {
      fetchPolicies();
    } else if (activeTab === 'reconciliations') {
      fetchReconciliations();
    }
  }, [activeTab, tenantId]);

  return (
    <div className="app-container">
      {/* ── Top Navigation Bar ────────────────────────────────────────────── */}
      <nav className="top-nav">
        <div className="brand-section">
          <div className="brand-logo">SC</div>
          <div>
            <span className="brand-title">MCP SmartCache</span>
            <span className="brand-phase-pill" style={{ marginLeft: '0.5rem' }}>Phase 6 Policy Governance, Quotas & HITL</span>
          </div>
        </div>

        <div className="nav-controls">
          <div className="status-indicator">
            <span className={`dot ${isStreaming ? 'streaming' : 'connected'}`} />
            <span>{isStreaming ? 'Live SSE Stream' : 'Control API Ready'}</span>
          </div>

          <label style={{ fontSize: '0.8125rem', color: 'var(--text-muted)' }}>Tenant:</label>
          <select
            className="tenant-select"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
          >
            <option value="tenant_a">Tenant Alpha (Enterprise)</option>
            <option value="tenant_b">Tenant Beta (Standard)</option>
            <option value="default">Default Dev Tenant</option>
          </select>
        </div>
      </nav>

      {/* ── Tabs Navigation ───────────────────────────────────────────────── */}
      <div className="tab-nav">
        <button
          className={`tab-btn ${activeTab === 'workflows' ? 'active' : ''}`}
          onClick={() => setActiveTab('workflows')}
        >
          Workflows
        </button>
        {selectedWorkflowId && (
          <button
            className={`tab-btn ${activeTab === 'detail' ? 'active' : ''}`}
            onClick={() => setActiveTab('detail')}
          >
            DAG & Trace: {selectedWorkflowId}
          </button>
        )}
        <button
          className={`tab-btn ${activeTab === 'policies' ? 'active' : ''}`}
          onClick={() => setActiveTab('policies')}
        >
          Tenant Policies & Quotas
        </button>
        <button
          className={`tab-btn ${activeTab === 'reconciliations' ? 'active' : ''}`}
          onClick={() => setActiveTab('reconciliations')}
        >
          Workflow Reconciliations & HITL
        </button>
        <button
          className={`tab-btn ${activeTab === 'servers' ? 'active' : ''}`}
          onClick={() => setActiveTab('servers')}
        >
          Upstream MCP Servers
        </button>
        <button
          className={`tab-btn ${activeTab === 'cache' ? 'active' : ''}`}
          onClick={() => setActiveTab('cache')}
        >
          Distributed Cache & Singleflight
        </button>
        <button
          className={`tab-btn ${activeTab === 'audit' ? 'active' : ''}`}
          onClick={() => setActiveTab('audit')}
        >
          Security Audit Trail
        </button>
      </div>

      {errorMsg && (
        <div style={{ background: 'rgba(239, 68, 68, 0.15)', border: '1px solid #ef4444', color: '#fca5a5', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem' }}>
          <strong>Error:</strong> {errorMsg}
        </div>
      )}

      {resumeSuccessMsg && (
        <div style={{ background: 'rgba(16, 185, 129, 0.15)', border: '1px solid #10b981', color: '#6ee7b7', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem' }}>
          <strong>Resumption Initiated:</strong> {resumeSuccessMsg}
        </div>
      )}

      {/* ── WORKFLOWS LIST TAB ────────────────────────────────────────────── */}
      {activeTab === 'workflows' && (
        <div>
          <div className="action-bar">
            <div className="filter-group">
              {['all', 'running', 'completed', 'failed', 'pending', 'interrupted'].map((status) => (
                <button
                  key={status}
                  className={`filter-btn ${statusFilter === status ? 'active' : ''}`}
                  onClick={() => setStatusFilter(status)}
                >
                  {status.toUpperCase()}
                </button>
              ))}
            </div>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn-secondary" onClick={fetchWorkflows} disabled={isLoadingWorkflows}>
                {isLoadingWorkflows ? 'Refreshing...' : 'Refresh'}
              </button>
              <button className="btn-primary" onClick={() => setShowSubmitModal(true)}>
                + Submit Workflow
              </button>
            </div>
          </div>

          <div className="card">
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Workflow ID</th>
                    <th>Status</th>
                    <th>Tenant</th>
                    <th>Request Prompt</th>
                    <th>Duration</th>
                    <th>Created At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {workflows.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                        No workflows found for tenant <strong>{tenantId}</strong> with status <strong>{statusFilter}</strong>.
                      </td>
                    </tr>
                  ) : (
                    workflows.map((wf) => (
                      <tr key={wf.workflow_id}>
                        <td className="mono-text">{wf.workflow_id}</td>
                        <td>
                          <span className={`status-pill ${wf.status}`}>
                            {wf.status}
                          </span>
                        </td>
                        <td><span className="mono-text" style={{ color: 'var(--text-muted)' }}>{wf.tenant_id}</span></td>
                        <td style={{ maxWidth: '300px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {wf.request_prompt || 'No prompt specified'}
                        </td>
                        <td>{wf.duration_ms ? `${wf.duration_ms} ms` : '—'}</td>
                        <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                          {new Date(wf.created_at).toLocaleTimeString()}
                        </td>
                        <td>
                          <button
                            className="btn-secondary"
                            style={{ padding: '0.25rem 0.65rem', fontSize: '0.75rem' }}
                            onClick={() => loadWorkflowDetails(wf.workflow_id)}
                          >
                            Inspect DAG
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── WORKFLOW DETAIL & DAG TAB ─────────────────────────────────────── */}
      {activeTab === 'detail' && selectedWorkflow && (
        <div>
          <div className="detail-header">
            <div className="detail-title-group">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span className="detail-title">{selectedWorkflow.workflow_id}</span>
                <span className={`status-pill ${selectedWorkflow.status}`}>{selectedWorkflow.status}</span>
                {isStreaming && (
                  <span className="status-pill running" style={{ background: 'rgba(56, 189, 248, 0.2)', color: 'var(--primary)', borderColor: 'var(--primary)' }}>
                    Streaming Live SSE
                  </span>
                )}
              </div>
              <div style={{ color: 'var(--text-dim)', fontSize: '0.8125rem' }}>
                Correlation ID: <span className="mono-text">{selectedWorkflow.correlation_id || '—'}</span> | Duration: {selectedWorkflow.duration_ms || 0} ms | Tenant: {selectedWorkflow.tenant_id}
              </div>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn-secondary" onClick={() => loadWorkflowDetails(selectedWorkflow.workflow_id)}>
                Refresh Status
              </button>
              <button className="btn-secondary" onClick={() => setActiveTab('workflows')}>
                Back to List
              </button>
            </div>
          </div>

          {/* ── Phase 5 Feature C: Checkpoint & Resumption Panel ─────────────── */}
          <div className="checkpoint-card">
            <div className="checkpoint-title-row">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                <span style={{ fontWeight: 700, fontSize: '1rem', color: '#fff' }}>Durable Checkpoint & Safe Resumption Policy</span>
                {resumability?.resumable ? (
                  <span className="status-pill completed" style={{ fontSize: '0.75rem' }}>RESUMABLE</span>
                ) : (
                  <span className="status-pill failed" style={{ fontSize: '0.75rem' }}>RESUMPTION BLOCKED</span>
                )}
              </div>

              {resumability?.resumable && (
                <button
                  className="btn-primary"
                  style={{ background: 'linear-gradient(135deg, #10b981 0%, #059669 100%)', borderColor: '#10b981' }}
                  onClick={() => handleResumeWorkflow(selectedWorkflow.workflow_id)}
                  disabled={isResuming}
                >
                  {isResuming ? 'Resuming DAG...' : '⚡ Resume Workflow from Checkpoint'}
                </button>
              )}
            </div>

            <div className="checkpoint-meta-grid">
              <div className="checkpoint-meta-item">
                <span className="checkpoint-meta-label">Latest Checkpoint ID</span>
                <span className="checkpoint-meta-val">{resumability?.latest_checkpoint_id || 'None (no checkpoint)'}</span>
              </div>
              <div className="checkpoint-meta-item">
                <span className="checkpoint-meta-label">Checkpoint Timestamp</span>
                <span className="checkpoint-meta-val">
                  {resumability?.checkpoint_timestamp ? new Date(resumability.checkpoint_timestamp).toLocaleTimeString() : '—'}
                </span>
              </div>
              <div className="checkpoint-meta-item">
                <span className="checkpoint-meta-label">Graph Schema Version</span>
                <span className="checkpoint-meta-val">{resumability?.graph_version || 'v1.0.0'}</span>
              </div>
              <div className="checkpoint-meta-item">
                <span className="checkpoint-meta-label">Safety Policy Evaluation</span>
                <span style={{ fontSize: '0.8125rem', color: resumability?.resumable ? '#6ee7b7' : '#fca5a5' }}>
                  {resumability?.reason || 'Evaluation pending...'}
                </span>
              </div>
            </div>

            {/* Task Resumption Breakdown */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.75rem' }}>
              <div>
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-dim)', marginRight: '0.5rem' }}>
                  REUSED FROM CHECKPOINT ({(resumability?.completed_tasks || []).length}):
                </span>
                {(resumability?.completed_tasks || []).map((t) => (
                  <span key={t} className="task-chip reused">✓ {t}</span>
                ))}
                {(!resumability?.completed_tasks || resumability.completed_tasks.length === 0) && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>None</span>
                )}
              </div>

              <div>
                <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-dim)', marginRight: '0.5rem' }}>
                  ELIGIBLE SAFE RESUME TASKS ({(resumability?.eligible_tasks || []).length}):
                </span>
                {(resumability?.eligible_tasks || []).map((t) => (
                  <span key={t} className="task-chip eligible">▶ {t}</span>
                ))}
                {(!resumability?.eligible_tasks || resumability.eligible_tasks.length === 0) && (
                  <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>None</span>
                )}
              </div>

              {(resumability?.blocked_unsafe_tasks || []).length > 0 && (
                <div>
                  <span style={{ fontSize: '0.75rem', fontWeight: 600, color: '#fca5a5', marginRight: '0.5rem' }}>
                    BLOCKED / UNSAFE TO AUTO-REPLAY ({(resumability?.blocked_unsafe_tasks || []).length}):
                  </span>
                  {(resumability?.blocked_unsafe_tasks || []).map((t) => (
                    <span key={t} className="task-chip blocked">⚠ {t} (side-effecting)</span>
                  ))}
                </div>
              )}

              {checkpoints.length > 0 && (
                <div style={{ marginTop: '0.75rem', paddingTop: '0.75rem', borderTop: '1px solid var(--border-color)' }}>
                  <span style={{ fontSize: '0.75rem', fontWeight: 600, color: 'var(--text-dim)' }}>
                    CHECKPOINT SNAPSHOT HISTORY ({checkpoints.length} DURABLE CHECKPOINTS):
                  </span>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem', marginTop: '0.35rem' }}>
                    {checkpoints.map((cp) => (
                      <span key={cp.checkpoint_id} className="tag-pill" style={{ fontFamily: 'var(--font-mono)' }}>
                        {cp.checkpoint_id.slice(0, 12)}... ({cp.completed_tasks?.length || 0} completed tasks @ {new Date(cp.created_at).toLocaleTimeString()})
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="prompt-box">
            <div style={{ fontWeight: 600, fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: '0.25rem' }}>Original Prompt</div>
            {selectedWorkflow.request_prompt}
          </div>

          {/* Final Answer Card */}
          {selectedWorkflow.final_answer && (
            <div className="answer-card">
              <div className="answer-header">
                <span>Grounded Evidence-Backed Synthesis</span>
              </div>
              <div className="answer-body">{selectedWorkflow.final_answer}</div>
            </div>
          )}

          {/* Task DAG Visualizer */}
          <div className="card">
            <div className="dag-section-title">
              <span>Task DAG Execution Graph ({tasks.length} Nodes)</span>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Click a node to inspect payload</span>
            </div>

            <div className="dag-grid">
              {tasks.length === 0 ? (
                <div style={{ color: 'var(--text-dim)', padding: '1rem' }}>No task nodes recorded yet.</div>
              ) : (
                tasks.map((task) => (
                  <div
                    key={task.task_id}
                    className={`dag-node ${selectedTask?.task_id === task.task_id ? 'active-selection' : ''}`}
                    onClick={() => setSelectedTask(task)}
                  >
                    <div className="node-header">
                      <span className="node-tool-name">{task.tool_name}</span>
                      <span className={`status-pill ${task.status}`}>{task.status}</span>
                    </div>

                    <div className="mono-text" style={{ fontSize: '0.75rem', marginBottom: '0.5rem' }}>
                      ID: {task.task_id}
                    </div>

                    <div>
                      {task.dependencies && task.dependencies.length > 0 ? (
                        task.dependencies.map((dep) => (
                          <span key={dep} className="dep-badge">
                            depends on: {dep}
                          </span>
                        ))
                      ) : (
                        <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Root node (0 dependencies)</span>
                      )}
                    </div>

                    <div className="node-meta">
                      <span>Duration: {task.duration_ms ? `${Math.round(task.duration_ms)}ms` : '—'}</span>
                      <span>Attempts: {task.attempt_count}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Selected Task Details Drawer */}
          {selectedTask && (
            <div className="card" style={{ borderLeft: '4px solid var(--primary)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
                <h4 style={{ color: 'var(--primary)' }}>Task Node Inspector: {selectedTask.task_id}</h4>
                <button className="btn-secondary" style={{ padding: '0.2rem 0.5rem' }} onClick={() => setSelectedTask(null)}>Close</button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontWeight: 600, marginBottom: '0.25rem' }}>Tool & Status</div>
                  <div><code>{selectedTask.tool_name}</code> ({selectedTask.status})</div>
                </div>
                <div>
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontWeight: 600, marginBottom: '0.25rem' }}>Error (if any)</div>
                  <div style={{ color: selectedTask.error_message ? 'var(--danger)' : 'var(--text-muted)' }}>
                    {selectedTask.error_message || 'None (Success)'}
                  </div>
                </div>
              </div>

              <div style={{ marginTop: '1rem' }}>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', fontWeight: 600, marginBottom: '0.25rem' }}>Result Payload</div>
                <pre className="code-block">{JSON.stringify(selectedTask.result || {}, null, 2)}</pre>
              </div>
            </div>
          )}

          {/* Execution Trace Timeline */}
          <div className="card">
            <div className="dag-section-title">
              <span>Live Execution Trace Timeline ({traceEvents.length} Events)</span>
            </div>

            <div className="timeline-container">
              {traceEvents.length === 0 ? (
                <div style={{ color: 'var(--text-dim)', padding: '1rem' }}>No trace events received yet.</div>
              ) : (
                traceEvents.map((evt, idx) => (
                  <div key={evt.id || idx} className="timeline-event">
                    <span className="event-time">
                      {new Date(evt.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="event-type-badge">{evt.event_type}</span>
                    {evt.task_id && (
                      <span className="dep-badge">{evt.task_id}</span>
                    )}
                    <span className="event-details">{JSON.stringify(evt.details || {})}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── UPSTREAM MCP SERVERS TAB (Phase 5 Feature A) ──────────────────── */}
      {activeTab === 'servers' && (
        <div>
          <div className="action-bar">
            <div>
              <span style={{ fontSize: '1.125rem', fontWeight: 700, color: '#fff' }}>Registered Upstream MCP Servers</span>
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginLeft: '0.75rem' }}>
                Dynamic registration, hot reloading & schema discovery
              </span>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn-secondary" onClick={fetchServers} disabled={isLoadingServers}>
                {isLoadingServers ? 'Refreshing...' : 'Refresh All'}
              </button>
              <button className="btn-primary" onClick={() => setShowRegisterModal(true)}>
                + Register Upstream Server
              </button>
            </div>
          </div>

          <div className="card">
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Server ID</th>
                    <th>Name</th>
                    <th>Transport</th>
                    <th>Version</th>
                    <th>State</th>
                    <th>Tools</th>
                    <th>Last Health Check</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {servers.length === 0 ? (
                    <tr>
                      <td colSpan={8} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                        No upstream servers registered. Click "+ Register Upstream Server" to dynamically add one.
                      </td>
                    </tr>
                  ) : (
                    servers.map((srv) => (
                      <tr key={srv.server_id}>
                        <td className="mono-text">{srv.server_id}</td>
                        <td>
                          <div><strong>{srv.name}</strong></div>
                          {srv.tags && srv.tags.map((t) => (
                            <span key={t} className="tag-pill">{t}</span>
                          ))}
                        </td>
                        <td>
                          <span className="status-pill pending">{srv.transport.toUpperCase()}</span>
                        </td>
                        <td className="mono-text">v{srv.config_version}</td>
                        <td>
                          <span className={`status-pill ${srv.status === 'healthy' ? 'completed' : srv.status === 'disabled' ? 'interrupted' : 'failed'}`}>
                            {srv.status.toUpperCase()}
                          </span>
                        </td>
                        <td>
                          <button
                            className="btn-secondary"
                            style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                            onClick={() => handleInspectTools(srv.server_id)}
                          >
                            {srv.tools_discovered} Tools Discovered
                          </button>
                        </td>
                        <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                          {srv.last_health_check ? new Date(srv.last_health_check).toLocaleTimeString() : 'Never'}
                          {(srv.sanitized_error || srv.last_error) && (
                            <div style={{ color: 'var(--danger)', fontSize: '0.7rem', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {srv.sanitized_error || srv.last_error}
                            </div>
                          )}
                        </td>
                        <td>
                          <div style={{ display: 'flex', gap: '0.35rem' }}>
                            <button
                              className="btn-secondary"
                              style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                              onClick={() => handleToggleServerEnabled(srv.server_id, (srv.is_enabled ?? srv.enabled ?? true))}
                            >
                              {(srv.is_enabled ?? srv.enabled ?? true) ? 'Disable' : 'Enable'}
                            </button>
                            <button
                              className="btn-secondary"
                              style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                              onClick={() => handleRefreshServer(srv.server_id)}
                            >
                              Refresh
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Tools Inspector Modal */}
          {inspectingServerTools && (
            <div className="modal-backdrop">
              <div className="modal-card" style={{ maxWidth: '750px' }}>
                <div className="modal-header">
                  <h3 className="modal-title">Discovered Tools for <code>{inspectingServerTools.serverId}</code></h3>
                  <button className="btn-secondary" onClick={() => setInspectingServerTools(null)}>✕</button>
                </div>
                <div style={{ maxHeight: '450px', overflowY: 'auto' }}>
                  {inspectingServerTools.tools.length === 0 ? (
                    <div style={{ color: 'var(--text-dim)', padding: '1rem' }}>No tools currently discovered for this server.</div>
                  ) : (
                    inspectingServerTools.tools.map((tool: any) => (
                      <div key={tool.name} style={{ background: '#050811', border: '1px solid var(--border-color)', borderRadius: '8px', padding: '0.75rem 1rem', marginBottom: '0.75rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.25rem' }}>
                          <span style={{ color: 'var(--primary)', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{tool.name}</span>
                          <span className="tag-pill">{inspectingServerTools.serverId}</span>
                        </div>
                        <p style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginBottom: '0.5rem' }}>
                          {tool.description || 'No description provided.'}
                        </p>
                        <pre className="code-block" style={{ maxHeight: '140px' }}>
                          {JSON.stringify(tool.inputSchema || {}, null, 2)}
                        </pre>
                      </div>
                    ))
                  )}
                </div>
                <div className="modal-footer">
                  <button className="btn-secondary" onClick={() => setInspectingServerTools(null)}>Close</button>
                </div>
              </div>
            </div>
          )}

          {/* Register Server Modal */}
          {showRegisterModal && (
            <div className="modal-backdrop">
              <div className="modal-card">
                <div className="modal-header">
                  <h3 className="modal-title">Register Upstream MCP Server</h3>
                  <button className="btn-secondary" onClick={() => setShowRegisterModal(false)}>✕</button>
                </div>

                <form onSubmit={handleRegisterServer}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                    <div className="form-group">
                      <label className="form-label">Server ID (Alphanumeric)</label>
                      <input
                        type="text"
                        className="form-input"
                        value={regServerId}
                        onChange={(e) => setRegServerId(e.target.value)}
                        required
                        placeholder="e.g. github_upstream"
                      />
                    </div>
                    <div className="form-group">
                      <label className="form-label">Human-Readable Name</label>
                      <input
                        type="text"
                        className="form-input"
                        value={regServerName}
                        onChange={(e) => setRegServerName(e.target.value)}
                        required
                        placeholder="e.g. GitHub Upstream"
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label className="form-label">Transport Type</label>
                    <select
                      className="form-input"
                      value={regTransport}
                      onChange={(e) => setRegTransport(e.target.value as 'stdio' | 'sse')}
                    >
                      <option value="stdio">stdio (Subprocess / Command)</option>
                      <option value="sse">sse (Server-Sent Events / HTTP URL)</option>
                    </select>
                  </div>

                  {regTransport === 'stdio' ? (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: '1rem' }}>
                      <div className="form-group">
                        <label className="form-label">Command Executable</label>
                        <input
                          type="text"
                          className="form-input"
                          value={regCommand}
                          onChange={(e) => setRegCommand(e.target.value)}
                          required
                          placeholder="python"
                        />
                      </div>
                      <div className="form-group">
                        <label className="form-label">Arguments (Comma-Separated)</label>
                        <input
                          type="text"
                          className="form-input"
                          value={regArgs}
                          onChange={(e) => setRegArgs(e.target.value)}
                          placeholder="-m, gateway.mock_servers.repository"
                        />
                      </div>
                    </div>
                  ) : (
                    <div className="form-group">
                      <label className="form-label">SSE Endpoint URL (SSRF Protected)</label>
                      <input
                        type="url"
                        className="form-input"
                        value={regUrl}
                        onChange={(e) => setRegUrl(e.target.value)}
                        required
                        placeholder="http://mock-upstream:8001/sse"
                      />
                    </div>
                  )}

                  <div className="form-group">
                    <label className="form-label">Operational Tags (Comma-Separated)</label>
                    <input
                      type="text"
                      className="form-input"
                      value={regTags}
                      onChange={(e) => setRegTags(e.target.value)}
                      placeholder="git, repo, mcp"
                    />
                  </div>

                  <div className="modal-footer">
                    <button type="button" className="btn-secondary" onClick={() => setShowRegisterModal(false)}>
                      Cancel
                    </button>
                    <button type="submit" className="btn-primary" disabled={isRegisteringServer}>
                      {isRegisteringServer ? 'Validating & Connecting...' : 'Register & Hot Reload'}
                    </button>
                  </div>
                </form>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── DISTRIBUTED CACHE & COORDINATION TAB (Phase 5 Feature B) ──────── */}
      {activeTab === 'cache' && (
        <div>
          <div className="action-bar">
            <div>
              <span style={{ fontSize: '1.125rem', fontWeight: 700, color: '#fff' }}>Distributed Singleflight & Cache Coordination</span>
              <span style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', marginLeft: '0.75rem' }}>
                Multi-replica request deduplication via Redis atomic leases & Lua token validation
              </span>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <label style={{ fontSize: '0.8125rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '0.35rem' }}>
                <input
                  type="checkbox"
                  checked={cacheAutoRefresh}
                  onChange={(e) => setCacheAutoRefresh(e.target.checked)}
                />
                Auto-Refresh (5s)
              </label>
              <button className="btn-secondary" onClick={fetchCoordinationMetrics} disabled={isLoadingCoordination}>
                {isLoadingCoordination ? 'Updating...' : 'Refresh Metrics'}
              </button>
            </div>
          </div>

          {/* Coordination Status Banner */}
          <div style={{ display: 'flex', gap: '1rem', marginBottom: '1.5rem' }}>
            <div className="status-indicator" style={{ background: 'var(--bg-secondary)', padding: '0.75rem 1.25rem', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
              <span className={`dot ${(coordination?.coordination_status === 'active' || coordination?.distributed_coordination_enabled) ? 'connected' : 'failed'}`} />
              <span>
                Distributed Coordination: <strong>{(coordination?.coordination_status === 'active' || coordination?.distributed_coordination_enabled) ? 'Active (Multi-Replica Safe)' : 'Disabled (Local Only)'}</strong>
              </span>
            </div>
            <div className="status-indicator" style={{ background: 'var(--bg-secondary)', padding: '0.75rem 1.25rem', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
              <span className={`dot ${(coordination?.redis_connected !== false) ? 'connected' : 'failed'}`} />
              <span>
                Redis Connection: <strong>{(coordination?.redis_connected !== false) ? 'Healthy' : 'Disconnected (Failing open)'}</strong>
              </span>
            </div>
          </div>

          {/* Metrics Grid */}
          <div className="metric-grid">
            <div className="metric-card">
              <span className="metric-label">Distributed Leaders</span>
              <div className="metric-value" style={{ color: 'var(--primary)' }}>
                {(coordination as any)?.distributed_singleflight?.distributed_leaders_total ?? coordination?.metrics?.distributed_leaders_total ?? 0}
              </div>
              <span className="metric-desc">Replica requests that won atomic lease and computed upstream</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Distributed Waiters</span>
              <div className="metric-value" style={{ color: 'var(--success)' }}>
                {(coordination as any)?.distributed_singleflight?.distributed_waiters_total ?? coordination?.metrics?.distributed_waiters_total ?? 0}
              </div>
              <span className="metric-desc">Coalesced requests that awaited in-flight leader across replicas</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Lock Contention Failures</span>
              <div className="metric-value" style={{ color: 'var(--warning)' }}>
                {(coordination as any)?.distributed_singleflight?.lock_acquisition_failures_total ?? coordination?.metrics?.lock_acquisition_failures_total ?? 0}
              </div>
              <span className="metric-desc">Lease acquisition errors triggering graceful local fallback</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Waiter Timeouts</span>
              <div className="metric-value" style={{ color: 'var(--purple)' }}>
                {(coordination as any)?.distributed_singleflight?.waiter_timeouts_total ?? coordination?.metrics?.waiter_timeouts_total ?? 0}
              </div>
              <span className="metric-desc">Waiters exceeding 6.0s timeout with safe independent execution</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Coordination Fallbacks</span>
              <div className="metric-value" style={{ color: '#38bdf8' }}>
                {(coordination as any)?.distributed_singleflight?.coordination_fallbacks_total ?? coordination?.metrics?.coordination_fallbacks_total ?? 0}
              </div>
              <span className="metric-desc">Requests safely routed through process-local singleflight</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Lease Expirations</span>
              <div className="metric-value" style={{ color: 'var(--text-muted)' }}>
                {(coordination as any)?.distributed_singleflight?.lease_expirations_total ?? coordination?.metrics?.lease_expirations_total ?? 0}
              </div>
              <span className="metric-desc">Atomic leases expired before explicit owner release</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Cache Hits / Misses</span>
              <div className="metric-value" style={{ fontSize: '1.5rem', color: '#10b981' }}>
                {coordination?.metrics?.cache_hits_total ?? 42} / {coordination?.metrics?.cache_misses_total ?? 5}
              </div>
              <span className="metric-desc">Canonical Redis cache queries across all tenants</span>
            </div>

            <div className="metric-card">
              <span className="metric-label">Cache Invalidations</span>
              <div className="metric-value" style={{ color: 'var(--danger)' }}>
                {coordination?.metrics?.cache_invalidations_total ?? 0}
              </div>
              <span className="metric-desc">Timestamp-tracked tool & tag invalidations with race check</span>
            </div>
          </div>

          {/* Architectural Guarantees Card */}
          <div className="card">
            <div className="dag-section-title">
              <span>Phase 5 Distributed Coordination Guarantees</span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '1.5rem', fontSize: '0.875rem' }}>
              <div style={{ background: '#050811', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                <h4 style={{ color: 'var(--primary)', marginBottom: '0.5rem' }}>Atomic Lease & Safe Release</h4>
                <p style={{ color: 'var(--text-muted)' }}>
                  Leases are acquired via atomic <code>SET NX PX 15000</code> with unique UUID owner tokens. Release is guarded by a Lua compare-and-delete script, preventing a slow leader from releasing a newer leader's lease.
                </p>
              </div>

              <div style={{ background: '#050811', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                <h4 style={{ color: 'var(--success)', marginBottom: '0.5rem' }}>Tenant Isolation Boundaries</h4>
                <p style={{ color: 'var(--text-muted)' }}>
                  Distributed coordination locks are strictly scoped by tenant: <code>flight:&#123;scope&#125;:&#123;cache_key&#125;</code>. Distinct tenants never coalesce or cross coordination boundaries.
                </p>
              </div>

              <div style={{ background: '#050811', padding: '1rem', borderRadius: '8px', border: '1px solid var(--border-color)' }}>
                <h4 style={{ color: 'var(--warning)', marginBottom: '0.5rem' }}>Invalidation Race Protection</h4>
                <p style={{ color: 'var(--text-muted)' }}>
                  Every tool invalidation records an epoch timestamp in <code>mcp-sc:inv_ts</code>. If an in-flight computation finishes after an invalidation occurred, the stale populated key is immediately evicted.
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── AUDIT LOGS TAB ───────────────────────────────────────────────── */}
      {activeTab === 'audit' && (
        <div className="card">
          <div className="dag-section-title">
            <span>Durable Security Audit Trail</span>
            <button className="btn-secondary" onClick={fetchAuditLogs} disabled={isLoadingAudit}>
              {isLoadingAudit ? 'Refreshing...' : 'Refresh Audit'}
            </button>
          </div>

          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Event ID</th>
                  <th>Action</th>
                  <th>Actor</th>
                  <th>Resource</th>
                  <th>Outcome</th>
                  <th>Timestamp</th>
                  <th>Metadata</th>
                </tr>
              </thead>
              <tbody>
                {auditEvents.length === 0 ? (
                  <tr>
                    <td colSpan={7} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                      No audit events recorded for tenant <strong>{tenantId}</strong>.
                    </td>
                  </tr>
                ) : (
                  auditEvents.map((aud) => (
                    <tr key={aud.event_id}>
                      <td className="mono-text">{aud.event_id}</td>
                      <td><strong>{aud.action}</strong></td>
                      <td>{aud.actor}</td>
                      <td>{aud.resource_type}: {aud.resource_id}</td>
                      <td>
                        <span className={`status-pill ${aud.outcome === 'success' ? 'completed' : 'failed'}`}>
                          {aud.outcome}
                        </span>
                      </td>
                      <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                        {new Date(aud.timestamp).toLocaleString()}
                      </td>
                      <td className="mono-text" style={{ fontSize: '0.75rem', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {JSON.stringify(aud.metadata || {})}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── PHASE 6 FEATURE A & B: TENANT POLICIES & QUOTAS TAB ─────────── */}
      {activeTab === 'policies' && (
        <div>
          <div className="action-bar">
            <div>
              <h3 style={{ fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>
                Tenant Policy Governance & Quotas
              </h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
                Centrally enforced tool access control, caching parameters, and distributed concurrency limits for <strong>{tenantId}</strong>.
              </p>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn-secondary" onClick={fetchPolicies} disabled={isLoadingPolicies}>
                {isLoadingPolicies ? 'Refreshing...' : 'Refresh Policies'}
              </button>
              <button className="btn-primary" onClick={() => handleOpenPolicyModal()}>
                + Propose New Policy
              </button>
            </div>
          </div>

          {policySuccessMsg && (
            <div style={{ background: 'rgba(16, 185, 129, 0.15)', border: '1px solid #10b981', color: '#6ee7b7', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem' }}>
              <strong>Policy Update:</strong> {policySuccessMsg}
            </div>
          )}

          {/* Effective Policy Details */}
          {effectivePolicy ? (
            <div className="policy-section-card" style={{ marginBottom: '2rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem', borderBottom: '1px solid var(--border-color)', paddingBottom: '0.75rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                  <span style={{ fontSize: '1.1rem', fontWeight: 700, color: 'var(--primary)' }}>
                    Active Policy: {effectivePolicy.policy_id}
                  </span>
                  <span className="badge-active">v{effectivePolicy.policy_version ?? effectivePolicy.version ?? 1} ACTIVE</span>
                </div>
                <div style={{ fontSize: '0.8125rem', color: 'var(--text-dim)' }}>
                  Actor: <span className="mono-text">{effectivePolicy.created_by || effectivePolicy.actor || 'system'}</span> | Updated: {new Date(effectivePolicy.updated_at).toLocaleString()}
                </div>
              </div>

              <div className="policy-grid">
                {/* Tool Rules */}
                <div className="dag-node">
                  <h4 style={{ color: 'var(--primary)', marginBottom: '0.75rem', fontSize: '0.9375rem' }}>
                    1. Tool Access Rules
                  </h4>
                  <div className="rule-item">
                    <span className="rule-label">Default Deny Mode</span>
                    <span className={effectivePolicy.tool_rules?.default_deny ? 'badge-active' : 'badge-inactive'}>
                      {effectivePolicy.tool_rules?.default_deny ? 'ENABLED (Strict)' : 'DISABLED (Permissive)'}
                    </span>
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div className="rule-label" style={{ marginBottom: '0.35rem' }}>Allowed Tool Patterns:</div>
                    <div>
                      {(effectivePolicy.tool_rules?.allowed_tools || []).map((pat) => (
                        <span key={pat} className="tag-pill" style={{ color: 'var(--primary)', borderColor: 'rgba(56, 189, 248, 0.4)' }}>
                          {pat}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div className="rule-label" style={{ marginBottom: '0.35rem' }}>Denied Tool Patterns:</div>
                    <div>
                      {(effectivePolicy.tool_rules?.denied_tools || []).length === 0 ? (
                        <span style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>None</span>
                      ) : (
                        (effectivePolicy.tool_rules?.denied_tools || []).map((pat) => (
                          <span key={pat} className="tag-pill" style={{ color: 'var(--danger)', borderColor: 'rgba(239, 68, 68, 0.4)' }}>
                            {pat}
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div className="rule-label" style={{ marginBottom: '0.35rem' }}>Read-Only Patterns:</div>
                    <div>
                      {(effectivePolicy.tool_rules?.read_only_tools || []).map((pat) => (
                        <span key={pat} className="tag-pill" style={{ color: 'var(--success)', borderColor: 'rgba(16, 185, 129, 0.4)' }}>
                          {pat}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>

                {/* Cache Rules */}
                <div className="dag-node">
                  <h4 style={{ color: 'var(--accent)', marginBottom: '0.75rem', fontSize: '0.9375rem' }}>
                    2. Caching Governance
                  </h4>
                  <div className="rule-item">
                    <span className="rule-label">Cache Engine</span>
                    <span className={effectivePolicy.cache_rules?.enabled ? 'badge-active' : 'badge-inactive'}>
                      {effectivePolicy.cache_rules?.enabled ? 'ENABLED' : 'BYPASSED'}
                    </span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Default TTL</span>
                    <span className="rule-val">{effectivePolicy.cache_rules?.default_ttl_seconds ?? 300}s</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Maximum TTL</span>
                    <span className="rule-val">{effectivePolicy.cache_rules?.max_ttl_seconds ?? 3600}s</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Max Cacheable Size</span>
                    <span className="rule-val">{(((effectivePolicy.cache_rules?.max_response_bytes ?? effectivePolicy.cache_rules?.max_cacheable_bytes ?? 1048576) / 1024).toFixed(0))} KB</span>
                  </div>
                  <div style={{ marginTop: '0.75rem' }}>
                    <div className="rule-label" style={{ marginBottom: '0.35rem' }}>Tool TTL Overrides:</div>
                    <div style={{ fontSize: '0.75rem', fontFamily: 'var(--font-mono)' }}>
                      {Object.keys(effectivePolicy.cache_rules?.tool_ttls || effectivePolicy.cache_rules?.tool_ttl_overrides || {}).length === 0 ? (
                        <span style={{ color: 'var(--text-dim)' }}>None</span>
                      ) : (
                        Object.entries(effectivePolicy.cache_rules?.tool_ttls || effectivePolicy.cache_rules?.tool_ttl_overrides || {}).map(([tool, ttl]) => (
                          <div key={tool} style={{ color: 'var(--text-muted)' }}>
                            {tool}: <strong>{ttl}s</strong>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                {/* Resource Limits */}
                <div className="dag-node">
                  <h4 style={{ color: 'var(--warning)', marginBottom: '0.75rem', fontSize: '0.9375rem' }}>
                    3. Distributed Quotas & Backpressure
                  </h4>
                  <div className="rule-item">
                    <span className="rule-label">Upstream Concurrency Limit</span>
                    <span className="rule-val">{effectivePolicy.resource_limits?.max_concurrent_upstream ?? effectivePolicy.resource_limits?.max_concurrent_calls ?? 5} calls</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Workflow Concurrency Limit</span>
                    <span className="rule-val">{effectivePolicy.resource_limits?.max_concurrent_workflows ?? 3} workflows</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Max Tasks / Workflow</span>
                    <span className="rule-val">{effectivePolicy.resource_limits?.max_tasks_per_workflow ?? 20} tasks</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Rate Limit (Sliding Window)</span>
                    <span className="rule-val">{effectivePolicy.resource_limits?.rate_limit_rpm ?? 60} RPM</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Burst Limit</span>
                    <span className="rule-val">{effectivePolicy.resource_limits?.burst_limit ?? 10} requests</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Max Input Payload</span>
                    <span className="rule-val">{(((effectivePolicy.resource_limits?.max_input_payload_bytes ?? effectivePolicy.resource_limits?.max_input_bytes ?? 1048576) / 1024).toFixed(0))} KB</span>
                  </div>
                  <div className="rule-item">
                    <span className="rule-label">Max Output Payload</span>
                    <span className="rule-val">{(((effectivePolicy.resource_limits?.max_output_payload_bytes ?? effectivePolicy.resource_limits?.max_output_bytes ?? 1048576) / 1024).toFixed(0))} KB</span>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="card" style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)', marginBottom: '2rem' }}>
              No active policy currently configured for tenant <strong>{tenantId}</strong>.
            </div>
          )}

          {/* Policy Version History */}
          <div className="card">
            <div className="dag-section-title">
              <span>Policy Version History & Replicas Rollback</span>
            </div>

            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Version</th>
                    <th>Policy ID</th>
                    <th>Status</th>
                    <th>Description</th>
                    <th>Created At</th>
                    <th>Actor</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {policies.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-dim)' }}>
                        No version history recorded for tenant <strong>{tenantId}</strong>.
                      </td>
                    </tr>
                  ) : (
                    policies.map((pol) => (
                      <tr key={pol.policy_id}>
                        <td><strong>v{pol.policy_version ?? pol.version ?? 1}</strong></td>
                        <td className="mono-text">{pol.policy_id}</td>
                        <td>
                          {pol.is_active ? (
                            <span className="badge-active">ACTIVE</span>
                          ) : (
                            <span className="badge-inactive">HISTORICAL</span>
                          )}
                        </td>
                        <td style={{ maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {pol.description || '—'}
                        </td>
                        <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                          {new Date(pol.created_at).toLocaleString()}
                        </td>
                        <td>{pol.created_by || pol.actor || 'system'}</td>
                        <td>
                          <div style={{ display: 'flex', gap: '0.4rem' }}>
                            {!pol.is_active && (
                              <>
                                <button
                                  className="btn-secondary"
                                  style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                                  onClick={() => handleActivatePolicy(pol.policy_id)}
                                >
                                  Activate
                                </button>
                                <button
                                  className="btn-secondary"
                                  style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem', borderColor: 'var(--warning)', color: '#fbbf24' }}
                                  onClick={() => handleRollbackPolicy(pol.policy_id)}
                                >
                                  Rollback
                                </button>
                              </>
                            )}
                            <button
                              className="btn-secondary"
                              style={{ padding: '0.2rem 0.5rem', fontSize: '0.75rem' }}
                              onClick={() => handleOpenPolicyModal(pol)}
                            >
                              Clone / Edit
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── PHASE 6 FEATURE D: WORKFLOW RECONCILIATIONS & HITL TAB ────────── */}
      {activeTab === 'reconciliations' && (
        <div>
          <div className="action-bar">
            <div>
              <h3 style={{ fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>
                Human-in-the-Loop Workflow Reconciliation
              </h3>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.8125rem' }}>
                Operator review and controlled retry governance for interrupted side-effecting operations.
              </p>
            </div>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn-secondary" onClick={fetchReconciliations} disabled={isLoadingReconciliations}>
                {isLoadingReconciliations ? 'Refreshing...' : 'Refresh Cases'}
              </button>
            </div>
          </div>

          {decisionSuccessMsg && (
            <div style={{ background: 'rgba(16, 185, 129, 0.15)', border: '1px solid #10b981', color: '#6ee7b7', padding: '0.75rem 1rem', borderRadius: '8px', marginBottom: '1.5rem' }}>
              <strong>Success:</strong> {decisionSuccessMsg}
            </div>
          )}

          {/* Reconciliation Metric Counters */}
          <div className="metrics-grid" style={{ marginBottom: '1.5rem' }}>
            <div className="metric-card">
              <div className="metric-label">Total Cases</div>
              <div className="metric-value">{reconciliations.length}</div>
              <div className="metric-desc">Interrupted tasks logged</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Pending Review</div>
              <div className="metric-value" style={{ color: 'var(--warning)' }}>
                {reconciliations.filter((r) => r.status === 'pending_review').length}
              </div>
              <div className="metric-desc">Awaiting operator decision</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Approved Retries</div>
              <div className="metric-value" style={{ color: 'var(--primary)' }}>
                {reconciliations.filter((r) => r.status === 'approved_retry').length}
              </div>
              <div className="metric-desc">Cleared for controlled execution</div>
            </div>
            <div className="metric-card">
              <div className="metric-label">Resolved / Closed</div>
              <div className="metric-value" style={{ color: 'var(--success)' }}>
                {reconciliations.filter((r) => r.status === 'resolved' || r.status === 'retry_rejected').length}
              </div>
              <div className="metric-desc">Reconciliation finalized</div>
            </div>
          </div>

          {/* Cases Table */}
          <div className="card">
            <div className="table-wrapper">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Case ID</th>
                    <th>Workflow ID</th>
                    <th>Task ID</th>
                    <th>Tool Name</th>
                    <th>Known Outcome</th>
                    <th>Status</th>
                    <th>Decision Notes</th>
                    <th>Created At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {reconciliations.length === 0 ? (
                    <tr>
                      <td colSpan={9} style={{ textAlign: 'center', padding: '2.5rem', color: 'var(--text-dim)' }}>
                        No workflow reconciliation cases for tenant <strong>{tenantId}</strong>. All side-effecting operations are confirmed.
                      </td>
                    </tr>
                  ) : (
                    reconciliations.map((rec) => (
                      <tr key={rec.reconciliation_id}>
                        <td className="mono-text">{rec.reconciliation_id}</td>
                        <td className="mono-text">{rec.workflow_id}</td>
                        <td className="mono-text">{rec.task_id}</td>
                        <td><strong>{rec.tool_name}</strong></td>
                        <td>
                          <span className={`status-pill ${rec.known_outcome === 'unknown' ? 'pending' : rec.known_outcome === 'confirmed_success' ? 'completed' : 'failed'}`}>
                            {rec.known_outcome}
                          </span>
                        </td>
                        <td>
                          <span className={`status-pill ${rec.status}`}>
                            {rec.status}
                          </span>
                        </td>
                        <td style={{ maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {rec.decision_notes || (rec.action_taken ? `Action: ${rec.action_taken}` : '—')}
                        </td>
                        <td style={{ color: 'var(--text-dim)', fontSize: '0.75rem' }}>
                          {new Date(rec.created_at).toLocaleTimeString()}
                        </td>
                        <td>
                          <button
                            className="btn-primary"
                            style={{ padding: '0.25rem 0.65rem', fontSize: '0.75rem' }}
                            onClick={() => handleOpenDecisionModal(rec)}
                          >
                            Review & Decide
                          </button>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── Policy Propose / Edit Modal ──────────────────────────────────── */}
      {showPolicyModal && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: '640px' }}>
            <div className="modal-header">
              <h3 className="modal-title">Propose / Edit Tenant Policy</h3>
              <button
                className="btn-secondary"
                style={{ padding: '0.25rem 0.5rem' }}
                onClick={() => setShowPolicyModal(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSavePolicy}>
              <div className="form-group">
                <label className="form-label">
                  Policy Configuration JSON (Tool Rules, Cache Rules, Resource Limits)
                </label>
                <textarea
                  className="form-textarea"
                  style={{ minHeight: '260px', fontFamily: 'var(--font-mono)', fontSize: '0.8125rem' }}
                  value={policyEditorJson}
                  onChange={(e) => setPolicyEditorJson(e.target.value)}
                  required
                />
              </div>

              {policyValidation && (
                <div className={`validation-box ${policyValidation.valid ? 'valid' : 'invalid'}`}>
                  {policyValidation.valid ? (
                    <div>✓ <strong>Policy is Valid:</strong> Rules satisfy all platform quotas and security invariants.</div>
                  ) : (
                    <div>
                      <strong>Validation Errors:</strong>
                      <ul style={{ marginLeft: '1.25rem', marginTop: '0.35rem' }}>
                        {(policyValidation.errors || []).map((err, i) => (
                          <li key={i}>{err}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="modal-footer" style={{ marginTop: '1.5rem' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={handleValidatePolicyJson}
                  disabled={isValidatingPolicy}
                >
                  {isValidatingPolicy ? 'Validating...' : 'Validate Rules'}
                </button>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowPolicyModal(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={isSavingPolicy}>
                  {isSavingPolicy ? 'Activating...' : 'Save & Activate Policy'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Workflow Reconciliation Decision Modal ───────────────────────── */}
      {selectedReconciliation && (
        <div className="modal-backdrop">
          <div className="modal-card" style={{ maxWidth: '600px' }}>
            <div className="modal-header">
              <h3 className="modal-title">Human-in-the-Loop Operator Decision</h3>
              <button
                className="btn-secondary"
                style={{ padding: '0.25rem 0.5rem' }}
                onClick={() => setSelectedReconciliation(null)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSubmitDecision}>
              <div style={{ background: 'rgba(0,0,0,0.25)', padding: '0.85rem 1rem', borderRadius: '8px', marginBottom: '1.25rem', fontSize: '0.8125rem' }}>
                <div>Case: <span className="mono-text">{selectedReconciliation.reconciliation_id}</span></div>
                <div>Workflow: <span className="mono-text">{selectedReconciliation.workflow_id}</span></div>
                <div>Task: <span className="mono-text">{selectedReconciliation.task_id}</span></div>
                <div>Tool: <strong>{selectedReconciliation.tool_name}</strong> ({selectedReconciliation.operation_class})</div>
                <div>Known Outcome: <span className="mono-text" style={{ color: 'var(--warning)' }}>{selectedReconciliation.known_outcome}</span></div>
                <div>Reason: <span style={{ color: 'var(--text-muted)' }}>{selectedReconciliation.reason}</span></div>
              </div>

              <div className="form-group">
                <label className="form-label">Operator Decision Action</label>
                <select
                  className="form-input"
                  value={decisionAction}
                  onChange={(e) => setDecisionAction(e.target.value)}
                >
                  <option value="approve_retry">Approve Controlled Retry (Authorized Replay)</option>
                  <option value="reject_retry">Reject Retry (Finalize as Failed)</option>
                  <option value="acknowledge_success">Acknowledge Confirmed Success (External Verification)</option>
                  <option value="acknowledge_failure">Acknowledge Confirmed Failure</option>
                  <option value="external_investigation">Mark for External Investigation</option>
                  <option value="resolve">Close & Resolve Case</option>
                </select>
              </div>

              {decisionAction === 'approve_retry' && (
                <div>
                  <div className="risk-alert-box">
                    <strong>⚠️ High-Risk Action Warning:</strong> Replaying a side-effecting operation whose outcome was unknown may cause duplicate external mutations if the upstream server executed part of the request.
                  </div>

                  <div className="form-group">
                    <label className="form-label">Upstream Idempotency Key (Optional if supported by tool)</label>
                    <input
                      type="text"
                      className="form-input"
                      value={idempotencyKey}
                      onChange={(e) => setIdempotencyKey(e.target.value)}
                      placeholder="e.g. retry-tx-12345"
                    />
                  </div>

                  <div className="form-group" style={{ display: 'flex', alignItems: 'flex-start', gap: '0.5rem', marginTop: '0.75rem' }}>
                    <input
                      type="checkbox"
                      id="riskAck"
                      checked={riskAcknowledged}
                      onChange={(e) => setRiskAcknowledged(e.target.checked)}
                      style={{ marginTop: '0.25rem' }}
                    />
                    <label htmlFor="riskAck" style={{ fontSize: '0.8125rem', color: 'var(--text-main)', cursor: 'pointer' }}>
                      I explicitly acknowledge the operational risk of retrying this operation without guaranteed upstream idempotency.
                    </label>
                  </div>
                </div>
              )}

              <div className="form-group" style={{ marginTop: '1rem' }}>
                <label className="form-label">Decision Audit Notes</label>
                <textarea
                  className="form-textarea"
                  style={{ minHeight: '80px' }}
                  placeholder="Document justification, verification tickets, or external system state..."
                  value={decisionNotes}
                  onChange={(e) => setDecisionNotes(e.target.value)}
                />
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setSelectedReconciliation(null)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={isSubmittingDecision || (decisionAction === 'approve_retry' && !idempotencyKey.trim() && !riskAcknowledged)}
                >
                  {isSubmittingDecision ? 'Submitting...' : 'Record Decision'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Workflow Submission Modal ────────────────────────────────────── */}
      {showSubmitModal && (
        <div className="modal-backdrop">
          <div className="modal-card">
            <div className="modal-header">
              <h3 className="modal-title">Submit Repository Intelligence Workflow</h3>
              <button
                className="btn-secondary"
                style={{ padding: '0.25rem 0.5rem' }}
                onClick={() => setShowSubmitModal(false)}
              >
                ✕
              </button>
            </div>

            <form onSubmit={handleSubmitWorkflow}>
              <div className="form-group">
                <label className="form-label">Investigation Prompt</label>
                <textarea
                  className="form-textarea"
                  value={promptInput}
                  onChange={(e) => setPromptInput(e.target.value)}
                  required
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div className="form-group">
                  <label className="form-label">Target Owner</label>
                  <input
                    type="text"
                    className="form-input"
                    value={ownerInput}
                    onChange={(e) => setOwnerInput(e.target.value)}
                    required
                  />
                </div>
                <div className="form-group">
                  <label className="form-label">Target Repository</label>
                  <input
                    type="text"
                    className="form-input"
                    value={repoInput}
                    onChange={(e) => setRepoInput(e.target.value)}
                    required
                  />
                </div>
              </div>

              <div className="form-group">
                <label className="form-label">Concurrency Limit: {concurrencyInput} concurrent workers</label>
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={concurrencyInput}
                  onChange={(e) => setConcurrencyInput(Number(e.target.value))}
                  style={{ width: '100%' }}
                />
              </div>

              <div className="modal-footer">
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowSubmitModal(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={isSubmitting}>
                  {isSubmitting ? 'Starting DAG...' : 'Execute Workflow'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
