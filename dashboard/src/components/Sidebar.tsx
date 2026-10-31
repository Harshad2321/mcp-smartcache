import React from 'react';
import { motion } from 'framer-motion';
import {
  Network,
  GitBranch,
  Workflow,
  Zap,
  Shield,
  AlertTriangle,
  Server,
  Activity,
  Layers,
  Terminal,
  Building2,
} from 'lucide-react';
import { NavigationSection } from '../types';
import { MOCK_TENANTS } from '../mock/fixtures';

interface SidebarProps {
  currentSection: NavigationSection;
  onSelectSection: (section: NavigationSection) => void;
  currentTenant: string;
  onSelectTenant: (tenant: string) => void;
  dataMode?: 'mock' | 'live';
}

export const Sidebar: React.FC<SidebarProps> = ({
  currentSection,
  onSelectSection,
  currentTenant,
  onSelectTenant,
}) => {
  const navItems: { id: NavigationSection; label: string; icon: React.ReactNode; badge?: string }[] = [
    { id: 'architecture', label: 'Live Architecture', icon: <Network className="w-4 h-4" />, badge: 'Topology' },
    { id: 'workflows', label: 'Workflow Engine', icon: <Workflow className="w-4 h-4" /> },
    { id: 'dag', label: 'Task DAG Visualizer', icon: <GitBranch className="w-4 h-4" />, badge: 'DAG' },
    { id: 'traces', label: 'Live Trace Stream', icon: <Activity className="w-4 h-4" /> },
    { id: 'cache', label: 'Cache & Singleflight', icon: <Zap className="w-4 h-4 text-emerald-400" />, badge: '99.4%' },
    { id: 'policies', label: 'Tenant Policies', icon: <Shield className="w-4 h-4" /> },
    { id: 'reconciliation', label: 'Reconciliation (HITL)', icon: <AlertTriangle className="w-4 h-4 text-amber-400" />, badge: 'Safety' },
    { id: 'servers', label: 'Upstream MCP Servers', icon: <Server className="w-4 h-4" /> },
  ];

  return (
    <aside className="w-64 min-w-64 bg-[#11141A] border-r border-white/[0.08] flex flex-col h-screen sticky top-0 z-30 select-none">
      {/* Brand & Platform Header */}
      <div className="p-5 border-b border-white/[0.08]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#00F0FF] to-[#0070F3] flex items-center justify-center shadow-[0_0_15px_rgba(0,240,255,0.25)] shrink-0 font-bold text-black text-sm">
            <Layers className="w-4 h-4 text-black stroke-[2.5]" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5 min-w-0">
              <span className="font-semibold text-white tracking-tight text-sm truncate">MCP SmartCache</span>
              <span className="shrink-0 px-1.5 py-0.2 rounded text-[10px] font-mono bg-[#00F0FF]/15 text-[#00F0FF] border border-[#00F0FF]/30 font-semibold">
                v1.0
              </span>
            </div>
            <p className="text-[11px] text-slate-400 truncate">Enterprise Gateway & Runtime</p>
          </div>
        </div>

        {/* Enterprise Organization Selector */}
        <div className="mt-4 bg-[#161A22] p-2.5 rounded-xl border border-white/[0.06]">
          <div className="text-[10px] uppercase font-mono tracking-wider text-slate-400 px-1 mb-1.5 font-semibold flex items-center justify-between">
            <span className="flex items-center gap-1">
              <Building2 className="w-3 h-3 text-[#00F0FF]" />
              <span>Organization</span>
            </span>
            <span className="text-emerald-400">Isolated</span>
          </div>
          <select
            value={currentTenant}
            onChange={(e) => onSelectTenant(e.target.value)}
            className="w-full bg-[#1D222D] text-xs text-white rounded-lg px-2.5 py-1.5 outline-none cursor-pointer font-medium border border-white/[0.08] focus:border-[#00F0FF] transition-colors"
          >
            {MOCK_TENANTS.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Navigation List */}
      <nav className="flex-1 overflow-y-auto px-3 py-4 space-y-1">
        <div className="px-2 pb-2 text-[10px] font-mono uppercase tracking-wider text-slate-500 font-semibold">
          Platform Views
        </div>
        {navItems.map((item) => {
          const isActive = currentSection === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelectSection(item.id)}
              className={`w-full relative flex items-center justify-between px-3 py-2.5 rounded-lg text-xs font-medium transition-colors ${
                isActive
                  ? 'text-white bg-white/[0.08] font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.03]'
              }`}
            >
              {isActive && (
                <motion.div
                  layoutId="activeNavPill"
                  className="absolute left-0 top-1.5 bottom-1.5 w-1 bg-[#00F0FF] rounded-r"
                  transition={{ type: 'spring', stiffness: 380, damping: 30 }}
                />
              )}
              <div className="flex items-center gap-2.5 min-w-0">
                <span className={isActive ? 'text-[#00F0FF]' : 'text-slate-500'}>
                  {item.icon}
                </span>
                <span className="truncate">{item.label}</span>
              </div>
              {item.badge && (
                <span className="shrink-0 text-[10px] font-mono px-1.5 py-0.2 rounded bg-white/[0.06] text-slate-300 border border-white/[0.06]">
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* Footer: Production Health Status */}
      <div className="p-4 border-t border-white/[0.08] bg-[#0E1015] space-y-2.5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-400" />
            </span>
            <span className="text-[11px] font-mono text-slate-300">
              Cluster: Operational
            </span>
          </div>
          <span className="text-[10px] font-mono text-emerald-400 font-semibold">
            99.98% SLA
          </span>
        </div>

        <div className="flex items-center justify-between px-2.5 py-1.5 rounded bg-white/[0.04] text-[11px] font-mono text-slate-400 border border-white/[0.05]">
          <div className="flex items-center gap-1.5">
            <Terminal className="w-3 h-3 text-[#00F0FF]" />
            <span>Gateway :8000</span>
          </div>
          <span className="text-emerald-400 text-[10px] font-semibold">Active</span>
        </div>
      </div>
    </aside>
  );
};
