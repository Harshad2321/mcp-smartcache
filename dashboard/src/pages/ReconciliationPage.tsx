import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MOCK_RECONCILIATIONS } from '../mock/fixtures';
import { ReconciliationItem } from '../types';
import {
  AlertTriangle,
  ShieldAlert,
  X,
} from 'lucide-react';

export const ReconciliationPage: React.FC = () => {
  const [reconciliations, setReconciliations] = useState<ReconciliationItem[]>(MOCK_RECONCILIATIONS);
  const [selectedCase, setSelectedCase] = useState<ReconciliationItem | null>(reconciliations[0]);
  const [riskAcknowledged, setRiskAcknowledged] = useState<boolean>(false);
  const [decisionNotes, setDecisionNotes] = useState<string>('');
  const [decisionAction, setDecisionAction] = useState<'approve_retry' | 'skip_task' | 'abort_workflow'>('approve_retry');
  const [bannerNotice, setBannerNotice] = useState<string | null>(null);

  const pendingCases = reconciliations.filter((r) => r.status === 'pending');

  const handleSubmitDecision = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedCase) return;

    if (decisionAction === 'approve_retry' && !riskAcknowledged) {
      alert('You must check the risk acknowledgment checkbox before approving retry of a high-risk mutation.');
      return;
    }

    setReconciliations((prev) =>
      prev.map((c) =>
        c.id === selectedCase.id
          ? {
              ...c,
              status: decisionAction === 'approve_retry' ? 'approved_retry' : decisionAction === 'skip_task' ? 'skipped' : 'aborted',
            }
          : c
      )
    );

    setBannerNotice(`Decision '${decisionAction}' recorded for case ${selectedCase.id}. Idempotency key locked.`);
    setSelectedCase(null);
    setRiskAcknowledged(false);
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold text-white tracking-tight">
              Human-in-the-Loop (HITL) Safety Reconciliation
            </h2>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-amber-950/40 text-amber-300 border border-amber-500/30">
              {pendingCases.length} Pending Intervention
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Automated quarantine and safety approval gateway for workflows requiring explicit human confirmation during non-idempotent mutations.
          </p>
        </div>
      </div>

      {bannerNotice && (
        <div className="p-3 bg-emerald-950/40 border border-emerald-500/40 rounded-xl text-xs font-mono text-emerald-300 flex items-center justify-between">
          <span>✓ {bannerNotice}</span>
          <button onClick={() => setBannerNotice(null)} className="text-slate-400 hover:text-white">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Quarantined Cases Table */}
      <div className="bg-[#11141A] rounded-xl border border-white/[0.08] overflow-hidden shadow-xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="border-b border-white/[0.08] bg-[#0E1015] text-[11px] font-mono text-slate-400 uppercase tracking-wider">
                <th className="py-3 px-4">Case ID</th>
                <th className="py-3 px-4">Workflow & Task</th>
                <th className="py-3 px-4">Tool Namespace</th>
                <th className="py-3 px-4">Class</th>
                <th className="py-3 px-4">Reported Outcome</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.05] text-xs">
              {reconciliations.map((item) => (
                <tr
                  key={item.id}
                  onClick={() => setSelectedCase(item)}
                  className="hover:bg-white/[0.02] cursor-pointer transition-colors group"
                >
                  <td className="py-3 px-4 font-mono font-semibold text-white">
                    <span className="text-[#00F0FF]">{item.id}</span>
                  </td>

                  <td className="py-3 px-4 font-mono text-slate-300">
                    <div>{item.workflow_id}</div>
                    <div className="text-[11px] text-slate-500">{item.task_id}</div>
                  </td>

                  <td className="py-3 px-4 font-mono text-slate-300">
                    {item.tool_name}
                  </td>

                  <td className="py-3 px-4">
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-violet-950/40 text-violet-300 border border-violet-500/30 uppercase">
                      {item.operation_class}
                    </span>
                  </td>

                  <td className="py-3 px-4">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase ${
                        item.known_outcome === 'unknown'
                          ? 'bg-rose-950/50 text-rose-300 border border-rose-500/40 animate-pulse'
                          : 'bg-amber-950/40 text-amber-300 border border-amber-500/30'
                      }`}
                    >
                      {item.known_outcome}
                    </span>
                  </td>

                  <td className="py-3 px-4">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold uppercase ${
                        item.status === 'pending'
                          ? 'bg-amber-950/40 text-amber-300 border border-amber-500/30'
                          : 'bg-emerald-950/40 text-emerald-300 border border-emerald-500/30'
                      }`}
                    >
                      {item.status}
                    </span>
                  </td>

                  <td className="py-3 px-4 text-right">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setSelectedCase(item);
                      }}
                      className="px-2.5 py-1 rounded-lg bg-[#161A22] border border-white/[0.08] group-hover:border-[#00F0FF]/40 text-xs font-mono text-slate-300 group-hover:text-[#00F0FF] transition-all"
                    >
                      Review & Decide →
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Slide-over Right Panel: Serious Fintech Treatment */}
      <AnimatePresence>
        {selectedCase && (
          <motion.div
            initial={{ opacity: 0, x: 380 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 380 }}
            transition={{ type: 'spring', stiffness: 350, damping: 30 }}
            className="fixed top-0 right-0 w-[420px] h-full bg-[#11141A]/98 backdrop-blur-2xl border-l border-white/[0.08] p-6 flex flex-col z-50 shadow-2xl overflow-y-auto"
          >
            <div className="flex items-center justify-between pb-4 border-b border-white/[0.08]">
              <div className="flex items-center gap-2">
                <ShieldAlert className="w-5 h-5 text-amber-400" />
                <div>
                  <h3 className="text-sm font-bold text-white tracking-tight">
                    Quarantine Decision Gate
                  </h3>
                  <span className="text-[10px] font-mono text-slate-400">Case ID: {selectedCase.id}</span>
                </div>
              </div>
              <button
                onClick={() => setSelectedCase(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.06]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleSubmitDecision} className="mt-4 space-y-4 flex-1">
              {/* High-Risk Fintech Warning Box */}
              <div className="fintech-risk-panel p-4 rounded-xl space-y-2">
                <div className="flex items-center gap-2 text-xs font-bold text-amber-300 uppercase tracking-wider">
                  <AlertTriangle className="w-4 h-4 text-amber-400" />
                  <span>Ambiguous State: {selectedCase.known_outcome}</span>
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">
                  {selectedCase.reason}
                </p>
                <div className="text-[10px] font-mono text-slate-400 pt-1 border-t border-amber-500/20">
                  Target Operation: <span className="text-white font-bold">{selectedCase.tool_name}</span> ({selectedCase.operation_class})
                </div>
              </div>

              {/* Raw Quarantined Payload */}
              <div>
                <span className="text-xs font-semibold text-white mb-1.5 block">
                  Intercepted Transaction Payload
                </span>
                <div className="bg-[#0B0D10] p-3 rounded-lg border border-white/[0.08] font-mono text-[11px] text-amber-300 max-h-36 overflow-y-auto">
                  <pre>{JSON.stringify(selectedCase.raw_payload, null, 2)}</pre>
                </div>
              </div>

              {/* Authoritative Decision Action Selector */}
              <div className="space-y-2">
                <label className="text-xs font-semibold text-white block">Authoritative Decision</label>
                <div className="space-y-2">
                  <label className="flex items-start gap-2.5 p-2.5 rounded-lg bg-[#161A22] border border-white/[0.08] cursor-pointer hover:border-white/[0.2]">
                    <input
                      type="radio"
                      name="decisionAction"
                      value="approve_retry"
                      checked={decisionAction === 'approve_retry'}
                      onChange={() => setDecisionAction('approve_retry')}
                      className="mt-0.5"
                    />
                    <div>
                      <div className="text-xs font-semibold text-white">Approve Replay Execution</div>
                      <div className="text-[11px] text-slate-400">
                        Operator verified transaction was NOT committed upstream. Safe to retry.
                      </div>
                    </div>
                  </label>

                  <label className="flex items-start gap-2.5 p-2.5 rounded-lg bg-[#161A22] border border-white/[0.08] cursor-pointer hover:border-white/[0.2]">
                    <input
                      type="radio"
                      name="decisionAction"
                      value="skip_task"
                      checked={decisionAction === 'skip_task'}
                      onChange={() => setDecisionAction('skip_task')}
                      className="mt-0.5"
                    />
                    <div>
                      <div className="text-xs font-semibold text-white">Skip Task & Resume</div>
                      <div className="text-[11px] text-slate-400">
                        Effect was already applied upstream or task can be safely omitted.
                      </div>
                    </div>
                  </label>

                  <label className="flex items-start gap-2.5 p-2.5 rounded-lg bg-[#161A22] border border-white/[0.08] cursor-pointer hover:border-white/[0.2]">
                    <input
                      type="radio"
                      name="decisionAction"
                      value="abort_workflow"
                      checked={decisionAction === 'abort_workflow'}
                      onChange={() => setDecisionAction('abort_workflow')}
                      className="mt-0.5"
                    />
                    <div>
                      <div className="text-xs font-semibold text-rose-400">Permanently Abort</div>
                      <div className="text-[11px] text-slate-400">
                        Halt execution immediately to prevent further state corruption.
                      </div>
                    </div>
                  </label>
                </div>
              </div>

              {/* Mandatory Risk Acknowledgment Checkbox */}
              {decisionAction === 'approve_retry' && (
                <div className="p-3 rounded-lg bg-rose-950/20 border border-rose-500/30 space-y-2">
                  <label className="flex items-start gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={riskAcknowledged}
                      onChange={(e) => setRiskAcknowledged(e.target.checked)}
                      className="mt-0.5 accent-rose-500"
                    />
                    <span className="text-[11px] text-rose-300 font-medium leading-relaxed">
                      I acknowledge the risk of duplicate external mutation on an unknown-outcome transaction.
                    </span>
                  </label>
                </div>
              )}

              {/* Operator Decision Notes */}
              <div>
                <label className="text-xs font-semibold text-white block mb-1">
                  Operator Audit Reason
                </label>
                <input
                  type="text"
                  placeholder="e.g. Verified Stripe webhook logs confirmed no charge"
                  value={decisionNotes}
                  onChange={(e) => setDecisionNotes(e.target.value)}
                  className="w-full bg-[#161A22] text-xs text-white rounded-lg p-2.5 border border-white/[0.08] focus:border-[#00F0FF] outline-none font-mono"
                />
              </div>

              <div className="pt-4 border-t border-white/[0.08]">
                <button
                  type="submit"
                  disabled={selectedCase.status !== 'pending' || (decisionAction === 'approve_retry' && !riskAcknowledged)}
                  className="w-full py-2.5 rounded-xl bg-[#00F0FF] text-black font-semibold text-xs hover:bg-[#00F0FF]/90 transition-all disabled:opacity-40 font-mono shadow-[0_0_15px_rgba(0,240,255,0.35)]"
                >
                  Commit Authoritative Decision →
                </button>
              </div>
            </form>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
