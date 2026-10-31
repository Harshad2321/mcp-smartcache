import React, { useState } from 'react';
import { MOCK_TRACES } from '../mock/fixtures';
import { TraceEntry } from '../types';
import {
  Activity,
  Zap,
  Shield,
  Database,
  CheckCircle2,
  AlertTriangle,
  Search,
} from 'lucide-react';

export const TraceTimelinePage: React.FC = () => {
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');

  const categories = [
    { id: 'all', label: 'All Events' },
    { id: 'cache', label: 'Cache & Singleflight' },
    { id: 'policy', label: 'Policy Evaluation' },
    { id: 'checkpoint', label: 'Checkpoints' },
    { id: 'task', label: 'Task Execution' },
    { id: 'reconciliation', label: 'Reconciliation' },
  ];

  const filteredTraces = MOCK_TRACES.filter((tr) => {
    const matchesCategory =
      selectedCategory === 'all' ||
      (selectedCategory === 'cache' && (tr.event_type === 'cache_hit' || tr.event_type === 'cache_miss')) ||
      (selectedCategory === 'policy' && tr.event_type === 'policy_eval') ||
      (selectedCategory === 'checkpoint' && tr.event_type === 'checkpoint_saved') ||
      (selectedCategory === 'task' && (tr.event_type === 'task_start' || tr.event_type === 'task_complete' || tr.event_type === 'upstream_call')) ||
      (selectedCategory === 'reconciliation' && tr.event_type === 'interrupted');

    const matchesSearch =
      tr.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
      tr.workflow_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      (tr.task_id && tr.task_id.toLowerCase().includes(searchQuery.toLowerCase()));

    return matchesCategory && matchesSearch;
  });

  const getEventBadge = (type: TraceEntry['event_type']) => {
    switch (type) {
      case 'cache_hit':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/40 text-emerald-300 border border-emerald-500/30">CACHE HIT</span>;
      case 'cache_miss':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-950/40 text-amber-300 border border-amber-500/30">CACHE MISS (LEADER)</span>;
      case 'policy_eval':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-violet-950/40 text-violet-300 border border-violet-500/30">POLICY EVAL</span>;
      case 'checkpoint_saved':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-sky-950/40 text-sky-300 border border-sky-500/30">CHECKPOINT PERSISTED</span>;
      case 'task_complete':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-emerald-950/40 text-emerald-300 border border-emerald-500/30">TASK COMPLETED</span>;
      case 'upstream_call':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00F0FF]/15 text-[#00F0FF] border border-[#00F0FF]/30">UPSTREAM MCP CALL</span>;
      case 'interrupted':
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-rose-950/40 text-rose-300 border border-rose-500/30">QUARANTINED</span>;
      default:
        return <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-white/[0.08] text-slate-300 border border-white/[0.08]">{type.toUpperCase()}</span>;
    }
  };

  const getEventIcon = (type: TraceEntry['event_type']) => {
    switch (type) {
      case 'cache_hit':
      case 'cache_miss':
        return <Zap className="w-4 h-4 text-emerald-400" />;
      case 'policy_eval':
        return <Shield className="w-4 h-4 text-violet-400" />;
      case 'checkpoint_saved':
        return <Database className="w-4 h-4 text-sky-400" />;
      case 'task_complete':
        return <CheckCircle2 className="w-4 h-4 text-emerald-400" />;
      case 'interrupted':
        return <AlertTriangle className="w-4 h-4 text-rose-400" />;
      default:
        return <Activity className="w-4 h-4 text-[#00F0FF]" />;
    }
  };

  return (
    <div className="p-6 lg:p-8 max-w-5xl mx-auto w-full min-w-0 space-y-6">
      {/* Stream Header & Connection State */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#11141A] p-4 rounded-xl border border-white/[0.08]">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-sm font-bold text-white tracking-tight">Execution Trace Stream</h2>
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/[0.06] border border-white/[0.08]">
              <span className="w-2 h-2 rounded-full bg-[#00F0FF] animate-pulse" />
              <span className="text-[11px] font-mono font-semibold text-white">
                Live SSE Stream Connected
              </span>
            </div>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Millisecond-accurate chronological event telemetry across Gateway, Singleflight, and LangGraph Runtime.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-slate-400 bg-[#161A22] px-3 py-1.5 rounded-lg border border-white/[0.06]">
            Buffer: 500 Events
          </span>
        </div>
      </div>

      {/* Filter Chips and Search */}
      <div className="bg-[#11141A] p-2.5 rounded-xl border border-white/[0.08] flex flex-col md:flex-row items-center justify-between gap-3">
        <div className="flex items-center gap-1.5 overflow-x-auto w-full md:w-auto p-0.5">
          {categories.map((c) => (
            <button
              key={c.id}
              onClick={() => setSelectedCategory(c.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium font-mono whitespace-nowrap transition-all ${
                selectedCategory === c.id
                  ? 'bg-white/[0.1] text-white font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
              }`}
            >
              {c.label}
            </button>
          ))}
        </div>

        <div className="relative w-full md:w-64">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Search trace logs..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-[#161A22] text-xs text-white rounded-lg pl-8 pr-3 py-1.5 border border-white/[0.08] focus:border-[#00F0FF] outline-none font-mono placeholder:text-slate-500"
          />
        </div>
      </div>

      {/* Timeline Stream */}
      <div className="relative pl-6 space-y-4 before:absolute before:left-2.5 before:top-3 before:bottom-3 before:w-0.5 before:bg-white/[0.08]">
        {filteredTraces.map((tr) => (
          <div key={tr.id} className="relative flex items-start gap-4 group">
            {/* Dot */}
            <div className="absolute -left-6 top-2 w-5 h-5 rounded-full bg-[#161A22] border border-white/[0.15] flex items-center justify-center group-hover:border-[#00F0FF] transition-colors">
              <span className="w-1.5 h-1.5 rounded-full bg-[#00F0FF]" />
            </div>

            {/* Event Card */}
            <div className="flex-1 bg-[#11141A] p-4 rounded-xl border border-white/[0.08] hover:border-white/[0.18] transition-all space-y-2">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2.5">
                  <div className="p-1.5 rounded-md bg-[#161A22] border border-white/[0.06]">
                    {getEventIcon(tr.event_type)}
                  </div>
                  <span className="text-xs font-mono font-semibold text-white">
                    {tr.task_id || tr.workflow_id}
                  </span>
                  {getEventBadge(tr.event_type)}
                </div>

                <div className="flex items-center gap-2.5 text-xs font-mono text-slate-400">
                  {tr.duration_ms !== undefined && (
                    <span className="text-[#00F0FF] bg-[#00F0FF]/10 px-2 py-0.5 rounded border border-[#00F0FF]/25 font-semibold">
                      {tr.duration_ms} ms
                    </span>
                  )}
                  <span>{tr.timestamp}</span>
                </div>
              </div>

              <p className="text-xs text-slate-300 font-mono leading-relaxed">
                {tr.message}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
