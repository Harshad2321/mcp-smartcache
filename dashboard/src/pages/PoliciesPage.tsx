import React, { useState } from 'react';
import { MOCK_POLICIES } from '../mock/fixtures';
import { TenantPolicyItem } from '../types';
import {
  RotateCcw,
  Lock,
  GitCommit,
  User,
} from 'lucide-react';

export const PoliciesPage: React.FC = () => {
  const [policies, setPolicies] = useState<TenantPolicyItem[]>(MOCK_POLICIES);
  const [activePolicyId, setActivePolicyId] = useState<string>(policies[0].policy_id);

  const activePolicy = policies.find((p) => p.policy_id === activePolicyId) || policies[0];

  const handleRollback = (targetPolicyId: string) => {
    setPolicies((prev) =>
      prev.map((p) => ({
        ...p,
        is_active: p.policy_id === targetPolicyId,
      }))
    );
    setActivePolicyId(targetPolicyId);
  };

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold text-white tracking-tight">Tenant Policy & Governance</h2>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-emerald-950/40 text-emerald-300 border border-emerald-500/30">
              v{activePolicy.version} Active
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Cryptographically signed tenant governance rules governing tool access control, rate quotas, and cache invalidation.
          </p>
        </div>
      </div>

      {/* Main Grid: Effective Policy Clean Summary vs Git-Log Version History */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Readable Policy Card */}
        <div className="lg:col-span-2 space-y-5">
          {/* Quotas & Limits Row */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="bg-[#11141A] p-4 rounded-xl border border-white/[0.08]">
              <span className="text-[11px] font-mono text-slate-400">Rate Limit Quota</span>
              <div className="text-2xl font-bold font-mono text-white mt-1">
                {activePolicy.rpm_limit} <span className="text-xs text-slate-400 font-normal">RPM</span>
              </div>
              <span className="text-[10px] font-mono text-[#00F0FF]">Burst limit: {activePolicy.burst_limit}</span>
            </div>

            <div className="bg-[#11141A] p-4 rounded-xl border border-white/[0.08]">
              <span className="text-[11px] font-mono text-slate-400">Default Cache TTL</span>
              <div className="text-2xl font-bold font-mono text-white mt-1">
                {activePolicy.cache_default_ttl} <span className="text-xs text-slate-400 font-normal">sec</span>
              </div>
              <span className="text-[10px] font-mono text-emerald-400">Redis auto-expiry</span>
            </div>

            <div className="bg-[#11141A] p-4 rounded-xl border border-white/[0.08]">
              <span className="text-[11px] font-mono text-slate-400">Concurrent Workflows</span>
              <div className="text-2xl font-bold font-mono text-white mt-1">
                {activePolicy.max_concurrent_workflows}
              </div>
              <span className="text-[10px] font-mono text-violet-400">Queue capacity</span>
            </div>
          </div>

          {/* Access Control Rules (Tools Allowlist & Denylist) */}
          <div className="bg-[#11141A] rounded-xl border border-white/[0.08] p-5 space-y-4">
            <div className="flex items-center justify-between pb-3 border-b border-white/[0.06]">
              <h3 className="text-xs font-semibold text-white flex items-center gap-2">
                <Lock className="w-4 h-4 text-[#00F0FF]" />
                <span>Tool Access Control Matrix (ACL)</span>
              </h3>
              <span className="text-[11px] font-mono text-slate-400">Explicit Allowlist</span>
            </div>

            <div className="space-y-3">
              <div>
                <span className="text-[11px] font-mono text-emerald-400 font-semibold uppercase tracking-wider block mb-1.5">
                  ✓ Permitted Tool Patterns
                </span>
                <div className="flex flex-wrap gap-2">
                  {activePolicy.allowed_tools.map((t) => (
                    <span
                      key={t}
                      className="px-2.5 py-1 rounded-lg text-xs font-mono font-semibold bg-emerald-950/30 text-emerald-300 border border-emerald-500/30"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <span className="text-[11px] font-mono text-rose-400 font-semibold uppercase tracking-wider block mb-1.5">
                  ✗ Explicitly Denied Tool Patterns
                </span>
                <div className="flex flex-wrap gap-2">
                  {activePolicy.denied_tools.map((t) => (
                    <span
                      key={t}
                      className="px-2.5 py-1 rounded-lg text-xs font-mono font-semibold bg-rose-950/30 text-rose-300 border border-rose-500/30"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Right Col: Git-Log Style Version History */}
        <div className="bg-[#11141A] rounded-xl border border-white/[0.08] p-5 space-y-4">
          <div className="flex items-center justify-between pb-3 border-b border-white/[0.06]">
            <h3 className="text-xs font-semibold text-white flex items-center gap-1.5">
              <GitCommit className="w-4 h-4 text-[#00F0FF]" />
              <span>Immutable Version Log</span>
            </h3>
            <span className="text-[11px] font-mono text-slate-500">{policies.length} Versions</span>
          </div>

          <div className="space-y-4">
            {policies.map((p) => {
              const isActive = p.is_active;
              return (
                <div
                  key={p.policy_id}
                  className={`p-3.5 rounded-xl border transition-all ${
                    isActive
                      ? 'bg-[#161A22] border-[#00F0FF]/40 shadow-[0_0_15px_rgba(0,240,255,0.15)]'
                      : 'bg-[#161A22]/50 border-white/[0.06] hover:border-white/[0.15]'
                  }`}
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-1.5 font-mono text-xs font-bold text-white">
                      <span>v{p.version}</span>
                      <span className="text-slate-500">&bull;</span>
                      <span className="text-[#00F0FF]">{p.policy_id}</span>
                    </div>
                    {isActive ? (
                      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-emerald-950 text-emerald-300 border border-emerald-500/30">
                        ACTIVE
                      </span>
                    ) : (
                      <button
                        onClick={() => handleRollback(p.policy_id)}
                        className="text-[10px] font-mono text-slate-400 hover:text-white flex items-center gap-1 px-1.5 py-0.5 rounded bg-white/[0.06] hover:bg-white/[0.1]"
                      >
                        <RotateCcw className="w-2.5 h-2.5" />
                        <span>Rollback</span>
                      </button>
                    )}
                  </div>

                  <p className="text-xs text-slate-300 mb-2">{p.change_summary}</p>

                  <div className="flex items-center justify-between text-[10px] font-mono text-slate-500 pt-2 border-t border-white/[0.05]">
                    <span className="flex items-center gap-1">
                      <User className="w-3 h-3" />
                      {p.author.split('@')[0]}
                    </span>
                    <span>{new Date(p.created_at).toLocaleDateString()}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
