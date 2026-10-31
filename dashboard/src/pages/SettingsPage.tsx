import React, { useState } from 'react';
import { api } from '../services/api';
import { CheckIcon } from '../components/Icons';

interface SettingsPageProps {
  tenantId: string;
  onSelectTenant: (tenantId: string) => void;
}

export const SettingsPage: React.FC<SettingsPageProps> = ({
  tenantId,
  onSelectTenant,
}) => {
  const [apiKey, setApiKey] = useState(api.getApiKey());
  const [isSaved, setIsSaved] = useState(false);

  const handleSaveKey = (e: React.FormEvent) => {
    e.preventDefault();
    api.setApiKey(apiKey);
    setIsSaved(true);
    setTimeout(() => setIsSaved(false), 2000);
  };

  return (
    <div className="page-container">
      <div className="filter-bar mb-6">
        <div>
          <h3 className="text-base font-semibold text-slate-800">
            Platform Configuration & Developer Settings
          </h3>
          <p className="text-xs text-slate-500">
            Manage credentials, inspect network topology, and access interactive API specifications
          </p>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-6">
        {/* Tenant Scope & Authentication */}
        <div className="content-card">
          <div className="card-header">
            <div>
              <h4 className="card-title">Tenant Scope & Client API Credentials</h4>
              <p className="card-subtitle">Bearer token sent with all outgoing dashboard API calls</p>
            </div>
          </div>
          <form onSubmit={handleSaveKey} className="p-4 space-y-4">
            <div>
              <label className="input-label">Active Tenant Context</label>
              <select
                className="input-field"
                value={tenantId}
                onChange={(e) => onSelectTenant(e.target.value)}
              >
                <option value="tenant_a">Tenant Alpha (Enterprise)</option>
                <option value="tenant_b">Tenant Beta (Standard)</option>
                <option value="default">Default Tenant</option>
              </select>
            </div>

            <div>
              <label className="input-label">Bearer API Key</label>
              <input
                type="password"
                className="input-field font-mono text-xs"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
              <span className="text-xs text-slate-400 mt-1 block">
                Constant-time authenticated and bound to tenant quota engine.
              </span>
            </div>

            <div className="flex items-center justify-between pt-2">
              <button type="submit" className="btn-action-primary">
                Update Active Credential
              </button>
              {isSaved && (
                <span className="text-xs font-semibold text-emerald-600 flex items-center gap-1">
                  <CheckIcon size={14} /> Credential updated
                </span>
              )}
            </div>
          </form>
        </div>

        {/* Live Service Endpoints & Documentation */}
        <div className="content-card">
          <div className="card-header">
            <div>
              <h4 className="card-title">Service Endpoints & Swagger API Docs</h4>
              <p className="card-subtitle">Direct links to interactive OpenAPI specifications</p>
            </div>
          </div>
          <div className="p-4 space-y-3">
            <div className="endpoint-row">
              <div>
                <div className="text-xs font-semibold text-slate-800">MCP Gateway Service</div>
                <div className="text-xs font-mono text-slate-500">http://localhost:8000</div>
              </div>
              <a
                href="http://localhost:8000/docs"
                target="_blank"
                rel="noreferrer"
                className="btn-table-action"
              >
                Open Gateway Swagger ↗
              </a>
            </div>

            <div className="endpoint-row">
              <div>
                <div className="text-xs font-semibold text-slate-800">Control Plane API</div>
                <div className="text-xs font-mono text-slate-500">http://localhost:8080</div>
              </div>
              <a
                href="http://localhost:8080/docs"
                target="_blank"
                rel="noreferrer"
                className="btn-table-action"
              >
                Open Control API Swagger ↗
              </a>
            </div>

            <div className="endpoint-row">
              <div>
                <div className="text-xs font-semibold text-slate-800">LangGraph Agent Runtime</div>
                <div className="text-xs font-mono text-slate-500">http://localhost:8002</div>
              </div>
              <span className="badge-transport">Internal Network Tier</span>
            </div>

            <div className="endpoint-row">
              <div>
                <div className="text-xs font-semibold text-slate-800">TLS Ingress Reverse Proxy</div>
                <div className="text-xs font-mono text-slate-500">https://localhost:443 (Nginx Edge)</div>
              </div>
              <span className="badge-positive">TLS 1.3 Active</span>
            </div>
          </div>
        </div>

        {/* Security & Isolation Boundaries */}
        <div className="content-card col-span-2">
          <div className="card-header">
            <div>
              <h4 className="card-title">Security Architecture & Environment Verification</h4>
              <p className="card-subtitle">Production boundary invariants enforced at runtime</p>
            </div>
          </div>
          <div className="p-4 grid grid-cols-3 gap-4 text-xs">
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="font-semibold text-slate-800 block mb-1">Dual-Tier Network Segregation</span>
              <p className="text-slate-500">
                PostgreSQL and Redis reside strictly on <code>internal-tier</code> with zero host ports. Runtime is unexposed externally.
              </p>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="font-semibold text-slate-800 block mb-1">Outbound Transport & SSRF Guards</span>
              <p className="text-slate-500">
                Upstream connections enforce DNS pin checks, IPv4-mapped IPv6 blocklists, cloud metadata rejection, and socket peer inspection.
              </p>
            </div>
            <div className="p-3 bg-slate-50 border border-slate-200 rounded">
              <span className="font-semibold text-slate-800 block mb-1">Crash Recovery & Safe Resumption</span>
              <p className="text-slate-500">
                Stranded workflows are automatically caught and transitioned to interrupted state on reboot without blind mutation replaying.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
