import React from 'react';
import type { JobStatus, JobRecord, JobFilters, OutputFileDto, WorkerHealthResponse, MediaRootDto } from '../models';
import { normalizeStatus, getProgress, formatEta, formatDate, formatBytes } from '../utils/helpers';
import { statusLabels } from '../utils/constants';

// ---------------------------------------------------------------------------
// Small presentational components
// ---------------------------------------------------------------------------

export function HealthPill({ label, ok, meta }: { label: string; ok: boolean; meta?: string }) {
  return <span className={`health-pill ${ok ? 'ok' : 'bad'}`}><i />{label}{meta ? <small>{meta}</small> : null}</span>;
}

export function CardHeader({ title, badge }: { title: string; badge?: string }) {
  return <div className="card-header"><h3>{title}</h3>{badge ? <span className="soft-badge">{badge}</span> : null}</div>;
}

export function SummaryCard({ label, value, tone, suffix = '' }: { label: string; value: number; tone: string; suffix?: string }) {
  return <div className={`summary-card ${tone}`}><span>{label}</span><strong>{value}{suffix}</strong></div>;
}

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`status-badge ${normalizeStatus(status)}`}>{statusLabels[status]}</span>;
}

export function EmptyState({ title, body }: { title: string; body?: string }) {
  return <div className="empty-state"><strong>{title}</strong>{body ? <span>{body}</span> : null}</div>;
}

