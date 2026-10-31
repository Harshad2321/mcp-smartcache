import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { WorkflowItem } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Search, GitBranch, Play, Clock, ArrowRight } from 'lucide-react';

interface WorkflowListPageProps {
  workflows: WorkflowItem[];
  tenantId?: string;
  onSelectWorkflow: (workflowId: string) => void;
  onOpenNewModal: () => void;
}

export const WorkflowListPage: React.FC<WorkflowListPageProps> = ({
  workflows,
  tenantId,
  onSelectWorkflow,
  onOpenNewModal,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const filteredWorkflows = workflows.filter((wf) => {
    const matchesSearch =
      wf.workflow_id.toLowerCase().includes(searchQuery.toLowerCase()) ||
      wf.request_prompt.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = statusFilter === 'all' || wf.status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  const statuses: { id: string; label: string; count: number }[] = [
    { id: 'all', label: 'All Workflows', count: workflows.length },
    { id: 'running', label: 'Running', count: workflows.filter((w) => w.status === 'running').length },
    { id: 'completed', label: 'Completed', count: workflows.filter((w) => w.status === 'completed').length },
    { id: 'interrupted', label: 'Interrupted (HITL)', count: workflows.filter((w) => w.status === 'interrupted').length },
    { id: 'failed', label: 'Failed', count: workflows.filter((w) => w.status === 'failed').length },
  ];

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: 0.05,
      },
    },
  };

  const itemVariants = {
    hidden: { opacity: 0, y: 8 },
    show: { opacity: 1, y: 0, transition: { duration: 0.2 } },
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Top Banner & Action */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-white tracking-tight">Active Workflow Pipeline</h2>
            {tenantId && (
              <span className="text-xs font-mono px-2 py-0.5 rounded bg-[#00F0FF]/15 text-[#00F0FF] border border-[#00F0FF]/30 font-semibold">
                {tenantId}
              </span>
            )}
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-white/[0.06] text-slate-400 border border-white/[0.06]">
              {filteredWorkflows.length} Loaded
            </span>
          </div>
          <p className="text-xs text-slate-400">
            Durable LangGraph workflows orchestrated with PostgreSQL checkpoints and multi-tenant rate limits.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={onOpenNewModal}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-[#00F0FF] text-black font-semibold text-xs hover:bg-[#00F0FF]/90 transition-all shadow-[0_0_15px_rgba(0,240,255,0.35)] font-mono"
          >
            <Play className="w-3.5 h-3.5 fill-black" />
            <span>+ Dispatch Workflow</span>
          </button>
        </div>
      </div>

      {/* Filter Bar & Tabs */}
      <div className="bg-[#11141A] p-2 rounded-xl border border-white/[0.08] flex flex-col md:flex-row items-center justify-between gap-3">
        {/* Status Tabs */}
        <div className="flex items-center gap-1 overflow-x-auto w-full md:w-auto p-1">
          {statuses.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setStatusFilter(tab.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all flex items-center gap-2 whitespace-nowrap ${
                statusFilter === tab.id
                  ? 'bg-white/[0.1] text-white font-semibold'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-white/[0.04]'
              }`}
            >
              <span>{tab.label}</span>
              <span
                className={`text-[10px] font-mono px-1.5 py-0.2 rounded ${
                  statusFilter === tab.id ? 'bg-[#00F0FF]/20 text-[#00F0FF]' : 'bg-white/[0.06] text-slate-400'
                }`}
              >
                {tab.count}
              </span>
            </button>
          ))}
        </div>

        {/* Search Input */}
        <div className="relative w-full md:w-72">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            placeholder="Search prompt, ID, tool..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-[#161A22] text-xs text-white rounded-lg pl-8 pr-3 py-1.5 border border-white/[0.08] focus:border-[#00F0FF] outline-none font-mono placeholder:text-slate-500"
          />
        </div>
      </div>

      {/* Workflow Table with Staggered Row Animation */}
      <div className="bg-[#11141A] rounded-xl border border-white/[0.08] overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-white/[0.08] bg-[#0E1015] text-[11px] font-mono text-slate-400 uppercase tracking-wider">
                <th className="py-3 px-4">Workflow ID</th>
                <th className="py-3 px-4">Objective / Prompt</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Task Progress</th>
                <th className="py-3 px-4">Latency</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <motion.tbody
              variants={containerVariants}
              initial="hidden"
              animate="show"
              className="divide-y divide-white/[0.05] text-xs"
            >
              {filteredWorkflows.map((wf) => {
                const pct = Math.round((wf.completed_tasks / wf.tasks_count) * 100);
                return (
                  <motion.tr
                    key={wf.workflow_id}
                    variants={itemVariants}
                    onClick={() => onSelectWorkflow(wf.workflow_id)}
                    className="hover:bg-white/[0.02] cursor-pointer transition-colors group"
                  >
                    <td className="py-3.5 px-4 font-mono font-semibold text-white whitespace-nowrap">
                      <div className="flex items-center gap-2">
                        <span className="text-[#00F0FF]">{wf.workflow_id}</span>
                        {wf.checkpoint_id && (
                          <span className="text-[10px] text-slate-500 font-normal">
                            ({wf.checkpoint_id.slice(0, 10)}...)
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="py-3.5 px-4 text-slate-300 max-w-md truncate">
                      {wf.request_prompt}
                    </td>

                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <StatusBadge status={wf.status} size="sm" />
                    </td>

                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <div className="w-36 space-y-1">
                        <div className="flex items-center justify-between text-[11px] font-mono text-slate-400">
                          <span>
                            {wf.completed_tasks}/{wf.tasks_count} steps
                          </span>
                          <span>{pct}%</span>
                        </div>
                        <div className="w-full bg-[#1D222D] h-1.5 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${
                              wf.status === 'failed'
                                ? 'bg-rose-500'
                                : wf.status === 'interrupted'
                                ? 'bg-amber-400'
                                : wf.status === 'running'
                                ? 'bg-[#00F0FF]'
                                : 'bg-emerald-400'
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </div>
                    </td>

                    <td className="py-3.5 px-4 font-mono text-slate-400 whitespace-nowrap">
                      <div className="flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-slate-500" />
                        <span>{(wf.duration_ms / 1000).toFixed(1)}s</span>
                      </div>
                    </td>

                    <td className="py-3.5 px-4 text-right whitespace-nowrap">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onSelectWorkflow(wf.workflow_id);
                        }}
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-[#161A22] text-xs font-mono text-slate-300 group-hover:text-[#00F0FF] group-hover:border-[#00F0FF]/40 border border-white/[0.08] transition-all"
                      >
                        <GitBranch className="w-3 h-3" />
                        <span>View DAG</span>
                        <ArrowRight className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
                      </button>
                    </td>
                  </motion.tr>
                );
              })}
            </motion.tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
