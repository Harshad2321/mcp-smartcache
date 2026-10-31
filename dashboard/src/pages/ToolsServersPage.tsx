import React, { useState } from 'react';
import { UpstreamServer } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { api } from '../services/api';
import { PlusIcon, RefreshIcon, PlayIcon, CloseIcon } from '../components/Icons';

interface ToolsServersPageProps {
  servers: UpstreamServer[];
  onRefresh: () => void;
}

export const ToolsServersPage: React.FC<ToolsServersPageProps> = ({
  servers,
  onRefresh,
}) => {
  const [selectedServer, setSelectedServer] = useState<UpstreamServer | null>(null);
  const [serverTools, setServerTools] = useState<any[]>([]);
  const [isLoadingTools, setIsLoadingTools] = useState(false);

  // Selected Tool for Schema Inspection & Invocation Test
  const [selectedTool, setSelectedTool] = useState<any | null>(null);
  const [testArguments, setTestArguments] = useState<string>('{\n  "owner": "google",\n  "repo": "smartcache"\n}');
  const [testResult, setTestResult] = useState<any | null>(null);
  const [testLatency, setTestLatency] = useState<number | null>(null);
  const [isInvoking, setIsInvoking] = useState(false);
  const [invokeError, setInvokeError] = useState<string | null>(null);

  // Register Server Modal State
  const [showRegisterModal, setShowRegisterModal] = useState(false);
  const [regServerId, setRegServerId] = useState('github_intelligence');
  const [regServerName, setRegServerName] = useState('GitHub Intelligence Upstream');
  const [regTransport, setRegTransport] = useState<'stdio' | 'sse'>('stdio');
  const [regCommand, setRegCommand] = useState('python');
  const [regArgs, setRegArgs] = useState('-m, gateway.mock_servers.repository');
  const [regUrl, setRegUrl] = useState('http://mock-upstream:8001/sse');
  const [regTags, setRegTags] = useState('git, code, metadata');
  const [isRegistering, setIsRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  // Load server tools when clicking inspect
  const handleInspectServer = async (server: UpstreamServer) => {
    setSelectedServer(server);
    setIsLoadingTools(true);
    setSelectedTool(null);
    setTestResult(null);
    try {
      const tools = await api.getServerTools(server.server_id);
      setServerTools(tools);
      if (tools.length > 0) {
        setSelectedTool(tools[0]);
      }
    } catch {
      setServerTools([]);
    } finally {
      setIsLoadingTools(false);
    }
  };

  const handleRefreshServer = async (serverId: string) => {
    try {
      await api.refreshServer(serverId);
      onRefresh();
    } catch (err: any) {
      alert(`Refresh failed: ${err.message}`);
    }
  };

  const handleDeleteServer = async (serverId: string) => {
    if (!window.confirm(`Are you sure you want to deregister upstream server '${serverId}'?`)) {
      return;
    }
    try {
      await api.deleteServer(serverId);
      if (selectedServer?.server_id === serverId) {
        setSelectedServer(null);
      }
      onRefresh();
    } catch (err: any) {
      alert(`Deregistration failed: ${err.message}`);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsRegistering(true);
    setRegisterError(null);

    const payload: Record<string, any> = {
      server_id: regServerId,
      name: regServerName,
      transport: regTransport,
      tags: regTags.split(',').map((t) => t.trim()).filter(Boolean),
    };

    if (regTransport === 'stdio') {
      payload.connection_config = {
        command: regCommand,
        args: regArgs.split(',').map((a) => a.trim()).filter(Boolean),
      };
    } else {
      payload.connection_config = {
        url: regUrl,
      };
    }

    try {
      await api.registerServer(payload);
      setShowRegisterModal(false);
      onRefresh();
    } catch (err: any) {
      setRegisterError(err.message || 'Server registration failed');
    } finally {
      setIsRegistering(false);
    }
  };

  const handleTestInvoke = async () => {
    if (!selectedTool) return;
    setIsInvoking(true);
    setInvokeError(null);
    setTestResult(null);
    setTestLatency(null);

    let parsedArgs = {};
    try {
      parsedArgs = JSON.parse(testArguments);
    } catch {
      setInvokeError('Invalid JSON in arguments payload');
      setIsInvoking(false);
      return;
    }

    try {
      const res = await api.invokeTool(selectedTool.name, parsedArgs);
      setTestResult(res.result);
      setTestLatency(res.latencyMs);
    } catch (err: any) {
      setInvokeError(err.message || 'Invocation failed');
    } finally {
      setIsInvoking(false);
    }
  };

  return (
    <div className="page-container">
      {/* Top Action Bar */}
      <div className="filter-bar">
        <div>
          <h3 className="text-base font-semibold text-slate-800">Upstream MCP Tool Servers</h3>
          <p className="text-xs text-slate-500">
            Registered transport endpoints providing tool capabilities to autonomous workflows
          </p>
        </div>
        <button
          type="button"
          className="btn-action-primary"
          onClick={() => setShowRegisterModal(true)}
        >
          <PlusIcon size={14} />
          <span>Register Upstream Server</span>
        </button>
      </div>

      {/* Upstream Servers Table */}
      <div className="table-card mb-6">
        {servers.length === 0 ? (
          <div className="empty-box py-10">
            <p className="text-slate-600 font-medium mb-1">No upstream servers registered</p>
            <p className="text-slate-400 text-xs">
              Click 'Register Upstream Server' to connect an MCP tool server via stdio or SSE.
            </p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Server ID</th>
                  <th>Name</th>
                  <th>Transport</th>
                  <th>Status</th>
                  <th>Discovered Tools</th>
                  <th>Tags</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {servers.map((s) => (
                  <tr
                    key={s.server_id}
                    className={selectedServer?.server_id === s.server_id ? 'row-selected' : ''}
                  >
                    <td className="font-mono text-xs font-semibold text-slate-800">{s.server_id}</td>
                    <td className="text-xs font-medium text-slate-700">{s.name}</td>
                    <td>
                      <span className="badge-transport">{s.transport}</span>
                    </td>
                    <td>
                      <StatusBadge status={s.status} size="sm" />
                    </td>
                    <td className="text-xs font-semibold text-slate-700">
                      {s.tools_discovered || 0} tools
                    </td>
                    <td className="text-xs text-slate-500">
                      {s.tags?.length ? s.tags.join(', ') : '—'}
                    </td>
                    <td>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          className="btn-table-action"
                          onClick={() => handleInspectServer(s)}
                        >
                          Inspect Tools
                        </button>
                        <button
                          type="button"
                          className="btn-icon-subtle"
                          title="Refresh server tools"
                          onClick={() => handleRefreshServer(s.server_id)}
                        >
                          <RefreshIcon size={14} />
                        </button>
                        <button
                          type="button"
                          className="btn-icon-danger"
                          title="Deregister server"
                          onClick={() => handleDeleteServer(s.server_id)}
                        >
                          <CloseIcon size={14} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Server Tools & Interactive Invocation Inspector */}
      {selectedServer && (
        <div className="tool-inspector-card">
          <div className="inspector-card-header">
            <div>
              <h4 className="text-base font-semibold text-slate-800">
                Discovered Tools: {selectedServer.name} ({selectedServer.server_id})
              </h4>
              <p className="text-xs text-slate-500">
                Inspect JSON Schema parameters, read-only eligibility, and execute test calls
              </p>
            </div>
            <button
              type="button"
              className="modal-close-btn"
              onClick={() => setSelectedServer(null)}
            >
              <CloseIcon size={16} />
            </button>
          </div>

          <div className="tool-inspector-grid">
            {/* Left: Tools List */}
            <div className="tool-list-col">
              <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider block mb-2">
                Available Tools ({serverTools.length})
              </span>
              {isLoadingTools ? (
                <div className="text-xs text-slate-400 p-4">Loading tools schema...</div>
              ) : serverTools.length === 0 ? (
                <div className="text-xs text-slate-400 p-4">No tools discovered from this server.</div>
              ) : (
                <div className="tool-button-list">
                  {serverTools.map((t) => (
                    <button
                      key={t.name}
                      type="button"
                      className={`tool-select-btn ${selectedTool?.name === t.name ? 'tool-select-active' : ''}`}
                      onClick={() => {
                        setSelectedTool(t);
                        setTestResult(null);
                        setTestLatency(null);
                      }}
                    >
                      <div className="font-mono text-xs font-semibold truncate">{t.name}</div>
                      <div className="text-xs text-slate-500 truncate">{t.description || 'No description'}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* Right: Tool Schema & Interactive Invocation Test */}
            <div className="tool-test-col">
              {selectedTool ? (
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h5 className="font-mono text-sm font-bold text-slate-800">{selectedTool.name}</h5>
                    <span className="badge-positive text-xs">Read-Only Eligible</span>
                  </div>
                  <p className="text-xs text-slate-600 mb-3">{selectedTool.description}</p>

                  <div className="mb-3">
                    <label className="text-xs font-semibold text-slate-500 block mb-1">
                      Input Arguments (JSON):
                    </label>
                    <textarea
                      rows={5}
                      className="font-mono text-xs w-full p-2 border border-slate-300 rounded bg-slate-50"
                      value={testArguments}
                      onChange={(e) => setTestArguments(e.target.value)}
                    />
                  </div>

                  <button
                    type="button"
                    className="btn-action-primary mb-3"
                    onClick={handleTestInvoke}
                    disabled={isInvoking}
                  >
                    <PlayIcon size={12} />
                    <span>{isInvoking ? 'Executing...' : 'Invoke Tool Test'}</span>
                  </button>

                  {invokeError && (
                    <div className="p-2 mb-3 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded">
                      <strong>Error:</strong> {invokeError}
                    </div>
                  )}

                  {testResult && (
                    <div className="mt-3">
                      <div className="flex items-center justify-between text-xs text-slate-500 mb-1">
                        <span>Response Output:</span>
                        <span className="font-mono font-semibold text-emerald-600">
                          Latency: {testLatency} ms
                        </span>
                      </div>
                      <pre className="code-block max-h-60 overflow-y-auto">
                        {JSON.stringify(testResult, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-xs text-slate-400 p-8 text-center">
                  Select a tool on the left to inspect its parameters and invoke a test call.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Register Server Modal */}
      <Modal
        isOpen={showRegisterModal}
        onClose={() => setShowRegisterModal(false)}
        title="Register Upstream MCP Server"
        subtitle="Connect a new tool provider via local Stdio process or remote SSE endpoint"
      >
        <form onSubmit={handleRegisterSubmit}>
          {registerError && (
            <div className="p-3 mb-4 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded">
              {registerError}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4 mb-3">
            <div>
              <label className="input-label">Server ID (Unique)</label>
              <input
                type="text"
                required
                className="input-field"
                value={regServerId}
                onChange={(e) => setRegServerId(e.target.value)}
              />
            </div>
            <div>
              <label className="input-label">Display Name</label>
              <input
                type="text"
                required
                className="input-field"
                value={regServerName}
                onChange={(e) => setRegServerName(e.target.value)}
              />
            </div>
          </div>

          <div className="mb-3">
            <label className="input-label">Transport Protocol</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  value="stdio"
                  checked={regTransport === 'stdio'}
                  onChange={() => setRegTransport('stdio')}
                />
                StdIO Process (Subprocess)
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                <input
                  type="radio"
                  name="transport"
                  value="sse"
                  checked={regTransport === 'sse'}
                  onChange={() => setRegTransport('sse')}
                />
                Server-Sent Events (SSE HTTP)
              </label>
            </div>
          </div>

          {regTransport === 'stdio' ? (
            <div className="grid grid-cols-2 gap-4 mb-3">
              <div>
                <label className="input-label">Executable Command</label>
                <input
                  type="text"
                  required
                  className="input-field"
                  value={regCommand}
                  onChange={(e) => setRegCommand(e.target.value)}
                />
              </div>
              <div>
                <label className="input-label">Arguments (Comma-separated)</label>
                <input
                  type="text"
                  className="input-field"
                  value={regArgs}
                  onChange={(e) => setRegArgs(e.target.value)}
                />
              </div>
            </div>
          ) : (
            <div className="mb-3">
              <label className="input-label">SSE Endpoint URL</label>
              <input
                type="url"
                required
                className="input-field"
                value={regUrl}
                onChange={(e) => setRegUrl(e.target.value)}
              />
            </div>
          )}

          <div className="mb-5">
            <label className="input-label">Classification Tags (Comma-separated)</label>
            <input
              type="text"
              className="input-field"
              value={regTags}
              onChange={(e) => setRegTags(e.target.value)}
            />
          </div>

          <div className="modal-actions">
            <button
              type="button"
              className="btn-action-secondary"
              onClick={() => setShowRegisterModal(false)}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="btn-action-primary"
              disabled={isRegistering}
            >
              {isRegistering ? 'Registering...' : 'Register Server'}
            </button>
          </div>
        </form>
      </Modal>
    </div>
  );
};

