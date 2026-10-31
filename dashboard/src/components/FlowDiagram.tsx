import React, { useState } from 'react';
import { ArrowRightIcon } from './Icons';

export const FlowDiagram: React.FC = () => {
  const [activeStep, setActiveStep] = useState<number | null>(null);

  const steps = [
    {
      id: 1,
      title: 'AI Agent Request',
      subtitle: 'Client / LLM Caller',
      detail: 'Client initiates tool call or agentic workflow with Bearer token & X-Tenant-ID scope.',
    },
    {
      id: 2,
      title: 'SmartCache Gateway',
      subtitle: 'Edge Policy & Ingress',
      detail: 'Authenticates caller, validates schema, applies tenant rate-limits and payload guards.',
    },
    {
      id: 3,
      title: 'Cache & Singleflight',
      subtitle: 'Redis Fast Path',
      detail: 'Checks cache index. If eligible hit, returns in <5ms. If concurrent, coalesces via distributed singleflight.',
    },
    {
      id: 4,
      title: 'Upstream MCP Tool',
      subtitle: 'StdIO / SSE Transport',
      detail: 'On cache miss, executes against upstream server, stores response for TTL window, returns result.',
    },
    {
      id: 5,
      title: 'Audited Response',
      subtitle: 'Persisted & Replayable',
      detail: 'Returns result to agent with trace ID, logging audit trail and updating performance metrics.',
    },
  ];

  return (
    <div className="flow-card">
      <div className="flow-header">
        <div>
          <span className="flow-badge">Core Architecture</span>
          <h3 className="flow-title">How MCP SmartCache Orchestrates Requests</h3>
          <p className="flow-desc">
            MCP SmartCache sits between AI agents and upstream tools, caching eligible read-only results,
            coalescing concurrent requests via distributed singleflight, and governing tenant policies.
          </p>
        </div>
      </div>

      <div className="flow-pipeline">
        {steps.map((s, idx) => (
          <React.Fragment key={s.id}>
            <div
              className={`flow-node ${activeStep === s.id ? 'flow-node-active' : ''}`}
              onClick={() => setActiveStep(activeStep === s.id ? null : s.id)}
            >
              <div className="flow-step-num">{s.id}</div>
              <div className="flow-node-title">{s.title}</div>
              <div className="flow-node-sub">{s.subtitle}</div>
            </div>
            {idx < steps.length - 1 && (
              <div className="flow-arrow">
                <ArrowRightIcon size={18} />
              </div>
            )}
          </React.Fragment>
        ))}
      </div>

      {activeStep && (
        <div className="flow-detail-drawer">
          <div className="flow-detail-title">
            Step {activeStep}: {steps.find((s) => s.id === activeStep)?.title}
          </div>
          <div className="flow-detail-body">
            {steps.find((s) => s.id === activeStep)?.detail}
          </div>
        </div>
      )}
    </div>
  );
};
