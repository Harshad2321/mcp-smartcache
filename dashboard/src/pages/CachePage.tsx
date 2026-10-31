import React, { useState } from 'react';
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, CartesianGrid } from 'recharts';
import { MOCK_CACHE_METRICS, MOCK_SPARKLINE } from '../mock/fixtures';
import { AnimatedCounter } from '../components/AnimatedCounter';
import {
  Zap,
  Cpu,
  Layers,
  Play,
  Database,
  Clock,
  Sparkles,
} from 'lucide-react';

export const CachePage: React.FC = () => {
  const [demoState, setDemoState] = useState<'idle' | 'running_cold' | 'cold_done' | 'running_warm' | 'warm_done'>('idle');
  const [coldLatency, setColdLatency] = useState<number>(642);
  const [warmLatency, setWarmLatency] = useState<number>(3.8);

  const handleRunPacketAnimation = async () => {
    setDemoState('running_cold');
    // Step 1: Cold miss traveling upstream
    await new Promise((r) => setTimeout(r, 800));
    setColdLatency(628 + Math.floor(Math.random() * 25));
    setDemoState('cold_done');

    await new Promise((r) => setTimeout(r, 500));

    // Step 2: Warm hit traveling to Redis only
    setDemoState('running_warm');
    await new Promise((r) => setTimeout(r, 400));
    setWarmLatency(3.4 + Number((Math.random() * 0.7).toFixed(1)));
    setDemoState('warm_done');
  };

  const latencyReduction = (((coldLatency - warmLatency) / coldLatency) * 100).toFixed(1);
  const isRunning = demoState === 'running_cold' || demoState === 'running_warm';

  return (
    <div className="p-6 lg:p-8 max-w-7xl mx-auto w-full min-w-0 space-y-6">
      {/* Workload Telemetry Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 bg-[#11141A] rounded-xl border border-white/[0.08]">
        <div className="flex items-center gap-2.5">
          <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-[#00F0FF]/15 text-[#00F0FF] border border-[#00F0FF]/30 uppercase tracking-wider">
            Distributed Workload Telemetry
          </span>
          <span className="text-xs text-slate-300 font-medium">
            50 Concurrent Ingress Requests &bull; Singleflight Coalescing
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs font-mono text-slate-400">
          <span>Engine: Redis 7.2 L2 &bull; Key: SHA-256</span>
        </div>
      </div>

      {/* 4 Core Infrastructure KPI Cards with Explicit Units */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Metric 1: Cache Hit Ratio */}
        <div className="bg-[#11141A] p-5 rounded-xl border border-white/[0.08] pro-card-hover flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Cache Hit Ratio</span>
            <Zap className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-3xl font-bold font-mono text-white tracking-tight">
            <AnimatedCounter value={MOCK_CACHE_METRICS.hit_ratio} decimals={1} suffix="%" />
          </div>
          <div className="mt-3 text-xs font-mono text-emerald-400 flex items-center gap-1">
            <span>↑ 99.4% Latency Drop</span>
          </div>
        </div>

        {/* Metric 2: Singleflight Waiters */}
        <div className="bg-[#11141A] p-5 rounded-xl border border-white/[0.08] pro-card-hover flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Singleflight Waiters</span>
            <Layers className="w-4 h-4 text-[#00F0FF]" />
          </div>
          <div className="text-3xl font-bold font-mono text-white tracking-tight">
            <AnimatedCounter value={MOCK_CACHE_METRICS.waiters_total} />
          </div>
          <div className="mt-3 text-xs font-mono text-slate-400">
            Deduplicated upstream calls
          </div>
        </div>

        {/* Metric 3: Total Redis L2 Hits */}
        <div className="bg-[#11141A] p-5 rounded-xl border border-white/[0.08] pro-card-hover flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Redis L2 Hits</span>
            <Database className="w-4 h-4 text-sky-400" />
          </div>
          <div className="text-3xl font-bold font-mono text-white tracking-tight">
            <AnimatedCounter value={MOCK_CACHE_METRICS.hits} />
          </div>
          <div className="mt-3 text-xs font-mono text-slate-400">
            Avg warm latency: 3.8 ms
          </div>
        </div>

        {/* Metric 4: Avoided Compute Time */}
        <div className="bg-[#11141A] p-5 rounded-xl border border-white/[0.08] pro-card-hover flex flex-col justify-between">
          <div className="flex items-center justify-between text-xs text-slate-400 mb-2">
            <span>Avoided Compute Time</span>
            <Clock className="w-4 h-4 text-violet-400" />
          </div>
          <div className="text-3xl font-bold font-mono text-white tracking-tight">
            <AnimatedCounter value={Math.round(MOCK_CACHE_METRICS.coordination_savings_ms / 60000)} suffix=" mins" />
          </div>
          <div className="mt-3 text-xs font-mono text-slate-400">
            Zero redundant upstream load
          </div>
        </div>
      </div>

      {/* Flagship LinkedIn Asset: The Animated Packet-Pulse Visualizer */}
      <div className="bg-[#11141A] rounded-xl border border-white/[0.08] p-6 shadow-xl relative overflow-hidden">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
          <div>
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-[#00F0FF]" />
              <h3 className="text-sm font-bold text-white tracking-tight">
                Distributed Singleflight & Request Coalescing
              </h3>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Real-time visualization of concurrent request deduplication and sub-4ms memory-tier acceleration.
            </p>
          </div>

          <button
            onClick={handleRunPacketAnimation}
            disabled={isRunning}
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[#00F0FF] text-black font-semibold text-xs hover:bg-[#00F0FF]/90 transition-all shadow-[0_0_15px_rgba(0,240,255,0.35)] disabled:opacity-50 font-mono"
          >
            <Play className="w-3.5 h-3.5 fill-black" />
            <span>
              {demoState === 'running_cold'
                ? 'Routing Cold Miss...'
                : demoState === 'running_warm'
                ? 'Resolving Cache Hit...'
                : 'Run Live Workload'}
            </span>
          </button>
        </div>

        {/* Architectural Flow Details */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-6">
          <div className="bg-[#161A22] p-3.5 rounded-lg border border-white/[0.06] text-xs">
            <span className="font-mono text-amber-400 font-semibold block mb-1">
              Cold Miss (Distributed Leader Execution)
            </span>
            <p className="text-slate-400 text-[11px] leading-relaxed">
              When a cache key is not found, Gateway acquires an atomic Redis lock (<code className="text-slate-300">SET NX</code>) and executes the upstream tool.
            </p>
          </div>
          <div className="bg-[#161A22] p-3.5 rounded-lg border border-white/[0.06] text-xs">
            <span className="font-mono text-emerald-400 font-semibold block mb-1">
              Warm Cache Hit (Redis L2 Retrieval)
            </span>
            <p className="text-slate-400 text-[11px] leading-relaxed">
              Canonical SHA-256 digest is matched in Redis. The cached response is fanned out in sub-5ms without touching upstream processes.
            </p>
          </div>
        </div>

        {/* Visual Request Flow Diagram */}
        <div className="relative py-8 px-4 bg-[#0B0D10] rounded-xl border border-white/[0.06] flex items-center justify-between max-w-4xl mx-auto overflow-hidden">
          {/* Node 1: AI Agent */}
          <div className="flex flex-col items-center gap-2 z-10 w-28 text-center">
            <div className="w-12 h-12 rounded-xl bg-[#161A22] border border-white/[0.1] flex items-center justify-center shadow-lg">
              <Cpu className="w-6 h-6 text-[#00F0FF]" />
            </div>
            <span className="text-xs font-semibold text-white">AI Agent</span>
            <span className="text-[10px] font-mono text-slate-500">Caller</span>
          </div>

          {/* Edge 1 to Gateway */}
          <div className="flex-1 h-0.5 bg-white/[0.1] relative mx-3">
            {isRunning && (
              <div className="absolute -top-1 w-2.5 h-2.5 rounded-full bg-[#00F0FF] shadow-[0_0_8px_#00F0FF] animate-pulse" />
            )}
          </div>

          {/* Node 2: Gateway */}
          <div className="flex flex-col items-center gap-2 z-10 w-28 text-center">
            <div className="w-12 h-12 rounded-xl bg-[#161A22] border border-white/[0.1] flex items-center justify-center shadow-lg">
              <Layers className="w-6 h-6 text-indigo-400" />
            </div>
            <span className="text-xs font-semibold text-white">MCP Gateway</span>
            <span className="text-[10px] font-mono text-[#00F0FF]">Singleflight</span>
          </div>

          {/* Edge 2 to Redis L2 */}
          <div className="flex-1 h-0.5 bg-white/[0.1] relative mx-3">
            {demoState === 'running_warm' && (
              <div className="absolute -top-1 w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-[0_0_8px_#10B981]" />
            )}
            {demoState === 'running_cold' && (
              <div className="absolute -top-1 w-2.5 h-2.5 rounded-full bg-amber-400 shadow-[0_0_8px_#F59E0B]" />
            )}
          </div>

          {/* Node 3: Redis L2 */}
          <div
            className={`flex flex-col items-center gap-2 z-10 w-28 text-center transition-all ${
              demoState === 'warm_done' ? 'scale-105' : ''
            }`}
          >
            <div
              className={`w-12 h-12 rounded-xl bg-[#161A22] border flex items-center justify-center shadow-lg transition-all ${
                demoState === 'warm_done'
                  ? 'border-emerald-400 shadow-[0_0_15px_rgba(16,185,129,0.4)]'
                  : 'border-white/[0.1]'
              }`}
            >
              <Zap className="w-6 h-6 text-emerald-400" />
            </div>
            <span className="text-xs font-semibold text-white">Redis L2 Cache</span>
            <span className="text-[10px] font-mono text-emerald-400">Warm Hit: ~3.8ms</span>
          </div>

          {/* Edge 3 to Upstream */}
          <div className="flex-1 h-0.5 bg-white/[0.1] relative mx-3">
            {demoState === 'running_cold' && (
              <div className="absolute -top-1 w-2.5 h-2.5 rounded-full bg-amber-400 shadow-[0_0_8px_#F59E0B]" />
            )}
          </div>

          {/* Node 4: Upstream Tool */}
          <div
            className={`flex flex-col items-center gap-2 z-10 w-28 text-center transition-all ${
              demoState === 'cold_done' ? 'scale-105' : ''
            }`}
          >
            <div
              className={`w-12 h-12 rounded-xl bg-[#161A22] border flex items-center justify-center shadow-lg transition-all ${
                demoState === 'cold_done'
                  ? 'border-amber-400 shadow-[0_0_15px_rgba(245,158,11,0.4)]'
                  : 'border-white/[0.1]'
              }`}
            >
              <Database className="w-6 h-6 text-amber-400" />
            </div>
            <span className="text-xs font-semibold text-white">Upstream MCP</span>
            <span className="text-[10px] font-mono text-amber-400">Cold Miss: ~640ms</span>
          </div>
        </div>

        {/* Latency Comparison Delta Banner */}
        <div className="mt-6 pt-6 border-t border-white/[0.08] grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-[#161A22] p-4 rounded-xl border border-white/[0.06] flex items-center justify-between">
            <div>
              <span className="text-[11px] font-mono text-amber-400 uppercase tracking-wider font-semibold">
                Request 1: Cold Miss (Upstream Leader)
              </span>
              <div className="text-2xl font-bold font-mono text-white mt-1">
                {coldLatency} ms
              </div>
              <p className="text-[11px] text-slate-400">Tool subprocess stdio execution latency</p>
            </div>
            <span className="px-2.5 py-1 rounded-full text-xs font-mono font-semibold bg-amber-950/40 text-amber-300 border border-amber-500/30">
              LEADER
            </span>
          </div>

          <div className="bg-[#161A22] p-4 rounded-xl border border-emerald-500/30 shadow-[0_0_20px_rgba(16,185,129,0.1)] flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-[11px] font-mono text-emerald-400 uppercase tracking-wider font-semibold">
                  Request 2: Warm Cache Hit (Redis L2)
                </span>
                <span className="text-[10px] font-mono text-black font-bold px-1.5 py-0.2 rounded bg-emerald-400">
                  {latencyReduction}% FASTER
                </span>
              </div>
              <div className="text-2xl font-bold font-mono text-emerald-300 mt-1">
                {warmLatency.toFixed(1)} ms
              </div>
              <p className="text-[11px] text-slate-400">Served directly from in-memory key</p>
            </div>
            <span className="px-2.5 py-1 rounded-full text-xs font-mono font-semibold bg-emerald-950/40 text-emerald-300 border border-emerald-500/30">
              CACHE HIT
            </span>
          </div>
        </div>
      </div>

      {/* Real-time Hit/Miss Throughput Area Chart with Explicit Axes */}
      <div className="bg-[#11141A] rounded-xl border border-white/[0.08] p-5 shadow-xl">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <div>
            <h4 className="text-xs font-semibold text-white">Throughput & Cache Hit Trajectory</h4>
            <p className="text-[11px] text-slate-400 font-mono">Real-time cache hit frequency vs miss rate over 1-hour window</p>
          </div>
          <div className="flex items-center gap-4 text-xs font-mono">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-emerald-400" />
              <span className="text-slate-300">Cache Hits (req/min)</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-sm bg-amber-400" />
              <span className="text-slate-300">Cache Misses (req/min)</span>
            </div>
          </div>
        </div>

        <div className="h-52 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={MOCK_SPARKLINE} margin={{ top: 10, right: 10, left: -10, bottom: 0 }}>
              <defs>
                <linearGradient id="hitsGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#10B981" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#10B981" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="missesGradient" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#F59E0B" stopOpacity={0.25} />
                  <stop offset="95%" stopColor="#F59E0B" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="time" stroke="#64748B" fontSize={11} fontFamily="JetBrains Mono" />
              <YAxis stroke="#64748B" fontSize={11} fontFamily="JetBrains Mono" unit=" r/m" />
              <Tooltip
                contentStyle={{ backgroundColor: '#161A22', borderColor: 'rgba(255,255,255,0.1)', borderRadius: 8 }}
                itemStyle={{ fontSize: 11, fontFamily: 'JetBrains Mono' }}
              />
              <Area type="monotone" dataKey="hits" stroke="#10B981" fill="url(#hitsGradient)" strokeWidth={2} />
              <Area type="monotone" dataKey="misses" stroke="#F59E0B" fill="url(#missesGradient)" strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};
