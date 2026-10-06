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
import { Trash2, Download, Layers, Archive, Cpu, X, Play, FileText } from 'lucide-react';
import { useI18n, type MessageKey } from '../i18n';

export function HealthPill({ label, ok, meta }: { label: string; ok: boolean; meta?: string }) {
  const { t } = useI18n();
  return (
    <div
      className="status-item"
      title={`${label}: ${ok ? t('common.status.ok') : t('common.status.error')}${meta ? ` (${meta})` : ''}`}
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
  const { t } = useI18n();
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
      <span>{t(`status.${status}` as MessageKey)}</span>
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
  confirmLabel,
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
  const { t } = useI18n();
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
            {t('common.cancel')}
          </button>
          <button className={danger ? 'btn btn-danger' : 'btn btn-primary'} onClick={onConfirm}>
            {confirmLabel ?? t('common.delete')}
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
  const { language, t } = useI18n();
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
        aria-label={t('job.details', { name })}
      >
        <div className="detail-header">
          <div className="flex items-center gap-2" style={{ minWidth: 0 }}>
            <StatusBadge status={job.status} />
            <h3 className="detail-title truncate" title={name}>
              {name}
            </h3>
          </div>
          <button className="btn-icon" onClick={onClose} aria-label={t('job.closeDetails')}>
            <X size={16} />
          </button>
        </div>

        {job.error_message && (
          <div className="detail-section">
            <span className="detail-section-title">{t('job.error')}</span>
            <div className="form-alert-error font-mono" style={{ overflowWrap: 'anywhere' }}>
              {job.error_message}
            </div>
          </div>
        )}
        {!!job.warnings?.length && (
          <div className="detail-section">
            <span className="detail-section-title">{t('job.warnings')}</span>
            <ul className="settings-help" style={{ overflowWrap: 'anywhere' }}>
              {job.warnings.map((warning, index) => <li key={index}>{warning}</li>)}
            </ul>
          </div>
        )}

        <div className="detail-section">
          <span className="detail-section-title">{t('job.overview')}</span>
          <div className="detail-grid">
            <DetailItem label={t('job.progress')} value={`${getProgress(job)}%`} mono />
            <DetailItem label={t('job.phase')} value={job.progress_phase || '—'} />
            <DetailItem label={t('job.profile')} value={job.profile} mono />
            <DetailItem
              label={t('job.target')}
              value={`${job.video_export} / ${job.audio_export} / ${job.subtitle_export}`}
              mono
            />
            <DetailItem label={t('job.attempts')} value={job.attempt_count} mono />
            <DetailItem
              label={t('job.batch')}
              value={job.batch_id ? job.batch_id.slice(0, 8) : '—'}
              mono
            />
            <DetailItem label={t('job.created')} value={formatDate(job.created_at, language)} />
            <DetailItem label={t('job.started')} value={formatDate(job.started_at, language)} />
            <DetailItem label={t('job.finished')} value={formatDate(job.finished_at, language)} />
            <DetailItem
              label={t('job.output')}
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
            <span className="detail-section-title">{t('job.liveTelemetry')}</span>
            <div className="detail-grid">
              <DetailItem label="FPS" value={job.progress_fps ?? '—'} mono />
              <DetailItem label={t('job.speed')} value={job.progress_speed ?? '—'} mono />
              <DetailItem label={t('job.bitrate')} value={job.progress_bitrate ?? '—'} mono />
              <DetailItem
                label={t('job.eta')}
                value={formatEta(job.progress_eta_seconds, language)}
                mono
              />
            </div>
          </div>
        )}

        {telemetry.length > 1 && (
          <div className="detail-section">
            <span className="detail-section-title">{t('job.progressHistory')}</span>
            <svg
              className="telemetry-chart"
              viewBox="0 0 300 90"
              role="img"
              aria-label={t('job.progressChart')}
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
            <span className="detail-section-title">{t('job.timeline')}</span>
            <ol className="timeline-list">
              {timeline
                .slice()
                .reverse()
                .map((entry, index) => (
                  <li className="timeline-item" key={index}>
                    <span className="timeline-time">{formatDate(entry.at, language)}</span>
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
            <span className="detail-section-title">
              {t('job.logTitle', { count: logTail.length })}
            </span>
            <button
              className="btn btn-outline"
              onClick={() => void downloadJobLog(job.id)}
              title={t('job.downloadLog')}
            >
              <FileText size={12} /> {t('job.fullLog')}
            </button>
          </div>
          {logTail.length ? (
            <pre className="log-box">{logTail.join('\n')}</pre>
          ) : (
            <p className="text-xs text-zinc-500" style={{ margin: 0 }}>
              {t('job.noLog')}
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
  const { language, t } = useI18n();
  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Archive size={14} className="text-zinc-400" />
          <span>{t('outputs.title')}</span>
        </span>
        <div className="flex items-center gap-2">
          {onClear && outputs.length > 0 && (
            <button
              onClick={onClear}
              className="btn-icon"
              title={t('outputs.clear')}
              aria-label={t('outputs.clear')}
            >
              <Trash2 size={14} />
            </button>
          )}
          <span className="text-xs font-mono text-zinc-500">
            {t('outputs.files', { count: outputs.length })}
          </span>
        </div>
      </div>
      <div className="space-y-2 outputs-scroll pr-1">
        {outputs.length ? (
          outputs.slice(0, compact ? 6 : 20).map((output) => (
            <div className="output-item" key={output.filename}>
              <OutputThumbnail
                path={output.thumbnail_url}
                alt={t('outputs.thumbnail', { filename: output.filename })}
              />
              <div className="output-item-info">
                <p className="text-xs font-medium text-zinc-300 truncate" title={output.filename}>
                  {output.filename}
                </p>
                <span className="text-xs font-mono text-zinc-500 block mt-1">
                  {formatDate(output.modified_at, language)} • {formatBytes(output.size_bytes)}
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
                title={t('common.preview')}
                aria-label={t('outputs.previewNamed', { filename: output.filename })}
              >
                <Play size={12} />
              </button>
              <button
                onClick={() => onDownload?.(output.filename)}
                className="output-btn"
                title={t('common.download')}
                aria-label={t('outputs.downloadNamed', { filename: output.filename })}
              >
                <Download size={12} />
              </button>
              {onDelete && (
                <button
                  onClick={() => onDelete(output.filename)}
                  className="output-btn"
                  title={t('common.delete')}
                  aria-label={t('outputs.deleteNamed', { filename: output.filename })}
                >
                  <Trash2 size={12} />
                </button>
              )}
            </div>
          ))
        ) : (
          <div className="text-center py-6">
            <p className="text-xs text-zinc-500">{t('outputs.empty')}</p>
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
  const { t } = useI18n();
  const cpuPercent = workerHealth?.cpu_percent ?? 0;
  const diskPercent = workerHealth?.disk_used_percent ?? 0;

  return (
    <div className="sidebar-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Cpu size={14} className="text-zinc-400" />
          <span>{t('resources.title')}</span>
        </span>
        {!workerHealth ? (
          <span className="text-xs font-mono text-zinc-500">{t('common.offline')}</span>
        ) : cpuPercent > 85 ? (
          <span className="text-xs font-mono text-rose-500">{t('resources.highLoad')}</span>
        ) : cpuPercent > 50 ? (
          <span className="text-xs font-mono text-amber-500">{t('resources.moderate')}</span>
        ) : (
          <span className="text-xs font-mono text-emerald-500">{t('resources.stable')}</span>
        )}
      </div>

      <div className="resource-item">
        <div className="resource-label">
          <span>{t('resources.cpu')}</span>
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
          <span>{t('resources.disk')}</span>
          <span className="font-mono">
            {t('resources.free', {
              value: formatBytes(workerHealth?.disk_free_bytes ?? 0),
            })}
          </span>
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
        <span>{t('resources.workerHeartbeat')}</span>
        <span className={workerHealth?.worker_online ? 'text-emerald-400' : 'text-rose-400'}>
          {workerHealth?.worker_online ? t('common.online') : t('common.offline')}
        </span>
      </div>

      <div className="resource-item">
        <div className="resource-label">
          <span>{t('resources.activeJobs')}</span>
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
  const { t } = useI18n();
  const statuses: JobStatus[] = ['queued', 'running', 'cancelled', 'completed', 'failed'];
  return (
    <div className="panel-toolbar">
      <div className="panel-filters">
        <div className="input-wrapper">
          <input
            type="text"
            value={filters.q}
            onChange={(e) => setFilters((v) => ({ ...v, q: e.target.value }))}
            placeholder={t('job.searchPlaceholder')}
            className="form-input has-icon"
            aria-label={t('job.searchAria')}
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
          aria-label={t('job.filterStatus')}
        >
          <option value="all">{t('job.allStatuses')}</option>
          {statuses.map((status) => (
            <option key={status} value={status}>
              {t(`status.${status}` as MessageKey)}
            </option>
          ))}
        </select>

        <select
          value={filters.profile}
          onChange={(e) => setFilters((v) => ({ ...v, profile: e.target.value }))}
          className="form-input"
          style={{ width: 'auto' }}
          aria-label={t('job.filterProfile')}
        >
          <option value="">{t('job.allProfiles')}</option>
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
          aria-label={t('job.sort')}
        >
          <option value="newest">{t('job.sortNewest')}</option>
          <option value="oldest">{t('job.sortOldest')}</option>
          <option value="progress">{t('job.sortProgress')}</option>
        </select>
      </div>

      <div className="panel-actions">
        <button
          onClick={() => runBulkAction('start')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          {t('common.start')}
        </button>
        <button
          onClick={() => runBulkAction('cancel')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          {t('common.cancel')}
        </button>
        <button
          onClick={() => runBulkAction('archive')}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          {t('common.archive')}
        </button>
        <button
          onClick={() => setSelectedJobIds(new Set())}
          disabled={!selectedCount}
          className="btn btn-outline"
        >
          {t('common.clear')}
        </button>
        <button
          onClick={() => runBulkAction('delete')}
          disabled={!selectedCount}
          className="btn btn-danger"
        >
          {t('job.deleteSelected')}
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
  const { language, t } = useI18n();
  if (jobsLoading && !jobs.length) {
    return <div className="p-8 text-center text-zinc-500">{t('job.loading')}</div>;
  }

  if (!jobs.length) {
    return <EmptyState title={t('job.emptyTitle')} body={t('job.emptyBody')} />;
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
                aria-label={t('job.selectAll')}
              />
            </th>
            <th>{t('job.fileName')}</th>
            <th style={{ width: '7rem' }}>{t('job.profile')}</th>
            <th style={{ width: '10rem' }}>{t('job.progress')}</th>
            <th style={{ width: '6rem' }}>{t('job.target')}</th>
            <th style={{ width: '7rem' }}>{t('job.status')}</th>
            <th style={{ width: '4rem', textAlign: 'right' }}>{t('job.action')}</th>
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
                <td className="job-select-cell" onClick={(e) => e.stopPropagation()}>
                  <input
                    type="checkbox"
                    className="form-checkbox"
                    checked={isSelected}
                    aria-label={t('job.select', { name })}
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
                <td className="job-name-cell">
                  <div
                    className="font-medium text-zinc-200 truncate"
                    style={{ maxWidth: '240px' }}
                    title={name}
                  >
                    {name}
                  </div>
                </td>
                <td className="job-profile-cell font-mono text-zinc-400 text-xs truncate">
                  {job.profile || t('common.default')}
                </td>
                <td className="job-progress-cell">
                  <div className="progress-container">
                    <div className="progress-text">
                      <span>{progress}%</span>
                      {variant === 'running' && (
                        <span>{formatEta(job.progress_eta_seconds, language)}</span>
                      )}
                    </div>
                    <div className="progress-track">
                      <div
                        className={`progress-fill ${variant}`}
                        style={{ width: `${progress}%` }}
                      ></div>
                    </div>
                  </div>
                </td>
                <td className="job-target-cell font-mono text-zinc-400 text-xs">
                  {job.status === 'queued' && job.queue_position
                    ? `#${job.queue_position} · ${formatEta(job.estimated_start_seconds, language)}`
                    : `${job.video_export}/${job.audio_export}`}
                </td>
                <td className="job-status-cell">
                  <StatusBadge status={job.status} />
                  {!!job.warnings?.length && (
                    <span className="text-xs" title={job.warnings.join('\n')}>{t('job.warnings')} ({job.warnings.length})</span>
                  )}
                </td>
                <td
                  className="job-action-cell"
                  style={{ textAlign: 'right' }}
                  onClick={(e) => e.stopPropagation()}
                >
                  {variant === 'queued' || variant === 'running' ? (
                    <button
                      onClick={() => onCancelJob(job.id)}
                      className="btn-icon"
                      title={t('job.cancel')}
                      aria-label={t('job.cancelNamed', { name })}
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
                      title={t('job.delete')}
                      aria-label={t('job.deleteNamed', { name })}
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
  const { t } = useI18n();
  if (!batches.length) return null;
  return (
    <div className="panel batch-panel">
      <div className="sidebar-header">
        <span className="sidebar-title">
          <Layers size={14} /> {t('batches.title')}
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
              {t('batches.summary', {
                completed: batch.completed,
                running: batch.running,
                failed: batch.failed,
              })}
            </p>
            <div className="batch-actions">
              <button
                className="btn btn-outline"
                onClick={() => onAction(batch.batch_id, 'cancel')}
              >
                {t('common.cancel')}
              </button>
              <button className="btn btn-outline" onClick={() => onAction(batch.batch_id, 'retry')}>
                {t('common.retry')}
              </button>
              <button
                className="btn btn-outline"
                onClick={() => onAction(batch.batch_id, 'archive')}
              >
                {t('common.archive')}
              </button>
              <button className="btn btn-danger" onClick={() => onAction(batch.batch_id, 'delete')}>
                {t('common.delete')}
              </button>
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}
