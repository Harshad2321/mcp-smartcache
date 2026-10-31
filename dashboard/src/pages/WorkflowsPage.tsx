import React, { useState } from 'react';
import {
  Workflow,
  WorkflowTask,
  TraceEvent,
  WorkflowCheckpoint,
  WorkflowResumability,
} from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { TaskDAG } from '../components/TaskDAG';
import { SearchIcon, PlusIcon, CloseIcon } from '../components/Icons';

interface WorkflowsPageProps {
  workflows: Workflow[];
  selectedWorkflow: Workflow | null;
  tasks: WorkflowTask[];
  traceEvents: TraceEvent[];
  checkpoints: WorkflowCheckpoint[];
  resumability: WorkflowResumability | null;
  onSelectWorkflow: (workflowId: string | null) => void;
  onOpenSubmitModal: () => void;
  onResumeWorkflow: (workflowId: string) => void;
  onCancelWorkflow: (workflowId: string) => void;
  isResuming?: boolean;
}

export const WorkflowsPage: React.FC<WorkflowsPageProps> = ({
  workflows,
  selectedWorkflow,
  tasks,
  traceEvents,
  checkpoints,
  resumability,
  onSelectWorkflow,
  onOpenSubmitModal,
  onResumeWorkflow,
  onCancelWorkflow,
  isResuming = false,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedTask, setSelectedTask] = useState<WorkflowTask | null>(null);
  const [detailTab, setDetailTab] = useState<'dag' | 'tasks' | 'trace' | 'checkpoints'>('dag');

  const filteredWorkflows = workflows.filter((w) => {
    const matchesStatus =
      statusFilter === 'all' || w.status.toLowerCase() === statusFilter.toLowerCase();
    const query = searchQuery.toLowerCase().trim();
    const matchesQuery =
      !query ||
      w.workflow_id.toLowerCase().includes(query) ||
      (w.request_prompt && w.request_prompt.toLowerCase().includes(query));
    return matchesStatus && matchesQuery;
  });

  const statuses = ['all', 'running', 'completed', 'failed', 'interrupted', 'pending'];

  return (
    <div className="page-container">
      {/* If a workflow is selected, show detail view */}
      {selectedWorkflow ? (
        <div className="detail-view">
          {/* Detail Header */}
          <div className="detail-header">
            <div className="detail-header-left">
              <button
                type="button"
                className="btn-back"
                onClick={() => onSelectWorkflow(null)}
              >
                ← Back to Workflows
              </button>
              <div className="flex items-center gap-3 mt-2">
                <h2 className="detail-title">{selectedWorkflow.workflow_id}</h2>
                <StatusBadge status={selectedWorkflow.status} />
              </div>
              <p className="detail-prompt">
                {selectedWorkflow.request_prompt || 'No prompt provided'}
              </p>
            </div>

            <div className="detail-header-right">
              {/* Cancel Button if running */}
              {selectedWorkflow.status === 'running' && (
                <button
                  type="button"
                  className="btn-danger-outline"
                  onClick={() => onCancelWorkflow(selectedWorkflow.workflow_id)}
                >
                  Cancel Workflow
                </button>
              )}

              {/* Safe Resume Button if interrupted */}
              {selectedWorkflow.status === 'interrupted' && (
                <button
                  type="button"
                  className="btn-action-primary"
                  onClick={() => onResumeWorkflow(selectedWorkflow.workflow_id)}
                  disabled={isResuming}
                >
                  {isResuming ? 'Resuming...' : 'Safe Replay & Resume'}
                </button>
              )}
            </div>
          </div>

          {/* Metadata Row */}
          <div className="detail-meta-grid">
            <div className="meta-card">
              <span className="meta-label">Tenant</span>
              <span className="meta-val font-semibold">{selectedWorkflow.tenant_id}</span>
            </div>
            <div className="meta-card">
              <span className="meta-label">Execution Duration</span>
              <span className="meta-val">
                {selectedWorkflow.duration_ms ? `${(selectedWorkflow.duration_ms / 1000).toFixed(2)}s` : 'Active / Pending'}
              </span>
            </div>
            <div className="meta-card">
              <span className="meta-label">Checkpoints Saved</span>
              <span className="meta-val font-semibold">{checkpoints.length}</span>
            </div>
            <div className="meta-card">
              <span className="meta-label">Resumability Status</span>
              <span className="meta-val">
                {resumability?.resumable ? (
                  <span className="text-emerald-600 font-semibold">Resumable (Safe Replay)</span>
                ) : (
                  <span className="text-slate-500">Completed or Terminal</span>
                )}
              </span>
            </div>
          </div>

          {/* Sub-tabs: DAG Visualizer, Task Table, Trace Stream, Checkpoints */}
          <div className="tab-strip">
            <button
              type="button"
              className={`tab-strip-btn ${detailTab === 'dag' ? 'tab-strip-active' : ''}`}
              onClick={() => setDetailTab('dag')}
            >
              Task DAG Graph ({tasks.length})
            </button>
            <button
              type="button"
              className={`tab-strip-btn ${detailTab === 'tasks' ? 'tab-strip-active' : ''}`}
              onClick={() => setDetailTab('tasks')}
            >
              Task Execution List
            </button>
            <button
              type="button"
              className={`tab-strip-btn ${detailTab === 'trace' ? 'tab-strip-active' : ''}`}
              onClick={() => setDetailTab('trace')}
            >
              Live Trace Logs ({traceEvents.length})
            </button>
            <button
              type="button"
              className={`tab-strip-btn ${detailTab === 'checkpoints' ? 'tab-strip-active' : ''}`}
              onClick={() => setDetailTab('checkpoints')}
            >
              Durable Checkpoints ({checkpoints.length})
            </button>
          </div>

          {/* Detail Tab Content */}
          <div className="detail-content-area">
            {/* 1. DAG Visualizer */}
            {detailTab === 'dag' && (
              <TaskDAG
                tasks={tasks}
                selectedTaskId={selectedTask?.task_id || null}
                onSelectTask={(t) => setSelectedTask(t)}
              />
            )}

            {/* 2. Tasks Table */}
            {detailTab === 'tasks' && (
              <div className="table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Task ID</th>
                      <th>Tool Name</th>
                      <th>Status</th>
                      <th>Dependencies</th>
                      <th>Duration</th>
                      <th>Attempts</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tasks.map((t) => (
                      <tr key={t.task_id} className={selectedTask?.task_id === t.task_id ? 'row-selected' : ''}>
                        <td className="font-mono text-xs font-semibold">{t.task_id}</td>
                        <td className="font-mono text-xs text-indigo-600">{t.tool_name}</td>
                        <td><StatusBadge status={t.status} size="sm" /></td>
                        <td className="text-xs text-slate-500">
                          {t.dependencies?.length ? t.dependencies.join(', ') : 'None'}
                        </td>
                        <td className="text-xs text-slate-500">
                          {t.duration_ms ? `${(t.duration_ms / 1000).toFixed(2)}s` : '—'}
                        </td>
                        <td className="text-xs">{t.attempt_count}</td>
                        <td>
                          <button
                            type="button"
                            className="btn-table-action"
                            onClick={() => setSelectedTask(t)}
                          >
                            Inspect
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* 3. Trace Logs */}
            {detailTab === 'trace' && (
              <div className="trace-terminal">
                <div className="terminal-header">Workflow Execution Telemetry Stream</div>
                <div className="terminal-body">
                  {traceEvents.length === 0 ? (
                    <div className="text-slate-400">No trace events recorded for this run.</div>
                  ) : (
                    traceEvents.map((evt, idx) => (
                      <div key={idx} className="trace-row">
                        <span className="trace-time">
                          {new Date(evt.timestamp).toISOString().split('T')[1].slice(0, 12)}
                        </span>
                        <span className="trace-type">[{evt.event_type}]</span>
                        <span className="trace-task">{evt.task_id ? `@${evt.task_id}` : ''}</span>
                        <span className="trace-msg">
                          {typeof evt.details === 'object' ? JSON.stringify(evt.details) : String(evt.details)}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </div>
            )}

            {/* 4. Checkpoints */}
            {detailTab === 'checkpoints' && (
              <div className="checkpoints-list">
                {checkpoints.length === 0 ? (
                  <div className="empty-box">No durable checkpoints recorded.</div>
                ) : (
                  checkpoints.map((cp) => (
                    <div key={cp.checkpoint_id} className="checkpoint-card">
                      <div className="checkpoint-header">
                        <span className="font-mono text-xs font-semibold text-slate-700">
                          {cp.checkpoint_id}
                        </span>
                        <span className="text-xs text-slate-400">
                          {new Date(cp.created_at).toLocaleString()}
                        </span>
                      </div>
                      <div className="checkpoint-meta">
                        <span>Graph Version: {cp.graph_version}</span>
                        <span>Completed: {cp.completed_tasks?.length || 0} tasks</span>
                        <span>Pending: {cp.pending_tasks?.length || 0} tasks</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}

            {/* Task Detail Inspector Modal / Drawer */}
            {selectedTask && (
              <div className="task-inspector-panel">
                <div className="inspector-header">
                  <div>
                    <h4 className="font-semibold text-slate-800">Task Details: {selectedTask.task_id}</h4>
                    <span className="text-xs text-slate-500">{selectedTask.tool_name}</span>
                  </div>
                  <button
                    type="button"
                    className="modal-close-btn"
                    onClick={() => setSelectedTask(null)}
                  >
                    <CloseIcon size={16} />
                  </button>
                </div>
                <div className="inspector-body">
                  <div className="mb-3">
                    <span className="text-xs text-slate-500 block mb-1">Status</span>
                    <StatusBadge status={selectedTask.status} />
                  </div>
                  {selectedTask.error_message && (
                    <div className="inspector-error">
                      <strong>Execution Error:</strong> {selectedTask.error_message}
                    </div>
                  )}
                  {selectedTask.result && (
                    <div>
                      <span className="text-xs text-slate-500 block mb-1">Execution Output / Result:</span>
                      <pre className="code-block">
                        {JSON.stringify(selectedTask.result, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* Workflows List Table */
        <div className="list-view">
          {/* Controls Bar: Search, Status Filters, Submit CTA */}
          <div className="filter-bar">
            <div className="search-box">
              <SearchIcon size={16} className="search-icon" />
              <input
                type="text"
                placeholder="Search workflows by ID or prompt..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="search-input"
              />
            </div>

            <div className="status-pills">
              {statuses.map((st) => (
                <button
                  key={st}
                  type="button"
                  className={`status-pill-btn ${statusFilter === st ? 'status-pill-active' : ''}`}
                  onClick={() => setStatusFilter(st)}
                >
                  <span className="capitalize">{st}</span>
                </button>
              ))}
            </div>

            <button
              type="button"
              className="btn-action-primary"
              onClick={onOpenSubmitModal}
            >
              <PlusIcon size={14} />
              <span>Submit Workflow</span>
            </button>
          </div>

          {/* Workflows Table */}
          <div className="table-card">
            {filteredWorkflows.length === 0 ? (
              <div className="empty-box py-12">
                <p className="text-slate-600 font-medium mb-1">No workflows found</p>
                <p className="text-slate-400 text-xs">
                  {searchQuery || statusFilter !== 'all'
                    ? 'No workflows match the active filter criteria.'
                    : 'Submit a new agent workflow to begin autonomous execution.'}
                </p>
              </div>
            ) : (
              <div className="table-wrapper">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Workflow ID</th>
                      <th>Status</th>
                      <th>Request Prompt</th>
                      <th>Tasks</th>
                      <th>Duration</th>
                      <th>Created</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredWorkflows.map((wf) => (
                      <tr
                        key={wf.workflow_id}
                        className="table-row-clickable"
                        onClick={() => onSelectWorkflow(wf.workflow_id)}
                      >
                        <td className="font-mono text-xs font-semibold text-slate-800">
                          {wf.workflow_id}
                        </td>
                        <td>
                          <StatusBadge status={wf.status} size="sm" />
                        </td>
                        <td className="max-w-md truncate text-xs text-slate-600">
                          {wf.request_prompt || 'Workflow execution'}
                        </td>
                        <td className="text-xs text-slate-500">
                          {wf.plan?.max_concurrency ? `${wf.plan.max_concurrency} max workers` : 'Adaptive'}
                        </td>
                        <td className="text-xs text-slate-500">
                          {wf.duration_ms ? `${(wf.duration_ms / 1000).toFixed(2)}s` : '—'}
                        </td>
                        <td className="text-xs text-slate-400">
                          {new Date(wf.created_at).toLocaleString([], {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="btn-table-action"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectWorkflow(wf.workflow_id);
                            }}
                          >
                            Inspect DAG →
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

