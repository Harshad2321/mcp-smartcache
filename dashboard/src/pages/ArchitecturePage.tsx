import React, { useState, useMemo, useCallback } from 'react';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  NodeProps,
  Edge,
  Node,
  MarkerType,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Server,
  Database,
  Cpu,
  Layers,
  Zap,
  Shield,
  Activity,
  Play,
  CheckCircle2,
  X,
  Code2,
  Lock,
} from 'lucide-react';

// ── Custom Node Component ───────────────────────────────────────────────────

interface CustomNodeData {
  title: string;
  subtitle: string;
  protocol: string;
  status: 'healthy' | 'running' | 'idle';
  icon: string;
  role: string;
  responsibilities: string[];
  port?: string;
  isHighlighted?: boolean;
}

const CustomArchitectureNode: React.FC<NodeProps<CustomNodeData>> = ({ data, selected }) => {
  const getIcon = (name: string) => {
    switch (name) {
      case 'gateway': return <Layers className="w-5 h-5 text-[#00F0FF]" />;
      case 'runtime': return <Cpu className="w-5 h-5 text-indigo-400" />;
      case 'control_api': return <Shield className="w-5 h-5 text-violet-400" />;
      case 'redis': return <Zap className="w-5 h-5 text-emerald-400" />;
      case 'postgres': return <Database className="w-5 h-5 text-sky-400" />;
      case 'servers': return <Server className="w-5 h-5 text-amber-400" />;
      default: return <Activity className="w-5 h-5 text-slate-400" />;
    }
  };

  const isHighlighted = data.isHighlighted;

  return (
    <div
      className={`w-72 rounded-xl p-4 transition-all duration-150 select-none cursor-pointer ${
        selected || isHighlighted
          ? 'bg-[#1D222D] border-2 border-[#00F0FF] shadow-[0_0_20px_rgba(0,240,255,0.25)]'
          : 'bg-[#161A22] border border-white/[0.08] hover:border-white/[0.2] hover:bg-[#1A1F29]'
      }`}
    >
      {/* Handles on all 4 sides for clean, non-crossing edge routing */}
      <Handle type="target" position={Position.Top} id="top-target" className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="source" position={Position.Bottom} id="bottom-source" className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="target" position={Position.Left} id="left-target" className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="source" position={Position.Right} id="right-source" className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="source" position={Position.Left} id="left-source" className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="target" position={Position.Right} id="right-target" className="!bg-[#00F0FF] !w-2 !h-2" />

      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-[#11141A] border border-white/[0.06] shrink-0">
            {getIcon(data.icon)}
          </div>
          <div className="min-w-0">
            <h4 className="text-xs font-semibold text-white tracking-tight truncate">{data.title}</h4>
            <p className="text-[11px] font-mono text-slate-400 truncate">{data.subtitle}</p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
          <span className="w-2 h-2 rounded-full bg-emerald-400" />
        </div>
      </div>

      <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center justify-between text-[11px] font-mono">
        <span className="px-2 py-0.5 rounded bg-white/[0.06] text-slate-300">
          {data.protocol}
        </span>
        {data.port && <span className="text-[#00F0FF] font-semibold">{data.port}</span>}
      </div>
    </div>
  );
};

// ── Strict Collision-Free Component Layout ──────────────────────────────────
// Architecture positions carefully tuned so arrows NEVER cross component cards.

const initialNodes: Node<CustomNodeData>[] = [
  {
    id: 'agent',
    type: 'archNode',
    position: { x: 40, y: 50 },
    data: {
      title: 'AI Agent & LLM Client',
      subtitle: 'Client / Caller Layer',
      protocol: 'MCP SSE / HTTP',
      status: 'healthy',
      icon: 'runtime',
      port: 'Client App',
      role: 'Client Requester',
      responsibilities: [
        'Standardized Model Context Protocol dispatching across client applications.',
        'Cryptographic identity verification and bearer token propagation.',
        'Explicit multi-tenant scoping and request isolation.',
      ],
    },
  },
  {
    id: 'gateway',
    type: 'archNode',
    position: { x: 420, y: 50 },
    data: {
      title: 'MCP SmartCache Gateway',
      subtitle: 'Ingress & Singleflight Core',
      protocol: 'FastAPI / SSE',
      status: 'healthy',
      icon: 'gateway',
      port: 'Port :8000',
      role: 'Ingress & Acceleration Core',
      responsibilities: [
        'Request validation and payload size enforcement at the edge.',
        'Enterprise identity authentication via OIDC JWT or high-speed API keys.',
        'Cryptographically isolated per-tenant caching.',
        'Distributed singleflight leader election preventing downstream cache stampedes.',
        'Comprehensive SSRF protection blocking private subnets and cloud metadata.',
      ],
    },
  },
  {
    id: 'redis',
    type: 'archNode',
    position: { x: 40, y: 320 },
    data: {
      title: 'Redis Distributed Cache',
      subtitle: 'Multi-Tenant L2 Storage',
      protocol: 'Redis Pub/Sub',
      status: 'healthy',
      icon: 'redis',
      port: 'Port :6379',
      role: 'L2 Memory & Pub/Sub',
      responsibilities: [
        'Sub-4ms warm cache retrieval cutting tool latency by over 99%.',
        'Distributed event coordination and singleflight notification channels.',
        'High-precision sliding-window rate limiting and quota enforcement.',
        'Tenant-isolated data lifecycle with automated TTL expiration.',
      ],
    },
  },
  {
    id: 'upstream',
    type: 'archNode',
    position: { x: 420, y: 320 },
    data: {
      title: 'Upstream MCP Servers',
      subtitle: 'Subprocess & SSE Hosts',
      protocol: 'stdio / Remote SSE',
      status: 'healthy',
      icon: 'servers',
      port: 'Isolated stdio',
      role: 'External Tool Execution',
      responsibilities: [
        'Sandboxed execution across isolated stdio subprocesses and remote SSE streams.',
        'Automated capability discovery conforming to the Model Context Protocol standard.',
        'Defensive response size bounds protecting runtime memory headroom.',
      ],
    },
  },
  {
    id: 'runtime',
    type: 'archNode',
    position: { x: 800, y: 50 },
    data: {
      title: 'Agent Workflow Runtime',
      subtitle: 'LangGraph State Engine',
      protocol: 'Python Async',
      status: 'healthy',
      icon: 'runtime',
      port: 'Port :8002',
      role: 'Durable Orchestration',
      responsibilities: [
        'Durable LangGraph state machine execution for multi-step agent plans.',
        'Parallel task dependency resolution with asynchronous scheduling.',
        'Automated transactional state checkpointing before mutating actions.',
        'Zero-loss workflow resumption from the latest verified checkpoint.',
      ],
    },
  },
  {
    id: 'control_api',
    type: 'archNode',
    position: { x: 800, y: 220 },
    data: {
      title: 'Control & Policy API',
      subtitle: 'Governance & Reconciliation',
      protocol: 'REST / OpenAPI',
      status: 'healthy',
      icon: 'control_api',
      port: 'Port :8080',
      role: 'Governance Plane',
      responsibilities: [
        'Tool access control matrix with strict tenant allowlists and denylists.',
        'Per-tenant rate quota governance and concurrent workflow caps.',
        'Human-in-the-Loop review gates for non-idempotent operations.',
        'Signed policy version registry with instant one-click rollback.',
      ],
    },
  },
  {
    id: 'postgres',
    type: 'archNode',
    position: { x: 800, y: 390 },
    data: {
      title: 'PostgreSQL Datastore',
      subtitle: 'Checkpoints & Audit Logs',
      protocol: 'asyncpg / SSL',
      status: 'healthy',
      icon: 'postgres',
      port: 'Port :5432',
      role: 'Relational ACID Store',
      responsibilities: [
        'ACID-compliant relational persistence with automated schema migrations.',
        'Durable state checkpointing enabling point-in-time recovery.',
        'Tamper-evident audit trail capturing all system events and policy revisions.',
        'Safety quarantine log preserving operator risk sign-offs.',
      ],
    },
  },
];

// ── Strict Semantic Edges with Clear Labels and Arrowheads ───────────────────

const initialEdges: Edge[] = [
  // 1. Client to Gateway: Ingress Tool Call
  {
    id: 'e-agent-gateway',
    source: 'agent',
    target: 'gateway',
    sourceHandle: 'right-source',
    targetHandle: 'left-target',
    animated: true,
    label: 'Tool Call / HTTP',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#00F0FF' },
    style: { stroke: '#00F0FF', strokeWidth: 2 },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
  // 2. Gateway to Redis: Cache & Singleflight
  {
    id: 'e-gateway-redis',
    source: 'gateway',
    target: 'redis',
    sourceHandle: 'bottom-source',
    targetHandle: 'top-target',
    animated: true,
    label: 'Cache / PubSub',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#10B981' },
    style: { stroke: '#10B981', strokeWidth: 2 },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
  // 3. Gateway to Upstream: Clean vertical straight line directly down
  {
    id: 'e-gateway-upstream',
    source: 'gateway',
    target: 'upstream',
    sourceHandle: 'bottom-source',
    targetHandle: 'top-target',
    animated: true,
    label: 'stdio Dispatch',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#F59E0B' },
    style: { stroke: '#F59E0B', strokeWidth: 2 },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
  // 4. Gateway to Runtime: Workflow Dispatch
  {
    id: 'e-gateway-runtime',
    source: 'gateway',
    target: 'runtime',
    sourceHandle: 'right-source',
    targetHandle: 'left-target',
    animated: true,
    label: 'Workflow Dispatch',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#818CF8' },
    style: { stroke: '#818CF8', strokeWidth: 1.5 },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
  // 5. Runtime to Postgres: Durable Checkpoint Persistence
  {
    id: 'e-runtime-postgres',
    source: 'runtime',
    target: 'postgres',
    sourceHandle: 'bottom-source',
    targetHandle: 'top-target',
    animated: true,
    label: 'Checkpoints',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#38BDF8' },
    style: { stroke: '#38BDF8', strokeWidth: 1.5, strokeDasharray: '4,4' },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
  // 6. Control API to Postgres: Policy Store
  {
    id: 'e-control-postgres',
    source: 'control_api',
    target: 'postgres',
    sourceHandle: 'bottom-source',
    targetHandle: 'top-target',
    animated: false,
    label: 'Policy Store',
    markerEnd: { type: MarkerType.ArrowClosed, color: '#C084FC' },
    style: { stroke: '#C084FC', strokeWidth: 1.5 },
    labelStyle: { fill: '#94A3B8', fontFamily: 'JetBrains Mono', fontSize: 10 },
  },
];

// ── Interactive Guided Walkthrough Steps ────────────────────────────────────

const REQUEST_FLOW_STEPS = [
  {
    step: 1,
    title: 'Client Agent Tool Call',
    activeNode: 'agent',
    desc: 'Autonomous agent dispatches tool call repo_metadata__get_repo_info with Bearer token & X-Tenant-ID: tenant_a scope.',
  },
  {
    step: 2,
    title: 'Gateway Ingress & Policy Guard',
    activeNode: 'gateway',
    desc: 'Gateway validates token HMAC, parses tenant context, checks 1MB limit, and verifies tool permission against Tenant Policy.',
  },
  {
    step: 3,
    title: 'Distributed Singleflight & Cache Lookup',
    activeNode: 'redis',
    desc: 'Deterministic SHA-256 key computed. If cached, response returned in 3.8ms. If miss, node is elected Singleflight Leader via Redis SET NX.',
  },
  {
    step: 4,
    title: 'Isolated Subprocess Execution',
    activeNode: 'upstream',
    desc: 'Leader launches isolated subprocess via stdio. Concurrent waiters attach to Redis pub/sub channel instead of duplicating load.',
  },
  {
    step: 5,
    title: 'State Checkpointing & Response Delivery',
    activeNode: 'postgres',
    desc: 'Result cached in Redis L2. LangGraph state securely checkpointed to PostgreSQL, and validated response delivered to agent.',
  },
];

export const ArchitecturePage: React.FC = () => {
  const nodeTypes = useMemo(() => ({ archNode: CustomArchitectureNode }), []);
  const [nodes, setNodes] = useState<Node<CustomNodeData>[]>(initialNodes);
  const [edges] = useState<Edge[]>(initialEdges);
  const [selectedNode, setSelectedNode] = useState<CustomNodeData | null>(initialNodes[1].data); // Default to Gateway
  const [isWalkthroughActive, setIsWalkthroughActive] = useState<boolean>(false);
  const [currentStepIndex, setCurrentStepIndex] = useState<number>(0);

  const onNodeClick = useCallback((_: any, node: Node) => {
    setSelectedNode(node.data as CustomNodeData);
  }, []);

  const handleStartWalkthrough = () => {
    setIsWalkthroughActive(true);
    setCurrentStepIndex(0);
    highlightNode(REQUEST_FLOW_STEPS[0].activeNode);
  };

  const handleNextStep = () => {
    if (currentStepIndex < REQUEST_FLOW_STEPS.length - 1) {
      const nextIndex = currentStepIndex + 1;
      setCurrentStepIndex(nextIndex);
      highlightNode(REQUEST_FLOW_STEPS[nextIndex].activeNode);
    } else {
      setIsWalkthroughActive(false);
      clearHighlight();
    }
  };

  const highlightNode = (nodeId: string) => {
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isHighlighted: n.id === nodeId,
        },
      }))
    );
    const target = nodes.find((n) => n.id === nodeId);
    if (target) setSelectedNode(target.data);
  };

  const clearHighlight = () => {
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isHighlighted: false,
        },
      }))
    );
  };

  return (
    <div className="flex-1 flex flex-col h-[calc(100vh-4.75rem)] overflow-hidden infra-grid-bg relative min-w-0">
      {/* Top Floating Control Bar */}
      <div className="absolute top-4 left-6 z-10 flex items-center gap-3 flex-wrap">
        <div className="bg-[#11141A]/95 backdrop-blur-md px-3.5 py-2 rounded-xl border border-white/[0.08] flex items-center gap-3 shadow-lg">
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-[#00F0FF] animate-pulse" />
            <span className="text-xs font-semibold text-white tracking-tight">System Topology</span>
          </div>
          <span className="text-slate-600">|</span>
          <span className="text-[11px] font-mono text-slate-400">Real-time visualization of request flow across gateway, cache, and runtime layers.</span>
        </div>

        <button
          onClick={isWalkthroughActive ? () => setIsWalkthroughActive(false) : handleStartWalkthrough}
          className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-semibold transition-all shadow-md font-mono ${
            isWalkthroughActive
              ? 'bg-[#00F0FF] text-black shadow-[0_0_15px_rgba(0,240,255,0.4)]'
              : 'bg-[#161A22] text-slate-300 border border-white/[0.08] hover:border-white/[0.2] hover:text-white'
          }`}
        >
          <Play className="w-3.5 h-3.5 fill-current" />
          <span>{isWalkthroughActive ? 'Exit Walkthrough' : 'Interactive Request Flow'}</span>
        </button>
      </div>

      {/* Guided Walkthrough Step Banner */}
      <AnimatePresence>
        {isWalkthroughActive && (
          <motion.div
            initial={{ opacity: 0, y: -16 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -16 }}
            className="absolute top-16 left-6 right-84 z-10 bg-[#161A22]/95 backdrop-blur-md p-4 rounded-xl border border-[#00F0FF]/40 shadow-[0_10px_30px_rgba(0,0,0,0.6)] flex items-center justify-between gap-4"
          >
            <div className="flex items-center gap-3 min-w-0">
              <span className="w-7 h-7 rounded-lg bg-[#00F0FF] text-black font-bold flex items-center justify-center text-xs font-mono shrink-0">
                {REQUEST_FLOW_STEPS[currentStepIndex].step}
              </span>
              <div className="min-w-0">
                <h4 className="text-xs font-semibold text-white tracking-tight">
                  {REQUEST_FLOW_STEPS[currentStepIndex].title}
                </h4>
                <p className="text-[11px] text-slate-300 truncate mt-0.5">
                  {REQUEST_FLOW_STEPS[currentStepIndex].desc}
                </p>
              </div>
            </div>

            <div className="flex items-center gap-3 shrink-0">
              <span className="text-[11px] font-mono text-slate-400">
                Step {currentStepIndex + 1} of {REQUEST_FLOW_STEPS.length}
              </span>
              <button
                onClick={handleNextStep}
                className="px-3 py-1.5 bg-[#00F0FF] text-black font-semibold text-xs rounded-lg hover:bg-[#00F0FF]/90 transition-all font-mono"
              >
                {currentStepIndex === REQUEST_FLOW_STEPS.length - 1 ? 'Finish Flow' : 'Next Step →'}
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* React Flow Canvas */}
      <div className="flex-1 w-full h-full">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.4}
          maxZoom={1.4}
        >
          <Background color="#1D222D" gap={24} size={1} />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>
      </div>

      {/* Slide-over Right Component Inspector Panel */}
      <AnimatePresence>
        {selectedNode && (
          <motion.div
            initial={{ opacity: 0, x: 320 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 320 }}
            transition={{ type: 'spring', stiffness: 350, damping: 30 }}
            className="absolute top-0 right-0 w-84 h-full bg-[#11141A]/95 backdrop-blur-xl border-l border-white/[0.08] p-5 flex flex-col z-20 shadow-2xl overflow-y-auto"
          >
            <div className="flex items-center justify-between pb-4 border-b border-white/[0.08]">
              <div>
                <span className="text-[10px] font-mono text-[#00F0FF] uppercase tracking-wider font-semibold">
                  Component Inspector
                </span>
                <h3 className="text-sm font-bold text-white tracking-tight">{selectedNode.title}</h3>
                <p className="text-[11px] font-mono text-slate-400">{selectedNode.role}</p>
              </div>
              <button
                onClick={() => setSelectedNode(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.06]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="mt-4 space-y-4 flex-1 text-xs">
              {/* Protocol & Port */}
              <div className="bg-[#161A22] p-3 rounded-lg border border-white/[0.06] space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Protocol Interface:</span>
                  <span className="font-mono text-white font-semibold">{selectedNode.protocol}</span>
                </div>
                {selectedNode.port && (
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Port / Binding:</span>
                    <span className="font-mono text-[#00F0FF]">{selectedNode.port}</span>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Health Status:</span>
                  <span className="font-mono text-emerald-400 flex items-center gap-1">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Operational
                  </span>
                </div>
              </div>

              {/* Core Capabilities */}
              <div>
                <h4 className="text-xs font-semibold text-white mb-2 flex items-center gap-1.5">
                  <Code2 className="w-3.5 h-3.5 text-[#00F0FF]" />
                  <span>Core Capabilities</span>
                </h4>
                <ul className="space-y-2">
                  {selectedNode.responsibilities.map((resp, i) => (
                    <li
                      key={i}
                      className="text-xs text-slate-300 bg-[#161A22] p-2.5 rounded-lg border border-white/[0.05] leading-relaxed flex items-start gap-2"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-[#00F0FF] mt-1.5 shrink-0" />
                      <span>{resp}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* Security Boundary Specs */}
              <div className="p-3 rounded-lg bg-[#161A22] border border-white/[0.06] space-y-1.5">
                <div className="flex items-center gap-1.5 font-semibold text-slate-200">
                  <Lock className="w-3.5 h-3.5 text-amber-400" />
                  <span>Security & Boundary Controls</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  Multi-tenant cryptographic data isolation, comprehensive SSRF protection, strict payload size verification, and constant-time authentication safeguards.
                </p>
              </div>
            </div>

            <div className="pt-4 border-t border-white/[0.08] text-center">
              <span className="text-[10px] font-mono text-slate-500 uppercase tracking-wider">
                Enterprise-Grade Architecture &bull; SOC2-Ready Security
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
