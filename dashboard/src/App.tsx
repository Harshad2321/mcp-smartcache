import React from 'react';

export default function App(): JSX.Element {
  return (
    <div style={{ fontFamily: 'system-ui, -apple-system, sans-serif', padding: '2rem', maxWidth: '800px', margin: '0 auto' }}>
      <h1>MCP SmartCache Dashboard</h1>
      <p style={{ color: '#666' }}>Phase 0 Scaffold Active — No workflow runs recorded yet.</p>
      <div style={{ padding: '1rem', border: '1px solid #e2e8f0', borderRadius: '8px', background: '#f8fafc' }}>
        <strong>System Status:</strong> Ready for Phase 1+ instrumentation.
      </div>
    </div>
  );
}
