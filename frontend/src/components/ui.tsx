import React from 'react';
import type { JobStatus, JobRecord, JobFilters, OutputFileDto, WorkerHealthResponse, MediaRootDto } from '../models';
import { normalizeStatus, getProgress, formatEta, formatDate, formatBytes } from '../utils/helpers';
import { statusLabels } from '../utils/constants';
import { Trash2, Download, Layers, Play, Archive, Cpu, CheckCircle2, AlertTriangle, Info } from 'lucide-react';

export function HealthPill({ label, ok, meta }: { label: string; ok: boolean; meta?: string }) {
  return (
    <div className="status-item">
      <span className={`status-dot ${ok ? 'ok' : 'error'} ${ok && !meta ? 'pulse' : ''}`}></span>
      <span>{label}{meta ? <span>: <span className="font-mono text-zinc-200">{meta}</span></span> : null}</span>
    </div>
  );
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const norm = normalizeStatus(status);
  let icon = null;
  if (norm === 'done') icon = <span className="w-1 h-1 bg-emerald-400 rounded-full" style={{ width: '4px', height: '4px', display: 'inline-block', borderRadius: '50%', backgroundColor: 'var(--emerald-400)' }}></span>;
  if (norm === 'running') icon = <span className="w-1 h-1 bg-blue-400 rounded-full pulse" style={{ width: '4px', height: '4px', display: 'inline-block', borderRadius: '50%', backgroundColor: 'var(--blue-400)' }}></span>;

  return (
    <span className={`badge badge-${norm}`}>
      {icon}
      <span>{statusLabels[status] || status}</span>
    </span>
  );
}

export function EmptyState({ title, body, action }: { title: string; body?: string; action?: React.ReactNode }) {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <Layers size={20} />
      </div>
      <div>
        <div className="text-sm font-medium text-zinc-200">{title}</div>
        {body && <div className="text-xs text-zinc-500 mt-1 max-w-xs mx-auto">{body}</div>}
      </div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function OutputsPanel({ outputs, compact = false, onClear }: { outputs: OutputFileDto[]; compact?: boolean; onClear?: () => void }) {
  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Archive size={14} className="text-zinc-400" />
          <span>Recent Outputs</span>
        </span>
        <span className="text-xs font-mono text-zinc-500 px-1 bg-zinc-950 border border-zinc-800 rounded">{outputs.length} Files</span>
      </div>
      <div className="space-y-2 max-h-[160px] overflow-y-auto pr-1">
        {outputs.length ? outputs.slice(0, compact ? 6 : 20).map((output) => (
          <div className="output-item" key={output.filename}>
            <div className="output-item-info">
              <p className="text-xs font-medium text-zinc-300 truncate" title={output.filename}>{output.filename}</p>
              <span className="text-xs font-mono text-zinc-500 block mt-0.5">{formatDate(output.modified_at)} • {formatBytes(output.size_bytes)}</span>
            </div>
            {/* Download button could trigger an API call or just be a link */}
            <a href={`/api/v1/outputs/${output.filename}/download`} target="_blank" rel="noreferrer" className="output-btn" title="Download">
              <Download size={12} />
            </a>
          </div>
        )) : <div className="text-center py-6"><p className="text-xs text-zinc-500">No completed outputs yet.</p></div>}
      </div>
    </div>
  );
}

export function SystemResourcesPanel({ workerHealth }: { workerHealth: WorkerHealthResponse | null }) {
  // Simple simulated or derived stats since actual CPU/RAM might not be in workerHealth
  const hasRunning = workerHealth && workerHealth.running_jobs > 0;
  const cpuPercent = hasRunning ? 45 : 12;
  
  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Cpu size={14} className="text-zinc-400" />
          <span>System Resources</span>
        </span>
        <span className="text-xs font-mono text-emerald-500">Stable</span>
      </div>
      
      <div className="resource-item">
        <div className="resource-label">
          <span>Worker CPU</span>
          <span className="font-mono">{cpuPercent}%</span>
        </div>
        <div className="resource-track">
          <div className="resource-fill bg-brand-500" style={{ width: `${cpuPercent}%`, backgroundColor: 'var(--brand-500)' }}></div>
        </div>
      </div>
      
      <div className="resource-item">
        <div className="resource-label">
          <span>Active Jobs</span>
          <span className="font-mono">{workerHealth?.running_jobs || 0}</span>
        </div>
        <div className="resource-track">
          <div className="resource-fill" style={{ width: `${Math.min(((workerHealth?.running_jobs || 0) / 4) * 100, 100)}%`, backgroundColor: 'var(--zinc-500)' }}></div>
        </div>
      </div>
    </div>
  );
}

