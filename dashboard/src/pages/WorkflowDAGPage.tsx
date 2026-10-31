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
import { DAGTaskNode } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { MOCK_DAG_TASKS, MOCK_WORKFLOWS } from '../mock/fixtures';
import {
  GitBranch,
  Clock,
  Terminal,
  CheckCircle2,
  X,
} from 'lucide-react';

// ── Custom Task Node Renderer ───────────────────────────────────────────────

const TaskNodeRenderer: React.FC<NodeProps<DAGTaskNode>> = ({ data, selected }) => {
  const isRunning = data.status === 'running';

  return (
    <div
      className={`w-72 rounded-xl p-3.5 transition-all select-none cursor-pointer ${
        selected
          ? 'bg-[#1D222D] border-2 border-[#00F0FF] shadow-[0_0_20px_rgba(0,240,255,0.3)]'
          : isRunning
          ? 'bg-[#161A22] border-2 border-[#00F0FF] animate-pulse-glow'
          : data.status === 'completed'
          ? 'bg-[#161A22] border border-emerald-500/30 hover:border-emerald-500/60'
          : 'bg-[#161A22] border border-white/[0.08] hover:border-white/[0.2] hover:bg-[#1A1F29]'
      }`}
    >
      <Handle type="target" position={Position.Top} className="!bg-[#00F0FF] !w-2 !h-2" />
      <Handle type="source" position={Position.Bottom} className="!bg-[#00F0FF] !w-2 !h-2" />

      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 truncate">
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-[10px] font-mono text-slate-400 truncate">
              {data.id}
            </span>
          </div>
          <h4 className="text-xs font-semibold text-white tracking-tight leading-snug">
            {data.label}
          </h4>
        </div>
        <StatusBadge status={data.status} size="sm" />
      </div>

      <div className="mt-2.5 pt-2 border-t border-white/[0.06] flex items-center justify-between text-[10px] font-mono">
        <span className="text-[#00F0FF] truncate max-w-[140px] bg-white/[0.04] px-1.5 py-0.5 rounded">
          {data.tool}
        </span>
        {data.duration_ms !== undefined ? (
          <span className="text-slate-400 flex items-center gap-1">
            <Clock className="w-3 h-3 text-slate-500" />
            {data.duration_ms}ms
          </span>
        ) : (
          <span className="text-slate-500 italic">Waiting...</span>
        )}
      </div>
    </div>
  );
};

// ── Symmetrical Non-Crossing DAG Coordinates ────────────────────────────────

const generateNodesAndEdges = (tasks: DAGTaskNode[]) => {
  const nodes: Node<DAGTaskNode>[] = [
    // 0. Root
    {
      id: tasks[0].id,
      type: 'dagTask',
      position: { x: 350, y: 30 },
      data: tasks[0],
    },
    // 1. Parallel Branch Left
    {
      id: tasks[1].id,
      type: 'dagTask',
      position: { x: 80, y: 180 },
      data: tasks[1],
    },
    // 2. Parallel Branch Right
    {
      id: tasks[2].id,
      type: 'dagTask',
      position: { x: 600, y: 180 },
      data: tasks[2],
    },
    // 3. Child of Branch Right
    {
      id: tasks[3].id,
      type: 'dagTask',
      position: { x: 600, y: 330 },
      data: tasks[3],
    },
    // 4. Convergence Node (Depends on Left Branch & Right Branch)
    {
      id: tasks[4].id,
      type: 'dagTask',
      position: { x: 350, y: 480 },
      data: tasks[4],
    },
    // 5. Final Sink Node
    {
      id: tasks[5].id,
      type: 'dagTask',
      position: { x: 350, y: 630 },
      data: tasks[5],
    },
  ];

  const edges: Edge[] = [];
  tasks.forEach((task) => {
    task.dependencies.forEach((dep) => {
      const strokeColor =
        task.status === 'completed'
          ? '#10B981'
          : task.status === 'running'
          ? '#00F0FF'
          : 'rgba(255,255,255,0.25)';

      edges.push({
        id: `e-${dep}-${task.id}`,
        source: dep,
        target: task.id,
        animated: task.status === 'running',
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: strokeColor,
          width: 14,
          height: 14,
        },
        style: {
          stroke: strokeColor,
          strokeWidth: task.status === 'running' ? 2.5 : 1.75,
        },
      });
    });
  });

  return { nodes, edges };
};

interface WorkflowDAGPageProps {
  workflowId?: string;
  onBackToWorkflows: () => void;
}

