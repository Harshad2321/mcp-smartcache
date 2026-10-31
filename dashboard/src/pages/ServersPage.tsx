import React, { useState } from 'react';
import { MOCK_SERVERS } from '../mock/fixtures';
import { UpstreamServerItem } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import {
  RefreshCw,
  Terminal,
  Globe,
  Check,
  ChevronDown,
} from 'lucide-react';

export const ServersPage: React.FC = () => {
  const [servers] = useState<UpstreamServerItem[]>(MOCK_SERVERS);
  const [refreshingId, setRefreshingId] = useState<string | null>(null);
  const [refreshedSuccessId, setRefreshedSuccessId] = useState<string | null>(null);
  const [expandedServerId, setExpandedServerId] = useState<string | null>(servers[0].id);

  const handleRefreshServer = async (id: string) => {
    setRefreshingId(id);
    await new Promise((r) => setTimeout(r, 650));
    setRefreshingId(null);
    setRefreshedSuccessId(id);
    setTimeout(() => setRefreshedSuccessId(null), 2000);
  };

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold text-white tracking-tight">
              Registered Upstream MCP Servers
            </h2>
            <span className="text-xs font-mono px-2 py-0.5 rounded bg-white/[0.06] text-slate-300 border border-white/[0.06]">
              {servers.length} Connected
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">
            Dynamic upstream Model Context Protocol hosts connected via stdio subprocesses and remote SSE streams.
          </p>
        </div>
      </div>

      {/* Server Cards List */}
      <div className="space-y-4">
        {servers.map((srv) => {
          const isExpanded = expandedServerId === srv.id;
          const isRefreshing = refreshingId === srv.id;
          const isRefreshed = refreshedSuccessId === srv.id;

          return (
            <div
              key={srv.id}
              className="bg-[#11141A] rounded-xl border border-white/[0.08] overflow-hidden transition-all pro-card-hover"
            >
              <div
                onClick={() => setExpandedServerId(isExpanded ? null : srv.id)}
                className="p-4 flex items-center justify-between gap-4 cursor-pointer select-none"
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-[#161A22] border border-white/[0.08] flex items-center justify-center">
                    {srv.transport === 'stdio' ? (
                      <Terminal className="w-5 h-5 text-[#00F0FF]" />
                    ) : (
                      <Globe className="w-5 h-5 text-indigo-400" />
                    )}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-bold text-white tracking-tight">{srv.name}</h3>
                      <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold uppercase bg-white/[0.06] text-slate-300 border border-white/[0.06]">
                        {srv.transport}
                      </span>
                    </div>
                    <p className="text-xs font-mono text-slate-400 truncate max-w-md mt-0.5">
                      {srv.command_or_url}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  <div className="hidden sm:flex items-center gap-3 text-xs font-mono text-slate-400">
                    <span>{srv.tools.length} Tools</span>
                    <span>&bull;</span>
                    <span className="text-emerald-400">{srv.uptime_pct}% Uptime</span>
                  </div>

                  <StatusBadge status={srv.status} size="sm" />

                  {/* Inline Refresh with Morphing Checkmark */}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      handleRefreshServer(srv.id);
                    }}
                    disabled={isRefreshing}
                    className="p-2 rounded-lg bg-[#161A22] border border-white/[0.08] text-slate-400 hover:text-white transition-all disabled:opacity-50"
                    title="Refresh tool schema catalog"
                  >
                    {isRefreshed ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? 'animate-spin text-[#00F0FF]' : ''}`} />
                    )}
                  </button>

                  <ChevronDown
                    className={`w-4 h-4 text-slate-500 transition-transform ${isExpanded ? 'rotate-180 text-white' : ''}`}
                  />
                </div>
              </div>

              {/* Collapsible Tool Catalog */}
              {isExpanded && (
                <div className="px-4 pb-4 pt-2 border-t border-white/[0.06] bg-[#0E1015]/60">
                  <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400 font-semibold block mb-2">
                    Discovered Namespaced Tools ({srv.tools.length})
                  </span>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
                    {srv.tools.map((t) => (
                      <div
                        key={t.name}
                        className="bg-[#161A22] p-3 rounded-lg border border-white/[0.06] flex flex-col justify-between"
                      >
                        <div>
                          <div className="flex items-center justify-between gap-1 mb-1">
                            <span className="text-xs font-mono font-semibold text-[#00F0FF] truncate">
                              {t.name}
                            </span>
                            {t.cached && (
                              <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-emerald-950/40 text-emerald-300 border border-emerald-500/30">
                                CACHED
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] text-slate-400 line-clamp-2">
                            {t.description}
                          </p>
                        </div>
                        <div className="mt-2 pt-2 border-t border-white/[0.05] flex items-center justify-between text-[10px] font-mono text-slate-500">
                          <span>{t.read_only ? 'Read-only' : 'Mutating tool'}</span>
                          <span className="text-slate-400">Strict schema</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};
