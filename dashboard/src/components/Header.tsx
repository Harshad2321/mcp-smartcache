import React from 'react';
import { NavigationSection } from '../types';
import { RefreshCw, Play, Radio, Building2 } from 'lucide-react';

interface HeaderProps {
  currentSection: NavigationSection;
  tenantId: string;
  dataMode?: 'mock' | 'live';
  onToggleDataMode?: () => void;
  clusterHealth?: { gateway: boolean; controlApi: boolean; redis: boolean; postgres: boolean };
  onTriggerDemo?: () => void;
  onRefresh?: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  currentSection,
  tenantId,
  clusterHealth = { gateway: true, controlApi: true, redis: true, postgres: true },
  onTriggerDemo,
  onRefresh,
}) => {
  const sectionMeta: Partial<Record<NavigationSection, { title: string; subtitle: string }>> = {
    architecture: {
      title: 'Interactive System Architecture',
      subtitle: 'A self-optimizing MCP gateway that cuts tool-call latency by 99%+ through distributed caching and request coalescing.',
    },
    workflows: {
      title: 'Workflow Orchestration Engine',
      subtitle: 'Enterprise-grade LangGraph state orchestration with durable PostgreSQL checkpoints and automated recovery.',
    },
    dag: {
      title: 'Task Dependency DAG Visualizer',
      subtitle: 'Real-time task dependency visualization, streaming status tracking, and step-level state inspection.',
    },
    traces: {
      title: 'Live Telemetry & Event Tracing',
      subtitle: 'High-resolution event stream, singleflight leader elections, and policy evaluation logs.',
    },
    cache: {
      title: 'Cache & Distributed Singleflight',
      subtitle: 'Redis L2 distributed caching, stampede suppression, and sub-4ms warm latency acceleration.',
    },
    policies: {
      title: 'Tenant Governance & Quotas',
      subtitle: 'Multi-tenant rate limits, cryptographic tool allowlists, and immutable policy rollback history.',
    },
    reconciliation: {
      title: 'Human-in-the-Loop Safety Gates',
      subtitle: 'Authoritative quarantine gates safeguarding non-idempotent mutations and high-risk operations.',
    },
    servers: {
      title: 'Upstream MCP Server Registry',
      subtitle: 'Dynamic discovery of stdio and remote SSE transports with automated schema inspection.',
    },
    settings: {
      title: 'Developer Platform Settings',
      subtitle: 'API credentials, OpenAPI schema documentation, and network security boundaries.',
    },
  };

  const current = sectionMeta[currentSection] || {
    title: 'MCP SmartCache',
    subtitle: 'High-performance control plane and gateway for Model Context Protocol workflows.',
  };

  const tenantDisplayName =
    tenantId === 'tenant_a'
      ? 'Acme Global'
      : tenantId === 'tenant_b'
      ? 'Apex Financial'
      : 'Core Staging';

  return (
    <header className="sticky top-0 z-20 bg-[#11141A]/95 backdrop-blur-md border-b border-white/[0.08]">
      <div className="flex items-center justify-between gap-4 px-6 py-3.5 min-h-[72px]">
        {/* Title block */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5 min-w-0">
            <h1 className="text-sm font-semibold text-white tracking-tight truncate">
              {current.title}
            </h1>
            <span className="text-slate-600 shrink-0">/</span>

            {/* Enterprise Multi-Tenant Account Badge */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[#161A22] border border-white/[0.08] text-xs font-medium text-slate-200 shrink-0">
              <Building2 className="w-3.5 h-3.5 text-[#00F0FF]" />
              <span className="text-white font-semibold">{tenantDisplayName}</span>
              <span className="text-[10px] font-mono text-slate-400">({tenantId})</span>
            </div>

            {/* Enterprise SLA Badge */}
            <span className="hidden md:flex shrink-0 text-[10px] font-mono px-2.5 py-1 rounded-md bg-emerald-500/10 text-emerald-300 border border-emerald-500/25 font-semibold items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              99.98% UPTIME SLA
            </span>
          </div>
          <p className="text-xs text-slate-400 truncate mt-0.5">{current.subtitle}</p>
        </div>

        {/* Actions & Health Pills */}
        <div className="flex items-center gap-3 shrink-0">
          {/* Cluster Status Pill */}
          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#161A22] border border-white/[0.08] text-xs font-mono text-slate-300">
            <Radio className="w-3 h-3 text-emerald-400" />
            <span>Cluster: Active</span>
          </div>

          {/* Cluster Health Pills */}
          <div className="hidden xl:flex items-center gap-3 bg-[#161A22] px-3 py-1.5 rounded-lg border border-white/[0.06] text-xs font-mono">
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${clusterHealth.gateway ? 'bg-emerald-400' : 'bg-rose-500'}`} />
              <span className="text-slate-300">Gateway :8000</span>
            </div>
            <span className="text-slate-600">|</span>
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${clusterHealth.redis ? 'bg-emerald-400' : 'bg-rose-500'}`} />
              <span className="text-slate-300">Redis L2</span>
            </div>
            <span className="text-slate-600">|</span>
            <div className="flex items-center gap-1.5">
              <span className={`w-1.5 h-1.5 rounded-full ${clusterHealth.postgres ? 'bg-emerald-400' : 'bg-rose-500'}`} />
              <span className="text-slate-300">PostgreSQL</span>
            </div>
          </div>

          {onRefresh && (
            <button
              onClick={onRefresh}
              className="p-2 rounded-lg bg-[#161A22] border border-white/[0.08] text-slate-400 hover:text-white transition-colors shrink-0"
              title="Refresh Real-time Telemetry"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          )}

          {onTriggerDemo && (
            <button
              onClick={onTriggerDemo}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#00F0FF] text-black font-semibold text-xs hover:bg-[#00F0FF]/90 transition-colors shrink-0 font-mono shadow-[0_0_12px_rgba(0,240,255,0.3)]"
            >
              <Play className="w-3.5 h-3.5 fill-black" />
              <span>See It in Action</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
};