export const WorkflowDAGPage: React.FC<WorkflowDAGPageProps> = ({
  workflowId = 'wf_pr_audit_7f29a',
  onBackToWorkflows,
}) => {
  const nodeTypes = useMemo(() => ({ dagTask: TaskNodeRenderer }), []);
  const initial = useMemo(() => generateNodesAndEdges(MOCK_DAG_TASKS), []);
  const [nodes] = useState<Node<DAGTaskNode>[]>(initial.nodes);
  const [edges] = useState<Edge[]>(initial.edges);
  const [selectedTask, setSelectedTask] = useState<DAGTaskNode | null>(MOCK_DAG_TASKS[3]); // Default to running task

  const currentWf = MOCK_WORKFLOWS.find((w) => w.workflow_id === workflowId) || MOCK_WORKFLOWS[0];

  const onNodeClick = useCallback((_: any, node: Node) => {
    setSelectedTask(node.data as DAGTaskNode);
  }, []);

  return (
    <div className="flex-1 flex flex-col h-[calc(100vh-4.75rem)] overflow-hidden infra-grid-bg relative min-w-0">
      {/* Top Breadcrumb & Workflow Telemetry Bar */}
      <div className="absolute top-4 left-6 z-10 flex items-center gap-3 flex-wrap">
        <button
          onClick={onBackToWorkflows}
          className="bg-[#11141A]/95 backdrop-blur-md px-3.5 py-2 rounded-xl border border-white/[0.08] text-xs font-mono text-slate-300 hover:text-white hover:border-white/[0.2] transition-colors shadow-lg"
        >
          ← Back to Workflows
        </button>

        <div className="bg-[#11141A]/95 backdrop-blur-md px-4 py-2 rounded-xl border border-white/[0.08] flex items-center gap-3 shadow-lg">
          <div className="flex items-center gap-2">
            <GitBranch className="w-4 h-4 text-[#00F0FF]" />
            <span className="text-xs font-mono font-semibold text-white">{currentWf.workflow_id}</span>
          </div>
          <span className="text-slate-600">|</span>
          <StatusBadge status={currentWf.status} size="sm" />
          <span className="text-slate-600">|</span>
          <span className="text-[11px] font-mono text-slate-400">
            Checkpoint: <span className="text-white font-semibold">{currentWf.checkpoint_id}</span>
          </span>
        </div>
      </div>

      {/* React Flow DAG Canvas */}
      <div className="flex-1 w-full h-full">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.4}
          maxZoom={1.5}
        >
          <Background color="#1D222D" gap={24} size={1} />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>
      </div>

      {/* Slide-over Right Task Inspector Side Panel */}
      <AnimatePresence>
        {selectedTask && (
          <motion.div
            initial={{ opacity: 0, x: 340 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 340 }}
            transition={{ type: 'spring', stiffness: 350, damping: 30 }}
            className="absolute top-0 right-0 w-96 h-full bg-[#11141A]/95 backdrop-blur-xl border-l border-white/[0.08] p-5 flex flex-col z-20 shadow-2xl overflow-y-auto"
          >
            <div className="flex items-center justify-between pb-4 border-b border-white/[0.08]">
              <div>
                <span className="text-[10px] font-mono text-[#00F0FF] uppercase tracking-wider font-semibold">
                  Task Execution Inspector
                </span>
                <h3 className="text-sm font-bold text-white tracking-tight">{selectedTask.label}</h3>
                <p className="text-[11px] font-mono text-slate-400">{selectedTask.id}</p>
              </div>
              <button
                onClick={() => setSelectedTask(null)}
                className="p-1 rounded-lg text-slate-400 hover:text-white hover:bg-white/[0.06]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="mt-4 space-y-4 flex-1">
              {/* Task Status & Metadata */}
              <div className="bg-[#161A22] p-3.5 rounded-lg border border-white/[0.06] space-y-2 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Execution Status:</span>
                  <StatusBadge status={selectedTask.status} size="sm" />
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Target Tool:</span>
                  <span className="font-mono text-[#00F0FF] font-semibold">{selectedTask.tool}</span>
                </div>
                {selectedTask.duration_ms !== undefined && (
                  <div className="flex items-center justify-between">
                    <span className="text-slate-400">Duration:</span>
                    <span className="font-mono text-white font-semibold">{selectedTask.duration_ms} ms</span>
                  </div>
                )}
                <div className="flex items-center justify-between">
                  <span className="text-slate-400">Retries Attempted:</span>
                  <span className="font-mono text-slate-300">{selectedTask.retries} / 3</span>
                </div>
              </div>

              {/* Input Arguments Payload */}
              <div>
                <h4 className="text-xs font-semibold text-white mb-1.5 flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5 text-[#00F0FF]" />
                  <span>Input Arguments (Validated Schema)</span>
                </h4>
                <div className="bg-[#0B0D10] p-3 rounded-lg border border-white/[0.08] font-mono text-[11px] text-slate-300 overflow-x-auto max-h-40">
                  <pre>{JSON.stringify(selectedTask.input_args, null, 2)}</pre>
                </div>
              </div>

              {/* Output Result Payload */}
              {selectedTask.output_result ? (
                <div>
                  <h4 className="text-xs font-semibold text-white mb-1.5 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Output Result Payload</span>
                  </h4>
                  <div className="bg-[#0B0D10] p-3 rounded-lg border border-white/[0.08] font-mono text-[11px] text-emerald-300 overflow-x-auto max-h-48">
                    <pre>{JSON.stringify(selectedTask.output_result, null, 2)}</pre>
                  </div>
                </div>
              ) : (
                <div className="p-3 bg-[#161A22] rounded-lg border border-white/[0.05] text-xs text-slate-400 italic">
                  Task output pending execution completion.
                </div>
              )}

              {/* Predecessor Dependencies */}
              <div>
                <h4 className="text-xs font-semibold text-white mb-1.5">Predecessor Dependencies</h4>
                {selectedTask.dependencies.length === 0 ? (
                  <span className="text-xs text-slate-500 italic">None (Root Task)</span>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {selectedTask.dependencies.map((dep) => (
                      <span
                        key={dep}
                        className="text-[11px] font-mono px-2 py-0.5 rounded bg-white/[0.06] text-slate-300 border border-white/[0.06]"
                      >
                        {dep}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Quick Actions */}
            <div className="pt-4 border-t border-white/[0.08]">
              <span className="text-[10px] font-mono text-slate-500 block text-center">
                LangGraph State Machine &bull; PostgreSQL Checkpointed
              </span>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};