export function ViewTabs({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <div className="tabs" role="tablist" aria-label="Dashboard sections">{(['queue', 'history', 'outputs', 'advanced'] as const).map((tab) => <button key={tab} type="button" role="tab" aria-selected={value === tab} className={`tab-button ${value === tab ? 'active' : ''}`} onClick={() => onChange(tab)}>{tab[0].toUpperCase() + tab.slice(1)}</button>)}</div>;
}

// ---------------------------------------------------------------------------
// MiniJob
// ---------------------------------------------------------------------------

export function MiniJob({ job, onOpen }: { job: JobRecord; onOpen: () => void }) {
  return <button className="mini-job" type="button" onClick={onOpen}><span><strong>{job.input_filename || job.source_path || job.id}</strong><small>{job.progress_phase || job.status} · {formatEta(job.progress_eta_seconds)}</small></span><b>{getProgress(job)}%</b></button>;
}

// ---------------------------------------------------------------------------
// JobControls
// ---------------------------------------------------------------------------

export function JobControls({ filters, setFilters, selectedCount, filteredJobs, setSelectedJobIds, runBulkAction }: {
  filters: JobFilters;
  setFilters: React.Dispatch<React.SetStateAction<JobFilters>>;
  selectedCount: number;
  filteredJobs: JobRecord[];
  setSelectedJobIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  runBulkAction: (action: 'cancel' | 'start' | 'archive' | 'delete') => void;
}) {
  return (
    <>
      <div className="filter-row">
        <div className="search-field">
          <input value={filters.q} onChange={(event) => setFilters((value) => ({ ...value, q: event.target.value }))} placeholder="Search jobs" />
          <button className="search-clear-button" type="button" onClick={() => setFilters((value) => ({ ...value, q: '' }))} disabled={!filters.q} aria-label="Clear jobs search" title="Clear jobs search"><span aria-hidden="true">✕</span></button>
        </div>
        <select value={filters.status} onChange={(event) => setFilters((value) => ({ ...value, status: event.target.value as JobFilters['status'] }))} aria-label="Filter by status"><option value="all">All statuses</option>{Object.keys(statusLabels).map((status) => <option key={status} value={status}>{statusLabels[status as JobStatus]}</option>)}</select>
        <select value={filters.profile} onChange={(event) => setFilters((value) => ({ ...value, profile: event.target.value }))} aria-label="Filter by profile"><option value="">All profiles</option><option value="h264_mp4">H.264</option><option value="h265_mp4">H.265</option><option value="vp9_webm">VP9</option></select>
        <select value={filters.sourceType} onChange={(event) => setFilters((value) => ({ ...value, sourceType: event.target.value as JobFilters['sourceType'] }))} aria-label="Filter by source type"><option value="all">All sources</option><option value="server">Server</option><option value="legacy">Legacy/upload</option></select>
        <select value={filters.sort} onChange={(event) => setFilters((value) => ({ ...value, sort: event.target.value as JobFilters['sort'] }))} aria-label="Sort jobs"><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="progress">Progress</option></select>
      </div>
      <div className="bulk-row">
        <span>{selectedCount} selected</span>
        <button className="ghost-button tiny" type="button" onClick={() => setSelectedJobIds(new Set(filteredJobs.map((job) => job.id)))}>Select visible</button>
        <button className="ghost-button tiny" type="button" onClick={() => setSelectedJobIds(new Set())}>Clear</button>
        <button className="ghost-button tiny" type="button" onClick={() => runBulkAction('start')} disabled={!selectedCount}>Start</button>
        <button className="ghost-button tiny" type="button" onClick={() => runBulkAction('cancel')} disabled={!selectedCount}>Cancel</button>
        <button className="ghost-button tiny" type="button" onClick={() => runBulkAction('archive')} disabled={!selectedCount}>Archive</button>
        <button className="danger-button tiny" type="button" onClick={() => runBulkAction('delete')} disabled={!selectedCount}>Delete</button>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// JobList
// ---------------------------------------------------------------------------

export function JobList({ jobsLoading, jobs, selectedJobIds, setSelectedJobIds, setSelectedJobId, refreshJobs, cancelJob }: {
  jobsLoading: boolean;
  jobs: JobRecord[];
  selectedJobIds: Set<string>;
  setSelectedJobIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  setSelectedJobId: (id: string) => void;
  refreshJobs: (mode?: 'initial' | 'background') => Promise<void>;
  cancelJob: (id: string) => Promise<unknown>;
}) {
  return (
    <div className="job-list" aria-live={jobsLoading ? 'polite' : 'off'}>
      {jobsLoading && !jobs.length ? <EmptyState title="Loading jobs…" /> : jobs.length ? jobs.map((job) => (
        <article className="job-row" key={job.id}>
          <input aria-label={`Select job ${job.id}`} type="checkbox" checked={selectedJobIds.has(job.id)} onChange={(event) => setSelectedJobIds((ids) => { const next = new Set(ids); if (event.target.checked) next.add(job.id); else next.delete(job.id); return next; })} />
          <div className="job-main">
            <div className="job-title-line"><strong>{job.input_filename || job.source_path || job.id}</strong><StatusBadge status={job.status} /></div>
            <small>{job.source_root_key ? 'Server source' : 'Legacy input'} · {job.video_export}/{job.audio_export}/{job.subtitle_export} · {formatDate(job.created_at)}</small>
            <div className="progress-line"><span style={{ width: `${getProgress(job)}%` }} /></div>
            <small>{job.progress_phase || 'queued'} · {job.progress_message || `${getProgress(job)}%`} · {formatEta(job.progress_eta_seconds)} · {job.progress_fps ? `${job.progress_fps.toFixed(1)} fps` : 'fps —'} · {job.progress_speed || 'speed —'}</small>
            {job.error_message ? <p className="error-text">{job.error_message}</p> : null}
          </div>
          <div className="job-actions">
            <span className="progress-number">{getProgress(job)}%</span>
            <button className="ghost-button tiny" type="button" onClick={() => setSelectedJobId(job.id)}>Open</button>
            {['queued', 'running'].includes(job.status) ? <button className="ghost-button tiny" type="button" onClick={() => void cancelJob(job.id).then(() => refreshJobs('background'))}>Cancel</button> : null}
          </div>
        </article>
      )) : <EmptyState title="No jobs found" body="Queue jobs from Convert or adjust filters." />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// OutputsPanel
// ---------------------------------------------------------------------------

export function OutputsPanel({ outputs, compact = false, onClear }: { outputs: OutputFileDto[]; compact?: boolean; onClear?: () => void }) {
  return (
    <section className={`card outputs-card ${compact ? 'compact-outputs' : ''}`}>
      <div className="card-header"><h3>Recent outputs</h3><span className="card-header-actions">{onClear && outputs.length ? <button className="ghost-button tiny" type="button" onClick={onClear}>Clear</button> : null}<span className="soft-badge">{outputs.length} files</span></span></div>
      <div className="output-list">
        {outputs.length ? outputs.slice(0, compact ? 6 : 20).map((output) => (
          <div className="output-tile" key={output.filename}>
            <strong>{output.filename}</strong>
            <small>{formatBytes(output.size_bytes)} · {formatDate(output.modified_at)}</small>
          </div>
        )) : <EmptyState title="No outputs yet" body="Completed conversions will appear here when available." />}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// AdvancedPanel
// ---------------------------------------------------------------------------

export function AdvancedPanel({ streamState, workerHealth, roots }: {
  streamState: string;
  workerHealth: WorkerHealthResponse | null;
  roots: MediaRootDto[];
}) {
  return (
    <div className="advanced-grid">
      <div className="detail-grid">
        <div><dt>Live channel</dt><dd>{streamState === 'live' ? 'SSE connected' : 'Polling fallback'}</dd></div>
        <div><dt>Worker</dt><dd>{workerHealth ? `${workerHealth.status} · ${workerHealth.running_jobs} active · ${workerHealth.queue_depth} queued` : 'unknown'}</dd></div>
        <div><dt>Media roots</dt><dd>{roots.length ? roots.map((root) => root.label).join(', ') : 'none'}</dd></div>
      </div>
      <EmptyState title="Safe browsing only" body="Arbitrary path entry remains disabled; only configured allowlisted media roots can be browsed." />
    </div>
  );
}
