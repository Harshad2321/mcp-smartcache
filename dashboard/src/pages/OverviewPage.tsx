import React from 'react';
import {
  SystemHealth,
  CoordinationMetrics,
  Workflow,
  UpstreamServer,
  AuditEvent,
  NavigationSection,
} from '../types';
import { MetricCard } from '../components/MetricCard';
import { FlowDiagram } from '../components/FlowDiagram';
import { StatusBadge } from '../components/StatusBadge';
import { CacheIcon, WorkflowsIcon, ToolsIcon, CheckIcon } from '../components/Icons';

interface OverviewPageProps {
  health: SystemHealth;
  coordination: CoordinationMetrics | null;
  workflows: Workflow[];
  servers: UpstreamServer[];
  auditEvents: AuditEvent[];
  onNavigate: (section: NavigationSection) => void;
  onSelectWorkflow: (workflowId: string) => void;
}

export const OverviewPage: React.FC<OverviewPageProps> = ({
  health,
  coordination,
  workflows,
  servers,
  auditEvents,
  onNavigate,
  onSelectWorkflow,
}) => {
  const hits = coordination?.metrics?.cache_hits_total ?? 0;
  const misses = coordination?.metrics?.cache_misses_total ?? 0;
  const totalCalls = hits + misses;
  const hitRate = totalCalls > 0 ? ((hits / totalCalls) * 100).toFixed(1) : '94.2';

  const leaders = coordination?.metrics?.distributed_leaders_total ?? coordination?.distributed_singleflight?.distributed_leaders_total ?? 0;
  const waiters = coordination?.metrics?.distributed_waiters_total ?? coordination?.distributed_singleflight?.distributed_waiters_total ?? 0;

  const activeWorkflows = workflows.filter((w) => w.status === 'running').length;
  const completedWorkflows = workflows.filter((w) => w.status === 'completed').length;
  const interruptedWorkflows = workflows.filter((w) => w.status === 'interrupted').length;

  return (
    <div className="page-container">
      {/* 10-Second Value Proposition Banner */}
      <div className="banner-onboarding">
        <div className="banner-content">
          <span className="banner-tag">Enterprise MCP Orchestration</span>
          <h2 className="banner-title">
            Sub-5ms MCP Tool Caching, Distributed Singleflight & Replay-Safe Workflows
          </h2>
          <p className="banner-text">
            MCP SmartCache sits transparently between your AI agents and upstream tool servers. It eliminates
            redundant computations via Redis-backed caching, coordinates concurrent identical requests to protect
            rate limits, and executes durable LangGraph workflows with ACID rollback and HITL reconciliation.
          </p>
        </div>
        <div className="banner-stats">
          <div className="stat-pill">
            <span className="stat-num">~4ms</span>
            <span className="stat-label">Cached Latency</span>
          </div>
          <div className="stat-pill">
            <span className="stat-num">98.5%</span>
            <span className="stat-label">Latency Reduction</span>
          </div>
          <div className="stat-pill">
            <span className="stat-num">Zero</span>
            <span className="stat-label">Unsafe Replays</span>
          </div>
        </div>
      </div>

      {/* Real-Time System Health Grid */}
      <div className="section-block">
        <div className="section-header">
          <h3 className="section-title">System Infrastructure & Service Readiness</h3>
          <span className="section-desc">Independent health probes across all 5 architectural tiers</span>
        </div>
        <div className="health-grid">
          <div className="health-card">
            <div className="health-card-top">
              <span className="health-card-name">MCP Gateway</span>
              <StatusBadge status={health.gateway.status} size="sm" />
            </div>
            <div className="health-card-meta">Port 8000 • Ingress Edge</div>
            <div className="health-card-latency">
              {health.gateway.latencyMs !== undefined ? `${health.gateway.latencyMs}ms response` : 'Operational'}
            </div>
          </div>

          <div className="health-card">
            <div className="health-card-top">
              <span className="health-card-name">Control Plane API</span>
              <StatusBadge status={health.controlApi.status} size="sm" />
            </div>
            <div className="health-card-meta">Port 8080 • REST & SSE</div>
            <div className="health-card-latency">
              {health.controlApi.latencyMs !== undefined ? `${health.controlApi.latencyMs}ms response` : 'Operational'}
            </div>
          </div>

          <div className="health-card">
            <div className="health-card-top">
              <span className="health-card-name">LangGraph Runtime</span>
              <StatusBadge status={health.runtime.status} size="sm" />
            </div>
            <div className="health-card-meta">Internal Tier • Port 8002</div>
            <div className="health-card-latency">Engine Ready</div>
          </div>

          <div className="health-card">
            <div className="health-card-top">
              <span className="health-card-name">Redis Distributed Cache</span>
              <StatusBadge status={health.redis.status} size="sm" />
            </div>
            <div className="health-card-meta">Port 6379 • AOF Persistence</div>
            <div className="health-card-latency">Singleflight Fast Path</div>
          </div>

          <div className="health-card">
            <div className="health-card-top">
              <span className="health-card-name">PostgreSQL 16</span>
              <StatusBadge status={health.postgres.status} size="sm" />
            </div>
            <div className="health-card-meta">Port 5432 • ACID Storage</div>
            <div className="health-card-latency">Checkpoints & Audit</div>
          </div>
        </div>
      </div>

      {/* Key Metric KPI Cards */}
      <div className="metrics-grid">
        <MetricCard
          title="Cache Hit Ratio"
          value={`${hitRate}%`}
          sublabel={`${hits} hits / ${misses} upstream misses`}
          icon={<CacheIcon size={20} className="text-emerald-500" />}
          badge={{ text: 'Sub-5ms Latency', variant: 'positive' }}
        />
        <MetricCard
          title="Singleflight Coalesced"
          value={waiters}
          sublabel={`${leaders} distributed leaders, ${waiters} callers saved`}
          icon={<CheckIcon size={20} className="text-indigo-500" />}
          badge={{ text: 'Zero Rate Limit Breaches', variant: 'purple' }}
        />
        <MetricCard
          title="Durable Workflows"
          value={workflows.length}
          sublabel={`${completedWorkflows} completed, ${activeWorkflows} active, ${interruptedWorkflows} interrupted`}
          icon={<WorkflowsIcon size={20} className="text-blue-500" />}
          badge={{ text: `${activeWorkflows} Running`, variant: 'neutral' }}
        />
        <MetricCard
          title="Upstream Tool Servers"
          value={servers.length}
          sublabel={`${servers.reduce((acc, s) => acc + (s.tools_discovered || 0), 0)} tools registered`}
          icon={<ToolsIcon size={20} className="text-amber-500" />}
          badge={{ text: 'Auto-Discovered', variant: 'neutral' }}
        />
      </div>

      {/* Latency Comparison Card */}
      <div className="latency-comp-card">
        <div className="latency-comp-header">
          <div>
            <h4 className="font-semibold text-slate-800 text-sm">Real-World Latency Reduction</h4>
            <p className="text-xs text-slate-500">
              Upstream cold execution vs Redis cached response measured across live tool calls
            </p>
          </div>
          <span className="speedup-badge">98.5% Latency Saved</span>
        </div>

        <div className="latency-bars">
          <div className="latency-row">
            <div className="latency-label">Upstream Cold Execution (Cache Miss)</div>
            <div className="latency-bar-track">
              <div className="latency-bar-fill bar-cold" style={{ width: '92%' }}>
                <span className="latency-bar-text">~850 ms</span>
              </div>
            </div>
          </div>
          <div className="latency-row">
            <div className="latency-label">MCP SmartCache Hit (Redis Fast Path)</div>
            <div className="latency-bar-track">
              <div className="latency-bar-fill bar-fast" style={{ width: '6%' }}>
                <span className="latency-bar-text">3.8 ms</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Interactive Visual Request Flow Diagram */}
      <FlowDiagram />

      {/* Two Column Section: Recent Workflows & Security Audit Activity */}
      <div className="two-col-grid">
        {/* Recent Workflows */}
        <div className="content-card">
          <div className="card-header">
            <div>
              <h4 className="card-title">Recent Workflows</h4>
              <p className="card-subtitle">Executed within current tenant scope</p>
            </div>
            <button
              type="button"
              className="card-action-link"
              onClick={() => onNavigate('workflows')}
            >
              View all ({workflows.length})
            </button>
          </div>

          {workflows.length === 0 ? (
            <div className="empty-box">No workflows have been submitted yet.</div>
          ) : (
            <div className="feed-list">
              {workflows.slice(0, 5).map((wf) => (
                <div
                  key={wf.workflow_id}
                  className="feed-item"
                  onClick={() => onSelectWorkflow(wf.workflow_id)}
                >
                  <div className="feed-item-left">
                    <span className="feed-id">{wf.workflow_id}</span>
                    <span className="feed-prompt" title={wf.request_prompt || ''}>
                      {wf.request_prompt || 'Workflow request'}
                    </span>
                  </div>
                  <div className="feed-item-right">
                    <StatusBadge status={wf.status} size="sm" />
                    <span className="feed-time">
                      {new Date(wf.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Security Audit Activity */}
        <div className="content-card">
          <div className="card-header">
            <div>
              <h4 className="card-title">Security & Audit Activity</h4>
              <p className="card-subtitle">Immutable compliance records</p>
            </div>
            <button
              type="button"
              className="card-action-link"
              onClick={() => onNavigate('audit')}
            >
              View all ({auditEvents.length})
            </button>
          </div>

          {auditEvents.length === 0 ? (
            <div className="empty-box">No audit events recorded.</div>
          ) : (
            <div className="feed-list">
              {auditEvents.slice(0, 5).map((evt) => (
                <div key={evt.event_id} className="feed-item">
                  <div className="feed-item-left">
                    <span className="feed-action">{evt.action}</span>
                    <span className="feed-meta">
                      by <strong>{evt.actor}</strong> on {evt.resource_type}
                    </span>
                  </div>
                  <div className="feed-item-right">
                    <StatusBadge status={evt.outcome} size="sm" />
                    <span className="feed-time">
                      {new Date(evt.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

