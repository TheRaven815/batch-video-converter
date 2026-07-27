import React, { useEffect } from 'react';
import type {
  BatchSummaryDto,
  JobStatus,
  JobRecord,
  JobFilters,
  OutputFileDto,
  WorkerHealthResponse,
} from '../models';
import { downloadJobLog, downloadOutput, getProtectedMediaUrl } from '../api';
import { statusVariant, getProgress, formatEta, formatDate, formatBytes } from '../utils/helpers';
import { statusLabels } from '../utils/constants';
import { Trash2, Download, Layers, Archive, Cpu, X, Play, FileText } from 'lucide-react';

export function HealthPill({ label, ok, meta }: { label: string; ok: boolean; meta?: string }) {
  return (
    <div
      className="status-item"
      title={`${label}: ${ok ? 'OK' : 'Error'}${meta ? ` (${meta})` : ''}`}
    >
      <span className={`status-dot ${ok ? 'ok' : 'error'} ${ok && !meta ? 'pulse' : ''}`}></span>
      <span>
        {label}
        {meta ? (
          <span>
            : <span className="font-mono text-zinc-200">{meta}</span>
          </span>
        ) : null}
      </span>
    </div>
  );
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const variant = statusVariant(status);
  let icon = null;
  if (variant === 'done')
    icon = (
      <span
        style={{
          width: '4px',
          height: '4px',
          display: 'inline-block',
          borderRadius: '50%',
          backgroundColor: 'var(--emerald-400)',
        }}
      ></span>
    );
  if (variant === 'running')
    icon = (
      <span
        className="pulse"
        style={{
          width: '4px',
          height: '4px',
          display: 'inline-block',
          borderRadius: '50%',
          backgroundColor: 'var(--blue-400)',
        }}
      ></span>
    );

  return (
    <span className={`badge badge-${variant}`}>
      {icon}
      <span>{statusLabels[status] || status}</span>
    </span>
  );
}

