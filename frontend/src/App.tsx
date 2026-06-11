import { useState, useEffect } from 'react';
import './index.css';

// In production, the API is served from the same origin via Nginx reverse proxy.
// In development, Vite proxies /api/* and internal endpoints to the backend.
// If you need a custom backend URL, set VITE_API_BASE in frontend/.env
const API_BASE = (import.meta.env as Record<string, string | undefined>).VITE_API_BASE || '';

type Job = {
  id: number;
  type: string;
  priority: number;
  status: string;
  retry_count: number;
  scheduled_at: string | null;
  interval: string | null;
  created_at: string;
};

export default function App() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeTab, setActiveTab] = useState<'dashboard' | 'create' | 'dlq'>('dashboard');

  useEffect(() => {
    fetchJobs();
    const interval = setInterval(fetchJobs, 3000);
    return () => clearInterval(interval);
  }, []);

  const fetchJobs = async () => {
    try {
      const res = await fetch(`${API_BASE}/jobs`);
      const data = await res.json();
      setJobs(data);
    } catch {
      // Silently retry on next poll
    }
  };

  const handleCreateJob = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    
    const payloadStr = fd.get('payload') as string;
    try {
      JSON.parse(payloadStr || '{}');
    } catch {
      // TODO(security): Replace with a framework-native modal component instead of alert()
      alert('Payload must be valid JSON');
      return;
    }

    const body: Record<string, unknown> = {
      type: fd.get('type') as string,
      priority: Number(fd.get('priority')),
      payload: payloadStr || '{}',
    };

    const scheduledAt = fd.get('scheduled_at') as string;
    if (scheduledAt) {
      body.scheduled_at = new Date(scheduledAt).toISOString();
    }

    const interval = fd.get('interval') as string;
    if (interval) {
      body.interval = interval;
    }

    try {
      await fetch(`${API_BASE}/jobs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      setActiveTab('dashboard');
      e.currentTarget.reset();
    } catch {
      // Error handling
    }
  };

  const handleCancel = async (id: number) => {
    await fetch(`${API_BASE}/jobs/${id}/cancel`, { method: 'POST' });
    fetchJobs();
  };

  const handleRetry = async (id: number) => {
    await fetch(`${API_BASE}/jobs/${id}/retry`, { method: 'POST' });
    fetchJobs();
  };

  const dlqJobs = jobs.filter(j => j.status === 'failed' && j.retry_count >= 3);
  
  const statusCounts = jobs.reduce((acc, job) => {
    acc[job.status] = (acc[job.status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const formatDate = (ds: string | null) => {
    if (!ds) return '-';
    return new Intl.DateTimeFormat('en-US', { 
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).format(new Date(ds));
  };

  const formatInterval = (interval: string | null) => {
    if (!interval) return '-';
    switch (interval) {
      case 'every_1_minute': return 'Every 1 minute';
      case 'every_5_minutes': return 'Every 5 minutes';
      case 'every_1_hour': return 'Every 1 hour';
      default:
        return interval
          .replace(/_/g, ' ')
          .replace(/^\w/, (c) => c.toUpperCase());
    }
  };

  const formatType = (type: string) => {
    if (!type) return '-';
    switch (type) {
      case 'send_email': return 'Send Email';
      default:
        return type
          .replace(/_/g, ' ')
          .replace(/^\w/, (c) => c.toUpperCase());
    }
  };

  return (
    <>
      <div className="header-row">
        <div>
          <h1>Dilamme Scheduler</h1>
          <p style={{ color: 'var(--text-muted)' }}>Background job processing and workflow engine.</p>
        </div>
      </div>

      <div className="nav-tabs">
        <div className={`nav-tab ${activeTab === 'dashboard' ? 'active' : ''}`} onClick={() => setActiveTab('dashboard')}>
          Dashboard
        </div>
        <div className={`nav-tab ${activeTab === 'create' ? 'active' : ''}`} onClick={() => setActiveTab('create')}>
          Create Job
        </div>
        <div className={`nav-tab ${activeTab === 'dlq' ? 'active' : ''}`} onClick={() => setActiveTab('dlq')}>
          DLQ ({dlqJobs.length})
        </div>
      </div>

      {activeTab === 'dashboard' && (
        <div className="tab-content">
          <div className="grid-cards">
            <div className="card glass">
              <div className="card-title">Pending</div>
              <div className="card-value">{statusCounts['pending'] || 0}</div>
            </div>
            <div className="card glass">
              <div className="card-title">Processing</div>
              <div className="card-value">{statusCounts['processing'] || 0}</div>
            </div>
            <div className="card glass">
              <div className="card-title">Completed</div>
              <div className="card-value">{statusCounts['completed'] || 0}</div>
            </div>
            <div className="card glass">
              <div className="card-title">Failed</div>
              <div className="card-value">{statusCounts['failed'] || 0}</div>
            </div>
          </div>

          <div className="glass" style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Type</th>
                  <th>Priority</th>
                  <th>Status</th>
                  <th>Retries</th>
                  <th>Scheduled</th>
                  <th>Interval</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map(job => (
                  <tr key={job.id}>
                    <td>#{job.id}</td>
                    <td>{formatType(job.type)}</td>
                    <td>{job.priority === 1 ? 'High' : job.priority === 2 ? 'Medium' : 'Low'}</td>
                    <td><span className={`badge ${job.status}`}>{job.status}</span></td>
                    <td>{job.retry_count}</td>
                    <td>{formatDate(job.scheduled_at)}</td>
                    <td>{formatInterval(job.interval)}</td>
                    <td>
                      {(job.status === 'pending' || job.status === 'processing') && (
                        <button className="danger" style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }} onClick={() => handleCancel(job.id)}>
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === 'create' && (
        <div className="glass" style={{ padding: '2rem', maxWidth: '600px', margin: '0 auto' }}>
          <h2>Create New Job</h2>
          <form onSubmit={handleCreateJob}>
            <div className="form-group">
              <label>Job Type</label>
              <select name="type" required>
                <option value="send_email">Send Email</option>
              </select>
            </div>
            
            <div className="form-group">
              <label>Priority</label>
              <select name="priority">
                <option value="1">1 - High</option>
                <option value="2" selected>2 - Medium</option>
                <option value="3">3 - Low</option>
              </select>
            </div>

            <div className="form-group">
              <label>Scheduled At (Optional)</label>
              <input type="datetime-local" name="scheduled_at" />
            </div>

            <div className="form-group">
              <label>Recurring Interval (Optional)</label>
              <select name="interval">
                <option value="">None</option>
                <option value="every_1_minute">Every 1 Minute</option>
                <option value="every_5_minutes">Every 5 minutes</option>
                <option value="every_1_hour">Every 1 hour</option>
              </select>
            </div>

            <div className="form-group">
              <label>Payload (JSON)</label>
              <textarea name="payload" rows={4} defaultValue='{"to": "user@example.com"}'></textarea>
            </div>

            <button type="submit" style={{ width: '100%' }}>Create Job</button>
          </form>
        </div>
      )}

      {activeTab === 'dlq' && (
        <div className="glass" style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Type</th>
                <th>Priority</th>
                <th>Failed At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {dlqJobs.length === 0 ? (
                <tr>
                  <td colSpan={5} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
                    No failed jobs in the DLQ.
                  </td>
                </tr>
              ) : null}
              {dlqJobs.map(job => (
                <tr key={job.id}>
                  <td>#{job.id}</td>
                  <td>{job.type}</td>
                  <td>{job.priority}</td>
                  <td>{formatDate(job.created_at)}</td>
                  <td>
                    <button style={{ padding: '0.25rem 0.5rem', fontSize: '0.75rem' }} onClick={() => handleRetry(job.id)}>
                      Manual Retry
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
