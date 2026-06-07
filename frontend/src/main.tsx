import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  browseMedia,
  bulkArchive,
  bulkCancel,
  bulkDelete,
  bulkStart,
  cancelJob,
  createJobsBatch,
  getLiveHealth,
  getReadyHealth,
  getWorkerHealth,
  listBatches,
  listJobs,
  listMediaRoots,
  listOutputs,
  probeSubtitles,
  validateJobs,
} from './api';
import type {
  AudioExport,
  BatchSummaryDto,
  ExportSettings,
  JobFilters,
  JobRecord,
  JobStatus,
  MediaBrowseEntryDto,
  MediaRootDto,
  OutputFileDto,
  StagedServerFile,
  SubtitleExport,
  VideoExport,
  WorkerHealthResponse,
  JobStreamPayload,
} from './models';
import './styles.css';

import {
  allowedExtensions,
  pollMs,
  defaultSettings,
  loadStoredPresets,
  type AppPage,
  type DashboardView,
  type LocalPreset,
} from './utils/constants';
import {
  deriveProfile,
  fileName,
  formatDate,
  formatEta,
  getProgress,
  mergeJobs,
  mergeOutputs,
  normalizeStatus,
  sortJobs,
  uniqueLanguages,
  createPresetId,
} from './utils/helpers';
import {
  HealthPill,
  CardHeader,
  SummaryCard,
  StatusBadge,
  EmptyState,
  ViewTabs,
  MiniJob,
  JobControls,
  JobList,
  OutputsPanel,
  AdvancedPanel,
} from './components/ui';