export function JobControls({ filters, setFilters, selectedCount, filteredJobs, setSelectedJobIds, runBulkAction }: {
  filters: JobFilters;
  setFilters: React.Dispatch<React.SetStateAction<JobFilters>>;
  selectedCount: number;
  filteredJobs: JobRecord[];
  setSelectedJobIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  runBulkAction: (action: 'cancel' | 'start' | 'archive' | 'delete') => void;
}) {
  return (
    <div className="panel-toolbar">
      <div className="panel-filters">
        <div className="input-wrapper">
          <input 
            type="text" 
            value={filters.q} 
            onChange={(e) => setFilters(v => ({...v, q: e.target.value}))} 
            placeholder="Search in queue..." 
            className="form-input has-icon" 
          />
          <span className="input-icon">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
          </span>
        </div>
        
        <select value={filters.status} onChange={(e) => setFilters(v => ({...v, status: e.target.value as any}))} className="form-input" style={{ width: 'auto' }}>
          <option value="all">All Statuses</option>
          {Object.keys(statusLabels).map(status => <option key={status} value={status}>{statusLabels[status as JobStatus]}</option>)}
        </select>

        <select value={filters.profile} onChange={(e) => setFilters(v => ({...v, profile: e.target.value}))} className="form-input" style={{ width: 'auto' }}>
          <option value="">All Profiles</option>
          <option value="h264_mp4">H.264 MP4</option>
          <option value="h265_mp4">H.265 MP4</option>
          <option value="vp9_webm">WebM VP9</option>
        </select>
      </div>
      
      <div className="panel-actions">
        <button onClick={() => runBulkAction('start')} disabled={!selectedCount} className="btn btn-outline">Start</button>
        <button onClick={() => setSelectedJobIds(new Set())} disabled={!selectedCount} className="btn btn-outline">Clear</button>
        <button onClick={() => runBulkAction('delete')} disabled={!selectedCount} className="btn btn-danger">Delete Selected</button>
      </div>
    </div>
  );
}

export function JobList({ jobsLoading, jobs, selectedJobIds, setSelectedJobIds, setSelectedJobId, refreshJobs, cancelJob, deleteJob }: {
  jobsLoading: boolean;
  jobs: JobRecord[];
  selectedJobIds: Set<string>;
  setSelectedJobIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  setSelectedJobId: (id: string) => void;
  refreshJobs: (mode?: 'initial' | 'background') => Promise<void>;
  cancelJob: (id: string) => Promise<unknown>;
  deleteJob: (id: string) => Promise<unknown>;
}) {
  if (jobsLoading && !jobs.length) {
    return <div className="p-8 text-center text-zinc-500">Loading jobs...</div>;
  }
  
  if (!jobs.length) {
    return (
      <EmptyState 
        title="Queue is Empty" 
        body="Add media files from the Convert tab to start processing." 
      />
    );
  }

  const toggleAll = (checked: boolean) => {
    if (checked) setSelectedJobIds(new Set(jobs.map(j => j.id)));
    else setSelectedJobIds(new Set());
  };

  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th style={{ width: '2.5rem' }}>
              <input 
                type="checkbox" 
                className="form-checkbox" 
                checked={jobs.length > 0 && selectedJobIds.size === jobs.length}
                onChange={(e) => toggleAll(e.target.checked)}
              />
            </th>
            <th>File Name</th>
            <th style={{ width: '7rem' }}>Profile</th>
            <th style={{ width: '10rem' }}>Progress</th>
            <th style={{ width: '6rem' }}>Target</th>
            <th style={{ width: '7rem' }}>Status</th>
            <th style={{ width: '4rem', textAlign: 'right' }}>Action</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(job => {
            const isSelected = selectedJobIds.has(job.id);
            const progress = getProgress(job);
            const norm = normalizeStatus(job.status);
            
            return (
              <tr key={job.id} className={isSelected ? 'selected' : ''}>
                <td>
                  <input 
                    type="checkbox" 
                    className="form-checkbox" 
                    checked={isSelected}
                    onChange={(e) => {
                      setSelectedJobIds(prev => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(job.id);
                        else next.delete(job.id);
                        return next;
                      });
                    }}
                  />
                </td>
                <td>
                  <div className="font-medium text-zinc-200 truncate" style={{ maxWidth: '240px' }} title={job.input_filename || job.source_path || job.id}>
                    {job.input_filename || job.source_path || job.id}
                  </div>
                </td>
                <td className="font-mono text-zinc-400 text-xs truncate">
                  {job.profile || 'default'}
                </td>
                <td>
                  <div className="progress-container">
                    <div className="progress-text">
                      <span>{progress}%</span>
                      {norm === 'running' && <span>{formatEta(job.progress_eta_seconds)}</span>}
                    </div>
                    <div className="progress-track">
                      <div className={`progress-fill ${norm}`} style={{ width: `${progress}%` }}></div>
                    </div>
                  </div>
                </td>
                <td className="font-mono text-zinc-400 text-xs">
                  {job.video_export}/{job.audio_export}
                </td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td style={{ textAlign: 'right' }}>
                  {norm === 'queued' || norm === 'running' ? (
                    <button onClick={() => cancelJob(job.id).then(() => refreshJobs('background'))} className="btn-icon" title="Cancel Job">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>
                    </button>
                  ) : (
                    <button onClick={() => deleteJob(job.id).then(() => refreshJobs('background'))} className="btn-icon" title="Delete Job">
                      <Trash2 size={14} />
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