export function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body?: string;
  action?: React.ReactNode;
}) {
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

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = 'Delete',
  danger = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div
        className="modal-card"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="modal-title">{title}</h3>
        <p className="modal-body">{body}</p>
        <div className="modal-actions">
          <button className="btn btn-outline" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function DetailItem({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="detail-item">
      <span className="detail-label">{label}</span>
      <span className={`detail-value ${mono ? 'font-mono' : ''}`}>{value ?? '—'}</span>
    </div>
  );
}

export function JobDetailDrawer({ job, onClose }: { job: JobRecord | null; onClose: () => void }) {
  useEffect(() => {
    if (!job) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    document.body.classList.add('drawer-open');
    return () => {
      window.removeEventListener('keydown', onKey);
      document.body.classList.remove('drawer-open');
    };
  }, [job, onClose]);

  if (!job) return null;

  const name = job.input_filename || job.source_path || job.id;
  const isRunning = statusVariant(job.status) === 'running';
  const timeline = Array.isArray(job.timeline) ? job.timeline : [];
  const logTail = Array.isArray(job.log_tail) ? job.log_tail : [];
  const telemetry = Array.isArray(job.telemetry_history) ? job.telemetry_history : [];

  return (
    <>
      <div className="detail-overlay" onClick={onClose}></div>
      <aside
        className="detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Job details: ${name}`}
      >
        <div className="detail-header">
          <div className="flex items-center gap-2" style={{ minWidth: 0 }}>
            <StatusBadge status={job.status} />
            <h3 className="detail-title truncate" title={name}>
              {name}
            </h3>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label="Close details">
            <X size={16} />
          </button>
        </div>

        {job.error_message && (
          <div className="detail-section">
            <span className="detail-section-title">Error</span>
            <div className="form-alert-error font-mono" style={{ overflowWrap: 'anywhere' }}>
              {job.error_message}
            </div>
          </div>
        )}

        <div className="detail-section">
          <span className="detail-section-title">Overview</span>
          <div className="detail-grid">
            <DetailItem label="Progress" value={`${getProgress(job)}%`} mono />
            <DetailItem label="Phase" value={job.progress_phase || '—'} />
            <DetailItem label="Profile" value={job.profile} mono />
            <DetailItem
              label="Target"
              value={`${job.video_export} / ${job.audio_export} / ${job.subtitle_export}`}
              mono
            />
            <DetailItem label="Attempts" value={job.attempt_count} mono />
            <DetailItem label="Batch" value={job.batch_id ? job.batch_id.slice(0, 8) : '—'} mono />
            <DetailItem label="Created" value={formatDate(job.created_at)} />
            <DetailItem label="Started" value={formatDate(job.started_at)} />
            <DetailItem label="Finished" value={formatDate(job.finished_at)} />
            <DetailItem
              label="Output"
              value={
                job.output_filename ? (
                  <button
                    className="detail-link"
                    onClick={() => void downloadOutput(job.output_filename!)}
                  >
                    {job.output_filename}
                  </button>
                ) : (
                  '—'
                )
              }
              mono
            />
          </div>
        </div>

        {isRunning && (
          <div className="detail-section">
            <span className="detail-section-title">Live Telemetry</span>
            <div className="detail-grid">
              <DetailItem label="FPS" value={job.progress_fps ?? '—'} mono />
              <DetailItem label="Speed" value={job.progress_speed ?? '—'} mono />
              <DetailItem label="Bitrate" value={job.progress_bitrate ?? '—'} mono />
              <DetailItem label="ETA" value={formatEta(job.progress_eta_seconds)} mono />
            </div>
          </div>
        )}

        {telemetry.length > 1 && (
          <div className="detail-section">
            <span className="detail-section-title">Progress History</span>
            <svg
              className="telemetry-chart"
              viewBox="0 0 300 90"
              role="img"
              aria-label="Conversion progress over time"
            >
              <polyline
                points={telemetry
                  .map((point, index) => {
                    const x = (index / Math.max(1, telemetry.length - 1)) * 300;
                    const y = 86 - (Number(point.progress_percent ?? 0) / 100) * 82;
                    return `${x},${y}`;
                  })
                  .join(' ')}
              />
            </svg>
          </div>
        )}

        {timeline.length > 0 && (
          <div className="detail-section">
            <span className="detail-section-title">Timeline</span>
            <ol className="timeline-list">
              {timeline
                .slice()
                .reverse()
                .map((entry, index) => (
                  <li className="timeline-item" key={index}>
                    <span className="timeline-time">{formatDate(entry.at)}</span>
                    <span>
                      <span className="text-zinc-300">{entry.phase || entry.status}</span>
                      {entry.message ? (
                        <span className="text-zinc-500"> — {entry.message}</span>
                      ) : null}
                    </span>
                  </li>
                ))}
            </ol>
          </div>
        )}

        <div className="detail-section">
          <div className="detail-section-header">
            <span className="detail-section-title">FFmpeg Log (last {logTail.length} lines)</span>
            <button
              className="btn btn-outline"
              onClick={() => void downloadJobLog(job.id)}
              title="Download complete FFmpeg log"
            >
              <FileText size={12} /> Full log
            </button>
          </div>
          {logTail.length ? (
            <pre className="log-box">{logTail.join('\n')}</pre>
          ) : (
            <p className="text-xs text-zinc-500" style={{ margin: 0 }}>
              No log output yet.
            </p>
          )}
        </div>
      </aside>
    </>
  );
}

export function OutputsPanel({
  outputs,
  compact = false,
  onClear,
  onDownload,
  onDelete,
}: {
  outputs: OutputFileDto[];
  compact?: boolean;
  onClear?: () => void;
  onDownload?: (filename: string) => void;
  onDelete?: (filename: string) => void;
}) {
  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Archive size={14} className="text-zinc-400" />
          <span>Recent Outputs</span>
        </span>
        <div className="flex items-center gap-2">
          {onClear && outputs.length > 0 && (
            <button
              onClick={onClear}
              className="btn-icon"
              title="Clear All Outputs"
              aria-label="Clear all outputs"
            >
              <Trash2 size={14} />
            </button>
          )}
          <span className="text-xs font-mono text-zinc-500">{outputs.length} Files</span>
        </div>
      </div>
      <div className="space-y-2 outputs-scroll pr-1">
        {outputs.length ? (
          outputs.slice(0, compact ? 6 : 20).map((output) => (
            <div className="output-item" key={output.filename}>
              <OutputThumbnail
                path={output.thumbnail_url}
                alt={`Thumbnail for ${output.filename}`}
              />
              <div className="output-item-info">
                <p className="text-xs font-medium text-zinc-300 truncate" title={output.filename}>
                  {output.filename}
                </p>
                <span className="text-xs font-mono text-zinc-500 block mt-1">
                  {formatDate(output.modified_at)} • {formatBytes(output.size_bytes)}
                </span>
              </div>
              <button
                onClick={() => {
                  if (!output.preview_url) return;
                  void getProtectedMediaUrl(output.preview_url).then((url) =>
                    window.open(url, '_blank', 'noopener,noreferrer'),
                  );
                }}
                className="output-btn"
                title="Preview"
                aria-label={`Preview ${output.filename}`}
              >
                <Play size={12} />
              </button>
              <button
                onClick={() => onDownload?.(output.filename)}
                className="output-btn"
                title="Download"
                aria-label={`Download ${output.filename}`}
              >
                <Download size={12} />
              </button>
              {onDelete && (
                <button
                  onClick={() => onDelete(output.filename)}
                  className="output-btn"
                  title="Delete"
                  aria-label={`Delete ${output.filename}`}
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          ))
        ) : (
          <div className="text-center py-6">
            <p className="text-xs text-zinc-500">No completed outputs yet.</p>
          </div>
        )}
      </div>
    </div>
  );
}

function OutputThumbnail({ path, alt }: { path?: string | null; alt: string }) {
  const [url, setUrl] = React.useState<string | null>(null);
  useEffect(() => {
    let active = true;
    if (path) {
      void getProtectedMediaUrl(path).then((resolved) => active && setUrl(resolved));
    }
    return () => {
      active = false;
    };
  }, [path]);
  return url ? <img className="output-thumbnail" src={url} alt={alt} loading="lazy" /> : null;
}

export function SystemResourcesPanel({
  workerHealth,
}: {
  workerHealth: WorkerHealthResponse | null;
}) {
  const cpuPercent = workerHealth?.cpu_percent ?? 0;
  const diskPercent = workerHealth?.disk_used_percent ?? 0;

  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Cpu size={14} className="text-zinc-400" />
          <span>System Resources</span>
        </span>
        {!workerHealth ? (
          <span className="text-xs font-mono text-zinc-500">Offline</span>
        ) : cpuPercent > 85 ? (
          <span className="text-xs font-mono text-rose-500">High Load</span>
        ) : cpuPercent > 50 ? (
          <span className="text-xs font-mono text-amber-500">Moderate</span>
        ) : (
          <span className="text-xs font-mono text-emerald-500">Stable</span>
        )}
      </div>

      <div className="resource-item">
        <div className="resource-label">
          <span>CPU Usage</span>
          <span className="font-mono">{cpuPercent}%</span>
        </div>
        <div className="resource-track">
          <div
            className="resource-fill"
            style={{ width: `${cpuPercent}%`, backgroundColor: 'var(--brand-500)' }}
          ></div>
        </div>
      </div>
      <div className="resource-item">
        <div className="resource-label">
          <span>Disk</span>
          <span className="font-mono">{formatBytes(workerHealth?.disk_free_bytes ?? 0)} free</span>
        </div>
        <div className="resource-track">
          <div
            className="resource-fill"
            style={{
              width: `${diskPercent}%`,
              backgroundColor: diskPercent > 90 ? 'var(--rose-500)' : 'var(--emerald-500)',
            }}
          />
        </div>
      </div>
      <div className="resource-label">
        <span>Worker heartbeat</span>
        <span className={workerHealth?.worker_online ? 'text-emerald-400' : 'text-rose-400'}>
          {workerHealth?.worker_online ? 'Online' : 'Offline'}
        </span>
      </div>

      <div className="resource-item">
        <div className="resource-label">
          <span>Active Jobs</span>
          <span className="font-mono">{workerHealth?.running_jobs || 0}</span>
        </div>
        <div className="resource-track">
          <div
            className="resource-fill"
            style={{
              width: `${Math.min(((workerHealth?.running_jobs || 0) / 4) * 100, 100)}%`,
              backgroundColor: 'var(--zinc-500)',
            }}
          ></div>
        </div>
      </div>
    </div>
  );
}

export function JobControls({
  filters,
  setFilters,
  selectedCount,
  setSelectedJobIds,
  runBulkAction,
}: {
  filters: JobFilters;
  setFilters: React.Dispatch<React.SetStateAction<JobFilters>>;
  selectedCount: number;
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
            onChange={(e) => setFilters((v) => ({ ...v, q: e.target.value }))}
            placeholder="Search in queue..."
            className="form-input has-icon"
            aria-label="Search jobs"
          />
          <span className="input-icon">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="11" cy="11" r="8"></circle>
              <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
            </svg>
          </span>
        </div>

        <select
          value={filters.status}
          onChange={(e) =>
            setFilters((v) => ({ ...v, status: e.target.value as JobFilters['status'] }))
          }
          className="form-input"
          style={{ width: 'auto' }}
          aria-label="Filter by status"
        >
          <option value="all">All Statuses</option>
          {Object.keys(statusLabels).map((status) => (
            <option key={status} value={status}>
              {statusLabels[status as JobStatus]}
            </option>
          ))}
        </select>

        <select
          value={filters.profile}
          onChange={(e) => setFilters((v) => ({ ...v, profile: e.target.value }))}
          className="form-input"
          style={{ width: 'auto' }}
          aria-label="Filter by profile"
        >
          <option value="">All Profiles</option>
          <option value="h264_mp4">H.264 MP4</option>
          <option value="h265_mp4">H.265 MKV</option>
          <option value="vp9_webm">WebM VP9</option>
        </select>

        <select
          value={filters.sort}
          onChange={(e) =>
            setFilters((v) => ({ ...v, sort: e.target.value as JobFilters['sort'] }))
          }
          className="form-input"
          style={{ width: 'auto' }}
          aria-label="Sort jobs"
        >
          <option value="newest">Newest First</option>
          <option value="oldest">Oldest First</option>
          <option value="progress">By Progress</option>
        </select>
      </div>

      <div className="panel-actions">
        <button
          onClick={() => runBulkAction('start')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          Start
        </button>
        <button
          onClick={() => runBulkAction('cancel')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          Cancel
        </button>
        <button
          onClick={() => runBulkAction('archive')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          Archive
        </button>
        <button
          onClick={() => setSelectedJobIds(new Set())}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          Clear
        </button>
        <button
          onClick={() => runBulkAction('delete')}
          disabled={!selectedCount}
          className="btn btn-danger"
        >
          Delete Selected
        </button>
      </div>
    </div>
  );
}

export function JobList({
  jobsLoading,
  jobs,
  selectedJobIds,
  setSelectedJobIds,
  onOpenDetail,
  onCancelJob,
  onDeleteJob,
}: {
  jobsLoading: boolean;
  jobs: JobRecord[];
  selectedJobIds: Set<string>;
  setSelectedJobIds: React.Dispatch<React.SetStateAction<Set<string>>>;
  onOpenDetail: (job: JobRecord) => void;
  onCancelJob: (id: string) => void;
  onDeleteJob: (id: string) => void;
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

  const allVisibleSelected = jobs.length > 0 && jobs.every((job) => selectedJobIds.has(job.id));

  const toggleAll = (checked: boolean) => {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (checked) jobs.forEach((job) => next.add(job.id));
      else jobs.forEach((job) => next.delete(job.id));
      return next;
    });
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
                checked={allVisibleSelected}
                onChange={(e) => toggleAll(e.target.checked)}
                aria-label="Select all visible jobs"
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
          {jobs.map((job) => {
            const isSelected = selectedJobIds.has(job.id);
            const progress = getProgress(job);
            const variant = statusVariant(job.status);
            const name = job.input_filename || job.source_path || job.id;

            return (
              <tr
                key={job.id}
                className={`row-clickable ${isSelected ? 'selected' : ''}`}
                onClick={() => onOpenDetail(job)}
              >
                <td onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    className="form-checkbox"
                    checked={isSelected}
                    aria-label={`Select ${name}`}
                    onChange={(e) => {
                      setSelectedJobIds((prev) => {
                        const next = new Set(prev);
                        if (e.target.checked) next.add(job.id);
                        else next.delete(job.id);
                        return next;
                      });
                    }}
                  />
                </td>
                <td>
                  <div
                    className="font-medium text-zinc-200 truncate"
                    style={{ maxWidth: '240px' }}
                    title={name}
                  >
                    {name}
                  </div>
                </td>
                <td className="font-mono text-zinc-400 text-xs truncate">
                  {job.profile || 'default'}
                </td>
                <td>
                  <div className="progress-container">
                    <div className="progress-text">
                      <span>{progress}%</span>
                      {variant === 'running' && <span>{formatEta(job.progress_eta_seconds)}</span>}
                    </div>
                    <div className="progress-track">
                      <div
                        className={`progress-fill ${variant}`}
                        style={{ width: `${progress}%` }}
                      ></div>
                    </div>
                  </div>
                </td>
                <td className="font-mono text-zinc-400 text-xs">
                  {job.status === 'queued' && job.queue_position
                    ? `#${job.queue_position} · ETA ${formatEta(job.estimated_start_seconds).replace('ETA ', '')}`
                    : `${job.video_export}/${job.audio_export}`}
                </td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td style={{ textAlign: 'right' }} onClick={(e) => e.stopPropagation()}>
                  {variant === 'queued' || variant === 'running' ? (
                    <button
                      onClick={() => onCancelJob(job.id)}
                      className="btn-icon"
                      title="Cancel Job"
                      aria-label={`Cancel ${name}`}
                    >
                      <svg
                        width="14"
                        height="14"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                      >
                        <circle cx="12" cy="12" r="10"></circle>
                        <line x1="15" y1="9" x2="9" y2="15"></line>
                        <line x1="9" y1="9" x2="15" y2="15"></line>
                      </svg>
                    </button>
                  ) : (
                    <button
                      onClick={() => onDeleteJob(job.id)}
                      className="btn-icon"
                      title="Delete Job"
                      aria-label={`Delete ${name}`}
                    >
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

export function BatchPanel({
  batches,
  onAction,
}: {
  batches: BatchSummaryDto[];
  onAction: (batchId: string, action: 'cancel' | 'retry' | 'archive' | 'delete') => void;
}) {
  if (!batches.length) return null;
  return (
    <div className="panel batch-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Layers size={14} /> Recent Batches
        </span>
      </div>
      <div className="batch-grid">
        {batches.slice(0, 8).map((batch) => (
          <article className="batch-card" key={batch.batch_id}>
            <div className="resource-label">
              <strong className="font-mono">{batch.batch_id.slice(0, 8)}</strong>
              <span>{batch.progress_percent}%</span>
            </div>
            <div className="progress-track">
              <div
                className="progress-fill running"
                style={{ width: `${batch.progress_percent}%` }}
              />
            </div>
            <p className="text-xs text-zinc-500">
              {batch.completed} done · {batch.running} running · {batch.failed} failed
            </p>
            <div className="batch-actions">
              <button
                className="btn btn-outline"
                onClick={() => onAction(batch.batch_id, 'cancel')}
              >
                Cancel
              </button>
              <button className="btn btn-outline" onClick={() => onAction(batch.batch_id, 'retry')}>
                Retry
              </button>
              <button
                className="btn btn-outline"
                onClick={() => onAction(batch.batch_id, 'archive')}
              >
                Archive
              </button>
              <button className="btn btn-danger" onClick={() => onAction(batch.batch_id, 'delete')}>
                Delete
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