function App() {
  const [roots, setRoots] = useState<MediaRootDto[]>([]);
  const [selectedRootKey, setSelectedRootKey] = useState('');
  const [currentPath, setCurrentPath] = useState('');
  const [browserQuery, setBrowserQuery] = useState('');
  const [entries, setEntries] = useState<MediaBrowseEntryDto[]>([]);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [staged, setStaged] = useState<StagedServerFile[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [outputs, setOutputs] = useState<OutputFileDto[]>([]);
  const [batches, setBatches] = useState<BatchSummaryDto[]>([]);
  const [workerHealth, setWorkerHealth] = useState<WorkerHealthResponse | null>(null);
  const [apiHealthy, setApiHealthy] = useState(false);
  const [redisHealthy, setRedisHealthy] = useState(false);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [browserLoading, setBrowserLoading] = useState(false);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [jobsRefreshing, setJobsRefreshing] = useState(false);
  const [manualRefreshFeedback, setManualRefreshFeedback] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<string>('');
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [activePage, setActivePage] = useState<AppPage>('dashboard');
  const [dashboardView, setDashboardView] = useState<DashboardView>('queue');
  const [streamState, setStreamState] = useState<'connecting' | 'live' | 'fallback'>('connecting');
  const [announcement, setAnnouncement] = useState('Queue updates will be announced here.');
  const [submitSummary, setSubmitSummary] = useState('');
  const [filters, setFilters] = useState<JobFilters>({ q: '', status: 'all', sort: 'newest', profile: '', sourceType: 'all' });
  const [settings, setSettings] = useState<ExportSettings>(defaultSettings);
  const [presets, setPresets] = useState<LocalPreset[]>(() => loadStoredPresets());
  const [selectedPresetId, setSelectedPresetId] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);

  const toastTimer = useRef<number | null>(null);
  const manualRefreshFeedbackTimer = useRef<number | null>(null);
  const refreshInFlight = useRef(false);
  const jobsLoaded = useRef(false);
  const sseActiveRef = useRef(false);
  const pollTimerRef = useRef<number | null>(null);

  const selectedRoot = useMemo(() => roots.find((root) => root.key === selectedRootKey), [roots, selectedRootKey]);
  const selectedEntries = useMemo(() => entries.filter((entry) => entry.type === 'file' && selectedPaths.has(entry.rel_path)), [entries, selectedPaths]);
  const selectedStageCount = staged.filter((item) => item.selected).length;
  const subtitleLanguages = useMemo(() => uniqueLanguages(staged), [staged]);
  const filteredJobs = useMemo(() => {
    const base = dashboardView === 'history' ? jobs.filter((job) => ['completed', 'failed', 'cancelled'].includes(job.status)) : jobs;
    return sortJobs(base, filters.sort);
  }, [dashboardView, jobs, filters.sort]);
  const selectedJob = useMemo(() => jobs.find((job) => job.id === selectedJobId) || null, [jobs, selectedJobId]);
  const activeJobs = useMemo(() => jobs.filter((job) => ['queued', 'running'].includes(job.status)), [jobs]);
  const runningJobs = useMemo(() => jobs.filter((job) => job.status === 'running'), [jobs]);
  const globalProgress = useMemo(() => (activeJobs.length ? Math.round(activeJobs.reduce((total, job) => total + getProgress(job), 0) / activeJobs.length) : 0), [activeJobs]);

  const summary = useMemo(() => {
    const counts: Record<JobStatus | 'all', number> = { all: jobs.length, queued: 0, running: 0, cancelled: 0, completed: 0, failed: 0 };
    jobs.forEach((job) => {
      const status = normalizeStatus(job.status) as JobStatus;
      counts[status] += 1;
    });
    return counts;
  }, [jobs]);

  const persistPresets = useCallback((next: LocalPreset[]) => {
    setPresets(next);
    localStorage.setItem('video-converter-presets-v1', JSON.stringify(next));
  }, []);

  const showToast = useCallback((message: string) => {
    setToast(message);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(''), 4200);
  }, []);

  const keepManualRefreshFeedbackVisible = useCallback(() => {
    setManualRefreshFeedback(true);
    if (manualRefreshFeedbackTimer.current) window.clearTimeout(manualRefreshFeedbackTimer.current);
    manualRefreshFeedbackTimer.current = window.setTimeout(() => setManualRefreshFeedback(false), 900);
  }, []);

  const refreshJobs = useCallback(async (mode: 'initial' | 'background' = 'background') => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    const initialLoad = mode === 'initial' && !jobsLoaded.current;
    if (initialLoad) setJobsLoading(true);
    else setJobsRefreshing(true);
    try {
      const [live, ready, worker, jobResult, outputList, batchList] = await Promise.all([
        getLiveHealth(),
        getReadyHealth(),
        getWorkerHealth().catch(() => null),
        listJobs(filters),
        listOutputs().catch(() => ({ outputs: [] })),
        listBatches().catch(() => ({ batches: [] })),
      ]);
      setApiHealthy(live.status === 'ok');
      setRedisHealthy(ready.redis === 'ok');
      setWorkerHealth(worker);
      setJobs((current) => mergeJobs(current, jobResult.jobs));
      setOutputs((current) => mergeOutputs(current, outputList.outputs));
      setBatches(batchList.batches);
      setLastSync(new Date().toISOString());
    } catch (error) {
      setApiHealthy(false);
      setRedisHealthy(false);
      showToast(error instanceof Error ? error.message : 'Failed to refresh queue');
    } finally {
      jobsLoaded.current = true;
      refreshInFlight.current = false;
      setJobsLoading(false);
      setJobsRefreshing(false);
    }
  }, [filters, showToast]);

  const handleManualRefresh = useCallback(async () => {
    setManualRefreshFeedback(true);
    if (manualRefreshFeedbackTimer.current) window.clearTimeout(manualRefreshFeedbackTimer.current);
    try {
      await refreshJobs('background');
    } finally {
      keepManualRefreshFeedbackVisible();
    }
  }, [keepManualRefreshFeedbackVisible, refreshJobs]);

  const loadRoots = useCallback(async () => {
    try {
      const data = await listMediaRoots();
      setRoots(data);
      setSelectedRootKey((current) => (current && data.some((root) => root.key === current) ? current : data[0]?.key || ''));
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Failed to load media roots');
    }
  }, [showToast]);

  const openPath = useCallback(async (path = currentPath, query = browserQuery) => {
    if (!selectedRootKey) return;
    setBrowserLoading(true);
    try {
      const data = await browseMedia(selectedRootKey, path, query);
      setCurrentPath(data.current_path || '');
      setEntries(data.entries || []);
      setSelectedPaths(new Set());
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Failed to browse media');
    } finally {
      setBrowserLoading(false);
    }
  }, [browserQuery, currentPath, selectedRootKey, showToast]);

  useEffect(() => {
    void loadRoots();
  }, [loadRoots]);

  useEffect(() => () => {
    if (manualRefreshFeedbackTimer.current) window.clearTimeout(manualRefreshFeedbackTimer.current);
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
  }, []);

  useEffect(() => {
    if (document.visibilityState === 'visible') void refreshJobs(jobsLoaded.current ? 'background' : 'initial');
  }, [refreshJobs]);

  useEffect(() => {
    if (selectedRootKey) void openPath('');
  }, [selectedRootKey]);

  useEffect(() => {
    let stream: EventSource | null = null;

    const clearPolling = () => {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };

    const startPolling = () => {
      if (sseActiveRef.current) return;
      clearPolling();
      if (document.visibilityState === 'visible') {
        pollTimerRef.current = window.setInterval(() => void refreshJobs('background'), pollMs);
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        if (sseActiveRef.current) {
          clearPolling();
        } else {
          void refreshJobs('background');
          startPolling();
        }
      } else {
        clearPolling();
      }
    };

    if (typeof EventSource !== 'undefined') {
      stream = new EventSource('/api/v1/jobs/stream');
      stream.onopen = () => {
        sseActiveRef.current = true;
        setStreamState('live');
        clearPolling();
      };
      stream.onerror = () => {
        sseActiveRef.current = false;
        setStreamState('fallback');
        startPolling();
      };
      stream.addEventListener('jobs_snapshot', (event) => {
        try {
          const payload = JSON.parse((event as MessageEvent).data) as JobStreamPayload;
          if (payload.data.jobs) {
            setJobs((current) => mergeJobs(current, payload.data.jobs || []));
            setLastSync(payload.timestamp);
            setAnnouncement(`Live queue update received for ${payload.data.jobs.length} jobs.`);
          }
        } catch {
          sseActiveRef.current = false;
          setStreamState('fallback');
          startPolling();
        }
      });
    } else {
      sseActiveRef.current = false;
      setStreamState('fallback');
      startPolling();
    }

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      clearPolling();
      sseActiveRef.current = false;
      stream?.close();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [refreshJobs]);

  const addSelectedToStage = useCallback(async () => {
    if (!selectedRoot) return;
    if (!selectedEntries.length) {
      showToast('Select at least one video file from the server browser.');
      return;
    }
    const existing = new Set(staged.map((item) => `${item.rootKey}:${item.sourcePath}`));
    const nextItems = selectedEntries
      .filter((entry) => allowedExtensions.has((entry.name.split('.').pop() || '').toLowerCase()))
      .filter((entry) => !existing.has(`${selectedRoot.key}:${entry.rel_path}`))
      .map<StagedServerFile>((entry) => ({ id: `${selectedRoot.key}:${entry.rel_path}`, rootKey: selectedRoot.key, rootLabel: selectedRoot.label, sourcePath: entry.rel_path, name: entry.name, selected: true, subtitleProbeStatus: 'idle' }));
    if (!nextItems.length) {
      showToast('Selected files are already staged.');
      return;
    }
    setStaged((items) => [...items, ...nextItems]);
    setSelectedPaths(new Set());
    showToast(`${nextItems.length} file${nextItems.length > 1 ? 's' : ''} added to staging.`);
    await Promise.all(nextItems.map(async (item) => {
      setStaged((items) => items.map((stage) => (stage.id === item.id ? { ...stage, subtitleProbeStatus: 'loading' } : stage)));
      try {
        const probe = await probeSubtitles(item.rootKey, item.sourcePath);
        const languages = [...new Set(probe.tracks.map((track) => track.language || 'und'))].sort();
        setStaged((items) => items.map((stage) => (stage.id === item.id ? { ...stage, subtitleProbeStatus: 'done', subtitleTrackCount: probe.tracks.length, subtitleLanguages: languages } : stage)));
      } catch {
        setStaged((items) => items.map((stage) => (stage.id === item.id ? { ...stage, subtitleProbeStatus: 'error' } : stage)));
      }
    }));
  }, [selectedEntries, selectedRoot, showToast, staged]);

  const buildPayload = useCallback((items: StagedServerFile[]) => items.map((item) => ({
    input_filename: item.name,
    source_root_key: item.rootKey,
    source_path: item.sourcePath,
    profile: deriveProfile(settings.video_export),
    video_export: settings.video_export,
    audio_export: settings.audio_export,
    subtitle_export: settings.subtitle_export,
    subtitle_language: settings.subtitle_language || null,
  })), [settings]);

  const validateStaging = useCallback(async () => {
    const selected = staged.filter((item) => item.selected);
    if (!selected.length) {
      showToast('Select staged items before validation.');
      return false;
    }
    const result = await validateJobs(buildPayload(selected));
    setSubmitSummary(`${result.valid_count} valid, ${result.invalid_count} invalid.`);
    if (result.invalid_count) showToast(result.items.filter((item) => !item.valid).map((item) => item.message).join('; '));
    else showToast('All selected staged items are valid.');
    return result.invalid_count === 0;
  }, [buildPayload, showToast, staged]);

  const submitBatch = useCallback(async () => {
    const selected = staged.filter((item) => item.selected);
    if (!selected.length) {
      showToast('Select at least one staged item before creating jobs.');
      return;
    }
    setSubmitting(true);
    try {
      const payload = buildPayload(selected);
      const validation = await validateJobs(payload);
      if (validation.invalid_count) {
        setSubmitSummary(`${validation.valid_count} valid, ${validation.invalid_count} invalid. Clear invalid items before submitting.`);
        showToast('Validation failed; jobs were not queued.');
        return;
      }
      const response = await createJobsBatch(payload);
      setStaged((items) => items.filter((item) => !selected.some((submitted) => submitted.id === item.id)));
      setSubmitSummary(`${response.jobs.length} queued successfully, 0 failed.`);
      setAnnouncement(`${response.jobs.length} jobs queued successfully.`);
      showToast(`${response.jobs.length} job${response.jobs.length > 1 ? 's' : ''} queued.`);
      setActivePage('dashboard');
      await refreshJobs();
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Failed to create jobs');
    } finally {
      setSubmitting(false);
    }
  }, [buildPayload, refreshJobs, showToast, staged]);

  const runBulkAction = useCallback(async (action: 'cancel' | 'start' | 'archive' | 'delete') => {
    const ids = [...selectedJobIds];
    if (!ids.length) {
      showToast('Select at least one job first.');
      return;
    }
    try {
      const result = action === 'cancel' ? await bulkCancel(ids) : action === 'start' ? await bulkStart(ids) : action === 'archive' ? await bulkArchive(ids) : await bulkDelete(ids);
      setSelectedJobIds(new Set());
      showToast(`${result.updated.length} updated${result.skipped.length ? `, ${result.skipped.length} skipped` : ''}.`);
      await refreshJobs();
    } catch (error) {
      showToast(error instanceof Error ? error.message : `Bulk ${action} failed`);
    }
  }, [refreshJobs, selectedJobIds, showToast]);

  const applyPreset = useCallback((presetId: string) => {
    setSelectedPresetId(presetId);
    const preset = presets.find((item) => item.id === presetId);
    if (!preset) return;
    setSettings(preset.settings);
    showToast(`Preset applied: ${preset.name}`);
  }, [presets, showToast]);

  const startEditPreset = useCallback((preset: LocalPreset) => {
    setEditingPresetId(preset.id);
    setPresetName(preset.name);
    setPresetDescription(preset.description);
    setSettings(preset.settings);
    setActivePage('presets');
  }, []);

  const resetPresetForm = useCallback(() => {
    setEditingPresetId(null);
    setPresetName('');
    setPresetDescription('');
  }, []);

  const savePreset = useCallback(() => {
    const name = presetName.trim();
    if (!name) {
      showToast('Preset name is required.');
      return;
    }
    const now = new Date().toISOString();
    const next = editingPresetId
      ? presets.map((preset) => (preset.id === editingPresetId ? { ...preset, name, description: presetDescription.trim(), settings: { ...settings }, updatedAt: now } : preset))
      : [{ id: createPresetId(), name, description: presetDescription.trim(), settings: { ...settings }, createdAt: now, updatedAt: now }, ...presets];
    persistPresets(next);
    resetPresetForm();
    showToast(editingPresetId ? 'Preset updated.' : 'Preset saved.');
  }, [editingPresetId, persistPresets, presetDescription, presetName, presets, resetPresetForm, settings, showToast]);

  const deletePreset = useCallback((presetId: string) => {
    persistPresets(presets.filter((preset) => preset.id !== presetId));
    if (selectedPresetId === presetId) setSelectedPresetId('');
    if (editingPresetId === presetId) resetPresetForm();
    showToast('Preset deleted.');
  }, [editingPresetId, persistPresets, presets, resetPresetForm, selectedPresetId, showToast]);

  const refreshButtonActive = jobsRefreshing || manualRefreshFeedback;

  const exportSettingsForm = (
    <div className="field-grid two dense-fields">
      <label>Video container<select value={settings.video_export} onChange={(event) => setSettings((value) => ({ ...value, video_export: event.target.value as VideoExport }))}>{(['mp4', 'mkv', 'webm'] as const).map((option) => <option key={option} value={option}>{option.toUpperCase()}</option>)}</select></label>
      <label>Audio<select value={settings.audio_export} onChange={(event) => setSettings((value) => ({ ...value, audio_export: event.target.value as AudioExport }))}>{(['copy', 'aac', 'mp3', 'opus'] as const).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
      <label>Subtitles<select value={settings.subtitle_export} onChange={(event) => setSettings((value) => ({ ...value, subtitle_export: event.target.value as SubtitleExport }))}>{(['none', 'embedded', 'separate_srt'] as const).map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
      <label>Language<select value={settings.subtitle_language} onChange={(event) => setSettings((value) => ({ ...value, subtitle_language: event.target.value }))}><option value="">Auto / first available</option>{subtitleLanguages.map((lang) => <option key={lang} value={lang}>{lang}</option>)}</select></label>
    </div>
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-block">
          <h1>Video Converter</h1>
        </div>
        <nav className="main-tabs" role="tablist" aria-label="Primary workflow">
          {(['dashboard', 'convert', 'presets'] as const).map((page) => (
            <button key={page} type="button" role="tab" aria-selected={activePage === page} className={`main-tab ${activePage === page ? 'active' : ''}`} onClick={() => setActivePage(page)}>
              {page === 'dashboard' ? 'Dashboard' : page === 'convert' ? 'Convert' : 'Presets'}
            </button>
          ))}
        </nav>
        <div className="health-cluster" aria-live="polite">
          <HealthPill label="API" ok={apiHealthy} />
          <HealthPill label="Redis" ok={redisHealthy} />
          <HealthPill label="Worker" ok={workerHealth?.status === 'ok'} meta={workerHealth ? `${workerHealth.running_jobs} active · ${workerHealth.queue_depth} queued` : 'unknown'} />
          <button className={`primary-button compact refresh-button${refreshButtonActive ? ' refreshing' : ''}${manualRefreshFeedback ? ' feedback' : ''}`} type="button" onClick={() => void handleManualRefresh()} disabled={jobsLoading || jobsRefreshing} aria-busy={refreshButtonActive} aria-label={refreshButtonActive ? 'Refreshing jobs' : 'Refresh jobs'} title={refreshButtonActive ? 'Refreshing jobs' : 'Refresh jobs'}>
            <span className="refresh-button__icon" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M20 6v5h-5" /><path d="M4 18v-5h5" /><path d="M18.7 9A7 7 0 0 0 6.3 6.8L4 9" /><path d="M5.3 15A7 7 0 0 0 17.7 17.2L20 15" /></svg></span>
            <span className="refresh-button__label">Refresh</span>
          </button>
        </div>
      </header>

      <main className="workspace">
        {activePage === 'dashboard' ? (
          <section className="page-stack" aria-labelledby="dashboard-title">
            <div className="page-heading dashboard-heading">
              <div><p className="eyebrow">Main dashboard</p><h2 id="dashboard-title">Queue control and output overview</h2></div>
              <div className="sync-strip"><span>{streamState === 'live' ? 'SSE live' : `Polling ${pollMs / 1000}s`}</span><span>Last sync {formatDate(lastSync)}</span></div>
            </div>
            {streamState === 'fallback' ? <div className="fallback-banner" role="status">Live stream disconnected; polling fallback is active.</div> : null}
            <div className="summary-grid compact-summary">
              <SummaryCard label="Total" value={summary.all} tone="neutral" />
              <SummaryCard label="Queued" value={summary.queued} tone="queued" />
              <SummaryCard label="Running" value={summary.running} tone="running" />
              <SummaryCard label="Done" value={summary.completed} tone="completed" />
              <SummaryCard label="Failed" value={summary.failed} tone="failed" />
              <SummaryCard label="Active" value={globalProgress} tone="running" suffix="%" />
            </div>
            <div className="dashboard-grid compact-dashboard-grid">
              <section className="card active-card compact-active-card" aria-label="Active conversion progress">
                <div className="active-compact-row">
                  <div className="active-compact-copy"><span className="eyebrow">Active</span><strong>{runningJobs.length ? `${runningJobs.length} running` : 'Idle'}</strong><small>{activeJobs.length ? `${activeJobs.length} queued or active · ${globalProgress}% overall` : 'No running conversions'}</small></div>
                  <div className="active-compact-meter"><div className="active-meter"><span style={{ width: `${globalProgress}%` }} /></div><b>{globalProgress}%</b></div>
                </div>
                {runningJobs.length ? <div className="mini-job-list compact-mini-list">{runningJobs.slice(0, 2).map((job) => <MiniJob key={job.id} job={job} onOpen={() => setSelectedJobId(job.id)} />)}</div> : <p className="active-idle-line">Queue activity will appear here when conversions start.</p>}
              </section>
              <section className="card batches-card compact-batches-card">
                <CardHeader title="Recent batches" badge={`${batches.length} batches`} />
                <div className="batch-list">
                  {batches.length ? batches.slice(0, 6).map((batch) => <div className="batch-row" key={batch.batch_id}><strong>{batch.batch_id.slice(0, 8)}</strong><span>{batch.total} jobs · {batch.completed} done · {batch.failed} failed</span><div className="progress-line"><span style={{ width: `${batch.progress_percent}%` }} /></div></div>) : <EmptyState title="No batches yet" body="Create jobs in Convert." />}
                </div>
              </section>
            </div>
            <section className="card queue-card">
              <div className="queue-toolbar">
                <div><p className="eyebrow">Jobs workspace</p><h2>Queue, history and actions</h2></div>
                <ViewTabs value={dashboardView} onChange={(v) => setDashboardView(v as DashboardView)} />
              </div>
              <JobControls filters={filters} setFilters={setFilters} selectedCount={selectedJobIds.size} filteredJobs={filteredJobs} setSelectedJobIds={setSelectedJobIds} runBulkAction={runBulkAction} />
              {dashboardView === 'outputs' ? <OutputsPanel outputs={outputs} /> : dashboardView === 'advanced' ? <AdvancedPanel streamState={streamState} workerHealth={workerHealth} roots={roots} /> : <JobList jobsLoading={jobsLoading} jobs={filteredJobs} selectedJobIds={selectedJobIds} setSelectedJobIds={setSelectedJobIds} setSelectedJobId={setSelectedJobId} refreshJobs={refreshJobs} cancelJob={(id) => cancelJob(id)} />}
            </section>
          </section>
        ) : null}

        {activePage === 'convert' ? (
          <section className="convert-layout" aria-labelledby="convert-title">
            <div className="page-heading convert-heading">
              <div><p className="eyebrow">Convert workflow</p><h2 id="convert-title">Select files and queue conversions</h2></div>
              <button className="ghost-button" type="button" onClick={() => setActivePage('dashboard')}>Back to dashboard</button>
            </div>
            <section className="card browser-card">
              <CardHeader title="Source browser" badge={selectedRoot?.label || 'No root'} />
              <div className="source-tabs" role="tablist" aria-label="Source type">
                <button className="source-tab disabled" type="button" role="tab" aria-selected="false" title="Browser upload is not enabled yet">Local files</button>
                <button className="source-tab disabled" type="button" role="tab" aria-selected="false" title="Folder picker is not enabled yet">Local folder</button>
                <button className="source-tab active" type="button" role="tab" aria-selected="true">Server browser</button>
              </div>
              <div className="field-grid browser-controls">
                <label>Media root<select value={selectedRootKey} onChange={(event) => setSelectedRootKey(event.target.value)}>{roots.length ? roots.map((root) => <option key={root.key} value={root.key}>{root.label}</option>) : <option value="">No roots</option>}</select></label>
                <label>Search<div className="inline-control"><input value={browserQuery} onChange={(event) => setBrowserQuery(event.target.value)} placeholder="Filter names" /><button className="ghost-button" type="button" onClick={() => void openPath(currentPath, browserQuery)}>Find</button></div></label>
              </div>
              <div className="path-bar"><span title={`/${currentPath}`}>/{currentPath || ''}</span><button className="ghost-button tiny" type="button" onClick={() => void openPath('', '')}>Root</button></div>
              <div className="browser-list dense" aria-live="polite">
                {browserLoading ? <EmptyState title="Loading directory…" /> : entries.length ? entries.map((entry) => (
                  <button className={`browser-row ${entry.type === 'file' && selectedPaths.has(entry.rel_path) ? 'selected' : ''}`} key={`${entry.type}:${entry.rel_path}`} type="button" onClick={() => {
                    if (entry.type === 'dir') void openPath(entry.rel_path, '');
                    else setSelectedPaths((paths) => {
                      const next = new Set(paths);
                      if (next.has(entry.rel_path)) next.delete(entry.rel_path);
                      else next.add(entry.rel_path);
                      return next;
                    });
                  }}>
                    <span className="row-icon">{entry.type === 'dir' ? '⌁' : '◼'}</span><span><strong>{entry.name}</strong><small>{entry.type === 'dir' ? 'Folder' : 'Server video'}</small></span>
                  </button>
                )) : <EmptyState title="No supported videos here" body="Only configured roots and supported video extensions are shown." />}
              </div>
              <button className="primary-button full" type="button" onClick={() => void addSelectedToStage()}>Add selected videos ({selectedEntries.length})</button>
            </section>
            <section className="card staging-card">
              <CardHeader title="Staging" badge={`${staged.length} files · ${selectedStageCount} selected`} />
              <div className="staging-actions"><button className="ghost-button" type="button" onClick={() => setStaged((items) => items.map((item) => ({ ...item, selected: true })))}>Select all</button><button className="ghost-button" type="button" onClick={() => setStaged((items) => items.map((item) => ({ ...item, selected: false })))}>Select none</button><button className="danger-button" type="button" onClick={() => setStaged((items) => items.filter((item) => !item.selected))}>Remove selected</button></div>
              <div className="stage-list compact-list">
                {staged.length ? staged.map((item) => <label className="stage-row" key={item.id}><input type="checkbox" checked={item.selected} onChange={(event) => setStaged((items) => items.map((stage) => stage.id === item.id ? { ...stage, selected: event.target.checked } : stage))} /><span><strong>{item.name}</strong><small>{item.rootLabel} · {fileName(item.sourcePath)}</small></span><span className="micro-badge">{item.subtitleProbeStatus === 'loading' ? 'subs…' : `${item.subtitleTrackCount ?? 0} subs`}</span></label>) : <EmptyState title="Staging is empty" body="Select server videos and add them here before queueing." />}
              </div>
            </section>
            <section className="card settings-card">
              <CardHeader title="Export settings" badge={deriveProfile(settings.video_export)} />
              <label className="preset-picker">Apply preset<select value={selectedPresetId} onChange={(event) => applyPreset(event.target.value)}><option value="">Custom settings</option>{presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
              {exportSettingsForm}
              <div className="submit-actions"><button className="ghost-button" type="button" onClick={() => void validateStaging()} disabled={submitting || !selectedStageCount}>Validate selected</button><button className="primary-button glow" type="button" onClick={() => void submitBatch()} disabled={submitting || !selectedStageCount}>{submitting ? 'Creating jobs…' : `Create ${selectedStageCount} job${selectedStageCount === 1 ? '' : 's'}`}</button></div>
              {submitSummary ? <p className="submit-summary" role="status">{submitSummary}</p> : null}
            </section>
          </section>
        ) : null}

        {activePage === 'presets' ? (
          <section className="presets-layout" aria-labelledby="presets-title">
            <div className="page-heading"><div><p className="eyebrow">Reusable conversion setup</p><h2 id="presets-title">Local presets</h2></div><span className="soft-badge">Stored in this browser</span></div>
            <section className="card preset-editor">
              <CardHeader title={editingPresetId ? 'Edit preset' : 'Create preset'} badge={deriveProfile(settings.video_export)} />
              <div className="field-grid preset-form"><label>Name<input value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="Fast MP4, Archive MKV…" /></label><label>Description<input value={presetDescription} onChange={(event) => setPresetDescription(event.target.value)} placeholder="Optional note" /></label></div>
              {exportSettingsForm}
              <div className="submit-actions"><button className="primary-button" type="button" onClick={savePreset}>{editingPresetId ? 'Update preset' : 'Save preset'}</button><button className="ghost-button" type="button" onClick={resetPresetForm}>Clear form</button></div>
            </section>
            <section className="card presets-card">
              <CardHeader title="Saved presets" badge={`${presets.length} presets`} />
              <div className="preset-grid">
                {presets.length ? presets.map((preset) => <article className="preset-tile" key={preset.id}><div><strong>{preset.name}</strong><small>{preset.description || 'No description'}</small></div><div className="preset-meta"><span>{preset.settings.video_export}/{preset.settings.audio_export}/{preset.settings.subtitle_export}</span><span>{formatDate(preset.updatedAt)}</span></div><div className="preset-actions"><button className="primary-button tiny" type="button" onClick={() => applyPreset(preset.id)}>Apply</button><button className="ghost-button tiny" type="button" onClick={() => startEditPreset(preset)}>Edit</button><button className="danger-button tiny" type="button" onClick={() => deletePreset(preset.id)}>Delete</button></div></article>) : <EmptyState title="No presets saved" body="Create reusable settings here, then apply them from the Convert tab." />}
              </div>
            </section>
          </section>
        ) : null}
      </main>

      {selectedJob ? <aside className="detail-drawer" role="dialog" aria-modal="false" aria-label={`Job detail for ${selectedJob.input_filename || selectedJob.id}`}>
        <div className="drawer-header"><div><p className="eyebrow">Job detail</p><h2>{selectedJob.input_filename || selectedJob.id}</h2></div><button className="ghost-button tiny" type="button" onClick={() => setSelectedJobId(null)} aria-label="Close job detail">Close</button></div>
        <dl className="detail-grid"><div><dt>Status</dt><dd><StatusBadge status={selectedJob.status} /></dd></div><div><dt>Source path</dt><dd>{selectedJob.source_path || selectedJob.input_filename || 'Legacy input'}</dd></div><div><dt>Batch</dt><dd>{selectedJob.batch_id || '—'}</dd></div><div><dt>Telemetry</dt><dd>{formatEta(selectedJob.progress_eta_seconds)} · {selectedJob.progress_fps ?? '—'} fps · {selectedJob.progress_bitrate || 'bitrate —'} · {selectedJob.progress_speed || 'speed —'}</dd></div></dl>
        {selectedJob.output_filename ? <a className="primary-button full" href={`/api/v1/outputs/${encodeURIComponent(selectedJob.output_filename)}/download`}>Download output</a> : null}
        <h3>Timeline</h3><ol className="timeline-list">{selectedJob.timeline?.length ? selectedJob.timeline.map((item, index) => <li key={`${item.at}-${index}`}><strong>{item.phase || item.status}</strong><span>{formatDate(item.at)} · {item.message || ''}</span></li>) : <li>No timeline events yet.</li>}</ol>
        <h3>Log tail</h3><pre className="log-tail">{selectedJob.log_tail?.length ? selectedJob.log_tail.join('\n') : 'No log lines yet.'}</pre>
      </aside> : null}

      <div className="sr-only" aria-live="polite">{announcement}</div>
      <footer className="footer"><span>Last sync: {jobsRefreshing ? 'updating…' : formatDate(lastSync)}</span><span>Live updates: {streamState === 'live' ? 'SSE connected' : `polling every ${pollMs / 1000}s`}</span><span>Contract: existing /api/v1 endpoints preserved</span></footer>
      {toast ? <div className="toast" role="status">{toast}</div> : null}
    </div>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
