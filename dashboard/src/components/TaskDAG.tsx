import React from 'react';
import { WorkflowTask } from '../types';
import { StatusBadge } from './StatusBadge';
import { ArrowRightIcon } from './Icons';

interface TaskDAGProps {
  tasks: WorkflowTask[];
  selectedTaskId: string | null;
  onSelectTask: (task: WorkflowTask) => void;
}

export const TaskDAG: React.FC<TaskDAGProps> = ({
  tasks,
  selectedTaskId,
  onSelectTask,
}) => {
  if (!tasks || tasks.length === 0) {
    return (
      <div className="dag-empty">
        <p className="text-slate-500 text-sm">No task execution graph available for this workflow.</p>
      </div>
    );
  }

  // Group tasks by level/dependencies
  const rootTasks = tasks.filter((t) => !t.dependencies || t.dependencies.length === 0);
  const dependentTasks = tasks.filter((t) => t.dependencies && t.dependencies.length > 0);

  return (
    <div className="dag-container">
      <div className="dag-header">
        <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
          Task Execution Graph (DAG) & Dependency Ordering
        </h4>
        <span className="text-xs text-slate-400">
          Click any node to inspect execution trace & input/output payload
        </span>
      </div>

      <div className="dag-grid">
        {/* Tier 1: Root / Seed Tasks */}
        <div className="dag-tier">
          <div className="dag-tier-label">Initial Tasks</div>
          <div className="dag-tier-nodes">
            {rootTasks.map((task) => (
              <div
                key={task.task_id}
                className={`dag-node ${selectedTaskId === task.task_id ? 'dag-node-selected' : ''}`}
                onClick={() => onSelectTask(task)}
              >
                <div className="dag-node-header">
                  <span className="dag-node-id">{task.task_id}</span>
                  <StatusBadge status={task.status} size="sm" />
                </div>
                <div className="dag-node-tool" title={task.tool_name}>
                  {task.tool_name}
                </div>
                {task.duration_ms !== undefined && task.duration_ms !== null && (
                  <div className="dag-node-duration">
                    {(task.duration_ms / 1000).toFixed(2)}s
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Tier Connector */}
        {dependentTasks.length > 0 && (
          <div className="dag-connector">
            <ArrowRightIcon size={24} className="text-slate-400" />
          </div>
        )}

        {/* Tier 2: Dependent / Downstream Tasks */}
        {dependentTasks.length > 0 && (
          <div className="dag-tier">
            <div className="dag-tier-label">Dependent Tasks</div>
            <div className="dag-tier-nodes">
              {dependentTasks.map((task) => (
                <div
                  key={task.task_id}
                  className={`dag-node ${selectedTaskId === task.task_id ? 'dag-node-selected' : ''}`}
                  onClick={() => onSelectTask(task)}
                >
                  <div className="dag-node-header">
                    <span className="dag-node-id">{task.task_id}</span>
                    <StatusBadge status={task.status} size="sm" />
                  </div>
                  <div className="dag-node-tool" title={task.tool_name}>
                    {task.tool_name}
                  </div>
                  <div className="dag-node-deps">
                    Depends on: {task.dependencies.join(', ')}
                  </div>
                  {task.duration_ms !== undefined && task.duration_ms !== null && (
                    <div className="dag-node-duration">
                      {(task.duration_ms / 1000).toFixed(2)}s
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

