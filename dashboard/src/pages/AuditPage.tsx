import React, { useState } from 'react';
import { AuditEvent } from '../types';
import { StatusBadge } from '../components/StatusBadge';
import { Modal } from '../components/Modal';
import { SearchIcon } from '../components/Icons';

interface AuditPageProps {
  auditEvents: AuditEvent[];
  tenantId: string;
}

export const AuditPage: React.FC<AuditPageProps> = ({ auditEvents, tenantId }) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedEvent, setSelectedEvent] = useState<AuditEvent | null>(null);

  const filteredEvents = auditEvents.filter((evt) => {
    const query = searchQuery.toLowerCase().trim();
    if (!query) return true;
    return (
      evt.action.toLowerCase().includes(query) ||
      evt.actor.toLowerCase().includes(query) ||
      evt.resource_type.toLowerCase().includes(query) ||
      (evt.resource_id && evt.resource_id.toLowerCase().includes(query)) ||
      evt.event_id.toLowerCase().includes(query)
    );
  });

  return (
    <div className="page-container">
      {/* Top Bar */}
      <div className="filter-bar">
        <div>
          <h3 className="text-base font-semibold text-slate-800">
            Cryptographic Audit & Security Telemetry
          </h3>
          <p className="text-xs text-slate-500">
            Immutable log of all tenant mutations, tool executions, policy changes, and security boundary events
          </p>
        </div>

        <div className="search-box">
          <SearchIcon size={16} className="search-icon" />
          <input
            type="text"
            placeholder="Search by action, actor, resource ID..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="search-input w-72"
          />
        </div>
      </div>

      {/* Audit Log Table */}
      <div className="table-card">
        {filteredEvents.length === 0 ? (
          <div className="empty-box py-10">
            <p className="text-slate-600 font-medium text-xs mb-1">No audit events match your search</p>
            <p className="text-slate-400 text-xs">Events will populate automatically as actions occur.</p>
          </div>
        ) : (
          <div className="table-wrapper">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Event ID</th>
                  <th>Timestamp</th>
                  <th>Actor</th>
                  <th>Action</th>
                  <th>Resource Type</th>
                  <th>Resource ID</th>
                  <th>Outcome</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {filteredEvents.map((evt) => (
                  <tr key={evt.event_id} className="table-row-clickable" onClick={() => setSelectedEvent(evt)}>
                    <td className="font-mono text-xs font-semibold text-slate-700">
                      {evt.event_id.slice(0, 16)}...
                    </td>
                    <td className="text-xs text-slate-500">
                      {new Date(evt.timestamp).toLocaleString()}
                    </td>
                    <td className="text-xs font-medium text-slate-800">{evt.actor}</td>
                    <td>
                      <span className="font-mono text-xs font-semibold text-indigo-600">{evt.action}</span>
                    </td>
                    <td className="text-xs text-slate-600">{evt.resource_type}</td>
                    <td className="font-mono text-xs text-slate-500 max-w-xs truncate">
                      {evt.resource_id || '—'}
                    </td>
                    <td>
                      <StatusBadge status={evt.outcome} size="sm" />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn-table-action"
                        onClick={(e) => {
                          e.stopPropagation();
                          setSelectedEvent(evt);
                        }}
                      >
                        Inspect Payload
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Formatted Audit Metadata Modal */}
      {selectedEvent && (
        <Modal
          isOpen={true}
          onClose={() => setSelectedEvent(null)}
          title={`Audit Event: ${selectedEvent.action}`}
          subtitle={`Event ID: ${selectedEvent.event_id}`}
        >
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 p-3 bg-slate-50 border border-slate-200 rounded text-xs">
              <div><strong>Actor:</strong> {selectedEvent.actor}</div>
              <div><strong>Outcome:</strong> <StatusBadge status={selectedEvent.outcome} size="sm" /></div>
              <div><strong>Resource:</strong> {selectedEvent.resource_type} ({selectedEvent.resource_id || 'global'})</div>
              <div><strong>Timestamp:</strong> {new Date(selectedEvent.timestamp).toISOString()}</div>
              <div><strong>Tenant Scope:</strong> {selectedEvent.tenant_id || tenantId}</div>
            </div>

            <div>
              <span className="text-xs font-semibold text-slate-600 block mb-1">
                Metadata & Structured Security Attributes:
              </span>
              <pre className="code-block max-h-80 overflow-y-auto">
                {JSON.stringify(selectedEvent.metadata || {}, null, 2)}
              </pre>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
};

