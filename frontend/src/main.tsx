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
  getAuthToken,
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
  presetStorageKey,
  type AppPage,
  type LocalPreset,
} from './utils/constants';
import {
  deriveProfile,
  fileName,
  formatDate,
  mergeJobs,
  mergeOutputs,
  normalizeStatus,
  sortJobs,
  uniqueLanguages,
  createPresetId,
} from './utils/helpers';
import {
  HealthPill,
  EmptyState,
  JobControls,
  JobList,
  OutputsPanel,
  SystemResourcesPanel,
} from './components/ui';
import { LoginPage } from './components/LoginPage';
import { SettingsPanel } from './components/SettingsPanel';
import { Video, RotateCw, Plus, Folder, FileVideo, HardDrive, Settings, Sliders, CheckCircle2, AlertTriangle, Info, Search, Trash2, Play } from 'lucide-react';

function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(!!getAuthToken());
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
  const [submitting, setSubmitting] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  
  const [activePage, setActivePage] = useState<AppPage>(() => {
    const hash = window.location.hash.replace('#', '');
    return (hash === 'dashboard' || hash === 'convert' || hash === 'presets' || hash === 'settings') ? hash as AppPage : 'dashboard';
  });

  const [streamState, setStreamState] = useState<'connecting' | 'live' | 'fallback'>('connecting');
  const [submitSummary, setSubmitSummary] = useState('');
  const [filters, setFilters] = useState<JobFilters>({ q: '', status: 'all', sort: 'newest', profile: '', sourceType: 'all' });
  const [settings, setSettings] = useState<ExportSettings>(defaultSettings);
  const [presets, setPresets] = useState<LocalPreset[]>(() => loadStoredPresets());
  const [selectedPresetId, setSelectedPresetId] = useState('');

  const toastTimer = useRef<number | null>(null);
  const refreshInFlight = useRef(false);
  const jobsLoaded = useRef(false);
  const sseActiveRef = useRef(false);
  const pollTimerRef = useRef<number | null>(null);

  const selectedRoot = useMemo(() => roots.find((root) => root.key === selectedRootKey), [roots, selectedRootKey]);
  const selectedEntries = useMemo(() => entries.filter((entry) => entry.type === 'file' && selectedPaths.has(entry.rel_path)), [entries, selectedPaths]);
  const selectedStageCount = staged.filter((item) => item.selected).length;
  const subtitleLanguages = useMemo(() => uniqueLanguages(staged), [staged]);
  const filteredJobs = useMemo(() => sortJobs(jobs.filter(job => {
    const sMatch = filters.status === 'all' || normalizeStatus(job.status) === filters.status;
    const qMatch = !filters.q || (job.input_filename || job.source_path || job.id).toLowerCase().includes(filters.q.toLowerCase());
    const pMatch = !filters.profile || job.profile === filters.profile;
    return sMatch && qMatch && pMatch;
  }), filters.sort), [jobs, filters]);

  const summary = useMemo(() => {
    const counts: Record<JobStatus | 'all', number> = { all: jobs.length, queued: 0, running: 0, cancelled: 0, completed: 0, failed: 0 };
    jobs.forEach((job) => {
      const status = normalizeStatus(job.status) as JobStatus;
      counts[status] = (counts[status] || 0) + 1;
    });
    return counts;
  }, [jobs]);

  const showToast = useCallback((msg: string, type: 'success' | 'error' | 'info' = 'info') => {
    setToast({ msg, type });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const refreshJobs = useCallback(async (mode: 'initial' | 'background' | 'manual' = 'background') => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    if (mode === 'initial' && !jobsLoaded.current) setJobsLoading(true);
    else setJobsRefreshing(true);
    
    const startTime = Date.now();
    try {
      const [live, ready, worker, jobResult, outputList, batchList] = await Promise.all([
        getLiveHealth(),
        getReadyHealth(),
        getWorkerHealth().catch(() => null),
        listJobs({ ...filters, status: 'all', profile: '' }), // fetch all to apply local filters
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
      // Optional: silent fail on background refresh
    } finally {
      const finishRefresh = () => {
        jobsLoaded.current = true;
        refreshInFlight.current = false;
        setJobsLoading(false);
        setJobsRefreshing(false);
        if (mode === 'manual') showToast('Data refreshed successfully', 'success');
      };

      const elapsed = Date.now() - startTime;
      if (mode === 'manual' && elapsed < 600) {
        setTimeout(finishRefresh, 600 - elapsed);
      } else {
        finishRefresh();
      }
    }
  }, [filters, showToast]);

  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');

  const persistPresets = useCallback((newPresets: LocalPreset[]) => {
    try {
      localStorage.setItem(presetStorageKey, JSON.stringify(newPresets));
    } catch { /* ignore */ }
    setPresets(newPresets);
  }, []);

  const startEditPreset = useCallback((preset: LocalPreset) => {
    setEditingPresetId(preset.id);
    setPresetName(preset.name);
    setPresetDescription(preset.description || '');
    setSettings(preset.settings);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  const resetPresetForm = useCallback(() => {
    setEditingPresetId(null);
    setPresetName('');
    setPresetDescription('');
    setSettings(defaultSettings);
  }, []);

  const savePreset = useCallback(() => {
    const name = presetName.trim();
    if (!name) {
      return;
    }
    const now = new Date().toISOString();
    const next = editingPresetId
      ? presets.map(p => p.id === editingPresetId ? { ...p, name, description: presetDescription.trim(), settings: { ...settings }, updatedAt: now } : p)
      : [...presets, { id: createPresetId(), name, description: presetDescription.trim(), settings: { ...settings }, createdAt: now, updatedAt: now }];
    
    persistPresets(next);
    resetPresetForm();
  }, [editingPresetId, persistPresets, presetDescription, presetName, presets, resetPresetForm, settings]);

  const deletePreset = useCallback((presetId: string) => {
    const next = presets.filter(p => p.id !== presetId);
    persistPresets(next);
    if (editingPresetId === presetId) resetPresetForm();
  }, [editingPresetId, persistPresets, presets, resetPresetForm]);

  const loadRoots = useCallback(async () => {
    try {
      const data = await listMediaRoots();
      setRoots(data);
      setSelectedRootKey((current) => (current && data.some((root) => root.key === current) ? current : data[0]?.key || ''));
    } catch (error) {
      showToast('Failed to load media roots', 'error');
    }
  }, [showToast]);

  const openPath = useCallback(async (path = '', query = '') => {
    if (!selectedRootKey) return;
    setBrowserLoading(true);
    try {
      const data = await browseMedia(selectedRootKey, path, query);
      setCurrentPath(data.current_path || '');
      setEntries(data.entries || []);
      setSelectedPaths(new Set());
    } catch (error) {
      showToast('Failed to browse media', 'error');
    } finally {
      setBrowserLoading(false);
    }
  }, [selectedRootKey, showToast]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void loadRoots();
  }, [loadRoots, isAuthenticated]);

  useEffect(() => {
    if (window.location.hash.replace('#', '') !== activePage) {
      window.location.hash = activePage;
    }
  }, [activePage]);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (document.visibilityState === 'visible') void refreshJobs(jobsLoaded.current ? 'background' : 'initial');
  }, [refreshJobs, isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated) return;
    if (selectedRootKey) void openPath('');
  }, [selectedRootKey, isAuthenticated, openPath]);

  useEffect(() => {
    if (!isAuthenticated) return;
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
        if (sseActiveRef.current) clearPolling();
        else { void refreshJobs('background'); startPolling(); }
      } else {
        clearPolling();
      }
    };

    if (typeof EventSource !== 'undefined') {
      const token = getAuthToken();
      stream = new EventSource(token ? `/api/v1/jobs/stream?token=${token}` : '/api/v1/jobs/stream');
      stream.onopen = () => { sseActiveRef.current = true; setStreamState('live'); clearPolling(); };
      stream.onerror = () => { sseActiveRef.current = false; setStreamState('fallback'); startPolling(); };
      stream.addEventListener('jobs_snapshot', (event) => {
        try {
          const payload = JSON.parse((event as MessageEvent).data) as JobStreamPayload;
          if (payload.data.jobs) {
            setJobs((current) => mergeJobs(current, payload.data.jobs || []));
            setLastSync(payload.timestamp);
          }
        } catch {
          sseActiveRef.current = false; setStreamState('fallback'); startPolling();
        }
      });
    } else {
      sseActiveRef.current = false; setStreamState('fallback'); startPolling();
    }

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => { clearPolling(); sseActiveRef.current = false; stream?.close(); document.removeEventListener('visibilitychange', handleVisibilityChange); };
  }, [refreshJobs, isAuthenticated]);

  const runBulkAction = useCallback(async (action: 'cancel' | 'start' | 'archive' | 'delete') => {
    const ids = [...selectedJobIds];
    if (!ids.length) { showToast('Select at least one job first.', 'error'); return; }
    try {
      const result = action === 'cancel' ? await bulkCancel(ids) : action === 'start' ? await bulkStart(ids) : action === 'archive' ? await bulkArchive(ids) : await bulkDelete(ids);
      setSelectedJobIds(new Set());
      showToast(`${result.updated.length} jobs ${action}ed.`, 'success');
      await refreshJobs('background');
    } catch (error) {
      showToast(`Action ${action} failed.`, 'error');
    }
  }, [refreshJobs, selectedJobIds, showToast]);

  const submitBatch = useCallback(async () => {
    const selected = staged.filter((item) => item.selected);
    if (!selected.length) { showToast('Select staged items.', 'error'); return; }
    setSubmitting(true);
    try {
      const payload = selected.map((item) => ({
        input_filename: item.name,
        source_root_key: item.rootKey,
        source_path: item.sourcePath,
        profile: deriveProfile(settings.video_export),
        video_export: settings.video_export,
        audio_export: settings.audio_export,
        subtitle_export: settings.subtitle_export,
        subtitle_language: settings.subtitle_language || null,
      }));
      const response = await createJobsBatch(payload);
      setStaged((items) => items.filter((item) => !selected.some((submitted) => submitted.id === item.id)));
      showToast(`${response.jobs.length} jobs queued successfully.`, 'success');
      setActivePage('dashboard');
      await refreshJobs('background');
    } catch (error) {
      showToast('Failed to create jobs', 'error');
    } finally {
      setSubmitting(false);
    }
  }, [refreshJobs, settings, showToast, staged]);

  if (!isAuthenticated) return <LoginPage onLogin={() => setIsAuthenticated(true)} />;

  return (
    <>
      <header className="app-header">
        <div className="header-container">
          <div className="header-left">
            <div className="brand">
              <div className="brand-icon">
                <Video size={16} />
              </div>
              <span className="font-semibold text-sm tracking-tight text-zinc-100">Video Converter</span>
              <span className="brand-version">v0.1.5</span>
            </div>

            <nav className="nav-tabs">
              <button className={`nav-tab ${activePage === 'dashboard' ? 'active' : ''}`} onClick={() => setActivePage('dashboard')}>Dashboard</button>
              <button className={`nav-tab ${activePage === 'convert' ? 'active' : ''}`} onClick={() => setActivePage('convert')}>Convert</button>
              <button className={`nav-tab ${activePage === 'presets' ? 'active' : ''}`} onClick={() => setActivePage('presets')}>Presets</button>
              <button className={`nav-tab ${activePage === 'settings' ? 'active' : ''}`} onClick={() => setActivePage('settings')}>Settings</button>
            </nav>
          </div>

          <div className="header-right">
            <div className="service-status">
              <HealthPill label="API" ok={apiHealthy} />
              <HealthPill label="Redis" ok={redisHealthy} />
              <HealthPill label="Worker" ok={workerHealth?.status === 'ok'} meta={workerHealth ? `${workerHealth.running_jobs} Active` : undefined} />
            </div>
            <button className="btn btn-outline" onClick={() => refreshJobs('manual')} disabled={jobsRefreshing}>
              <RotateCw size={14} className={jobsRefreshing ? 'spin' : ''} />
              <span>Refresh</span>
            </button>
          </div>
        </div>
      </header>

      <div className="notification-area">
        {toast && (
          <div className="toast">
            {toast.type === 'success' && <CheckCircle2 size={16} className="text-emerald-400" />}
            {toast.type === 'error' && <AlertTriangle size={16} className="text-rose-400" />}
            {toast.type === 'info' && <Info size={16} className="text-blue-400" />}
            <span className="text-zinc-200 font-medium">{toast.msg}</span>
          </div>
        )}
      </div>

      <main className="main-container">
        {activePage === 'dashboard' && (
          <>
            <div className="page-header">
              <div>
                <h1 className="text-lg font-semibold tracking-tight text-zinc-50">Dashboard</h1>
                <p className="text-xs text-zinc-400">Monitor conversion queue and system metrics.</p>
              </div>
              <button className="btn btn-primary" onClick={() => setActivePage('convert')}>
                <Plus size={14} />
                <span>New Job</span>
              </button>
            </div>

            <div className="metrics-grid">
              <div className="metric-card">
                <span className="metric-title">Total Jobs</span>
                <div className="metric-value-row">
                  <span className="text-2xl font-semibold font-mono tracking-tight text-zinc-100">{summary.all}</span>
                  <span className="text-[10px] text-zinc-500">active / done</span>
                </div>
              </div>
              <div className="metric-card queued">
                <span className="metric-title">Queued</span>
                <div className="metric-value-row">
                  <span className="text-2xl font-semibold font-mono tracking-tight text-amber-500">{summary.queued}</span>
                  <span className="text-[10px] text-zinc-500">waiting</span>
                </div>
              </div>
              <div className="metric-card running">
                <span className="metric-title">Running</span>
                <div className="metric-value-row">
                  <span className="text-2xl font-semibold font-mono tracking-tight text-blue-500">{summary.running}</span>
                  <span className="text-[10px] text-zinc-500">processing</span>
                </div>
              </div>
              <div className="metric-card done">
                <span className="metric-title">Completed</span>
                <div className="metric-value-row">
                  <span className="text-2xl font-semibold font-mono tracking-tight text-emerald-500">{summary.completed}</span>
                  <span className="text-[10px] text-zinc-500">success</span>
                </div>
              </div>
              <div className="metric-card failed">
                <span className="metric-title">Failed</span>
                <div className="metric-value-row">
                  <span className="text-2xl font-semibold font-mono tracking-tight text-rose-500">{summary.failed}</span>
                  <span className="text-[10px] text-zinc-500 font-mono">errors</span>
                </div>
              </div>
            </div>

            <div className="dashboard-grid">
              <div className="dashboard-main">
                <div className="panel">
                  <JobControls 
                    filters={filters} 
                    setFilters={setFilters} 
                    selectedCount={selectedJobIds.size} 
                    filteredJobs={filteredJobs} 
                    setSelectedJobIds={setSelectedJobIds} 
                    runBulkAction={runBulkAction} 
                  />
                  <JobList 
                    jobsLoading={jobsLoading} 
                    jobs={filteredJobs} 
                    selectedJobIds={selectedJobIds} 
                    setSelectedJobIds={setSelectedJobIds} 
                    setSelectedJobId={setSelectedJobId} 
                    refreshJobs={refreshJobs} 
                    cancelJob={cancelJob} 
                    deleteJob={(id) => bulkDelete([id])}
                  />
                </div>
              </div>

              <div className="dashboard-sidebar">
                <OutputsPanel outputs={outputs} />
                <SystemResourcesPanel workerHealth={workerHealth} />
              </div>
            </div>
          </>
        )}

        {activePage === 'convert' && (
          <div className="form-container">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-zinc-50">New Conversion</h1>
              <p className="text-xs text-zinc-400 mt-0.5">Select media files, choose encoding profile, and queue jobs.</p>
            </div>

            <div className="form-panel">
              <span className="form-section-title border-b pb-2">1. Source Browser</span>
              
              <div className="flex gap-2">
                <select className="form-input" style={{ width: '200px' }} value={selectedRootKey} onChange={e => setSelectedRootKey(e.target.value)}>
                  {roots.length ? roots.map(r => <option key={r.key} value={r.key}>{r.label}</option>) : <option value="">No roots</option>}
                </select>
                <div className="input-wrapper" style={{ flexGrow: 1 }}>
                  <input type="text" className="form-input has-icon" placeholder="Search files..." value={browserQuery} onChange={e => setBrowserQuery(e.target.value)} onKeyDown={e => e.key === 'Enter' && openPath(currentPath, browserQuery)} />
                  <Search size={14} className="input-icon" />
                </div>
                <button className="btn btn-outline" onClick={() => openPath(currentPath, browserQuery)}>Find</button>
              </div>
              
              <div className="bg-zinc-950 border border-zinc-800 rounded p-2 text-xs text-zinc-400 font-mono flex justify-between items-center">
                <span>/{currentPath}</span>
                <button className="text-zinc-500 hover:text-zinc-300" style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: '0.125rem 0.5rem' }} onClick={() => openPath('', '')}>Root</button>
              </div>
              
              <div className="border border-zinc-800 rounded bg-zinc-950 max-h-64 overflow-y-auto">
                {browserLoading ? <div className="p-4 text-center text-zinc-500">Loading...</div> : entries.length ? entries.map(entry => (
                  <div key={entry.rel_path} className={`flex items-center gap-3 p-2 hover:bg-zinc-900 border-b border-zinc-800 cursor-pointer ${selectedPaths.has(entry.rel_path) ? 'bg-zinc-900' : ''}`} onClick={() => {
                    if (entry.type === 'dir') openPath(entry.rel_path, '');
                    else setSelectedPaths(prev => {
                      const next = new Set(prev);
                      if (next.has(entry.rel_path)) next.delete(entry.rel_path); else next.add(entry.rel_path);
                      return next;
                    });
                  }}>
                    {entry.type === 'dir' ? <Folder size={16} className="text-blue-400" /> : <FileVideo size={16} className="text-zinc-500" />}
                    <div className="flex-grow">
                      <div className="text-xs text-zinc-200">{entry.name}</div>
                    </div>
                    {entry.type === 'file' && <input type="checkbox" className="form-checkbox" checked={selectedPaths.has(entry.rel_path)} readOnly />}
                  </div>
                )) : <div className="p-4 text-center text-zinc-500">No media found.</div>}
              </div>

              <div className="flex justify-end mt-2">
                <button className="btn btn-primary" onClick={() => {
                  const nextItems = selectedEntries.map(e => ({ id: `${selectedRootKey}:${e.rel_path}`, rootKey: selectedRootKey, rootLabel: selectedRoot?.label || '', sourcePath: e.rel_path, name: e.name, selected: true, subtitleProbeStatus: 'idle' as const }));
                  setStaged(prev => {
                    const existingIds = new Set(prev.map(i => i.id));
                    return [...prev, ...nextItems.filter(i => !existingIds.has(i.id))];
                  });
                  setSelectedPaths(new Set());
                  showToast('Added to staging.', 'success');
                }} disabled={selectedPaths.size === 0}>
                  Add Selected to Stage
                </button>
              </div>

              {staged.length > 0 && (
                <>
                  <span className="form-section-title border-b pb-2 mt-4">2. Staged Files ({staged.length})</span>
                  <div className="border border-zinc-800 rounded bg-zinc-950 max-h-40 overflow-y-auto">
                    {staged.map(item => (
                      <div key={item.id} className="flex items-center gap-3 p-2 border-b border-zinc-800">
                        <input type="checkbox" className="form-checkbox" checked={item.selected} onChange={e => setStaged(s => s.map(x => x.id === item.id ? { ...x, selected: e.target.checked } : x))} />
                        <div className="text-xs text-zinc-200 truncate flex-grow">{item.name}</div>
                        <button className="text-rose-400 hover:text-rose-500" onClick={() => setStaged(s => s.filter(x => x.id !== item.id))}><Trash2 size={14} /></button>
                      </div>
                    ))}
                  </div>
                </>
              )}

              <span className="form-section-title border-b pb-2 mt-4">3. Export Options</span>
              <div className="form-grid">
                <div className="form-group">
                  <label className="form-label">Video Format</label>
                  <select className="form-input" value={settings.video_export} onChange={e => setSettings(s => ({...s, video_export: e.target.value as any}))}>
                    <option value="mp4">MP4 (H.264)</option>
                    <option value="mkv">MKV (H.265)</option>
                    <option value="webm">WebM (VP9)</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Audio</label>
                  <select className="form-input" value={settings.audio_export} onChange={e => setSettings(s => ({...s, audio_export: e.target.value as any}))}>
                    <option value="copy">Copy Original</option>
                    <option value="aac">AAC</option>
                    <option value="mp3">MP3</option>
                    <option value="opus">Opus</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Subtitles</label>
                  <select className="form-input" value={settings.subtitle_export} onChange={e => setSettings(s => ({...s, subtitle_export: e.target.value as any}))}>
                    <option value="none">None</option>
                    <option value="embedded">Embedded</option>
                    <option value="separate_srt">Separate SRT</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Language Preference</label>
                  <select className="form-input" value={settings.subtitle_language} onChange={e => setSettings(s => ({...s, subtitle_language: e.target.value}))}>
                    <option value="">Auto Detect</option>
                    {subtitleLanguages.map(lang => <option key={lang} value={lang}>{lang}</option>)}
                  </select>
                </div>
              </div>

              <div className="border-t border-zinc-800 pt-4 flex items-center justify-end gap-2">
                <button className="btn btn-outline" onClick={() => setActivePage('dashboard')}>Cancel</button>
                <button className="btn btn-primary" onClick={submitBatch} disabled={submitting || staged.filter(s => s.selected).length === 0}>
                  <Play size={14} />
                  <span>Queue {staged.filter(s => s.selected).length} Jobs</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {activePage === 'presets' && (
          <div className="form-container">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-zinc-50">Presets</h1>
              <p className="text-xs text-zinc-400 mt-0.5">Manage your saved conversion profiles.</p>
            </div>
            
            <div className="form-panel mb-6">
              <span className="form-section-title border-b pb-2">{editingPresetId ? 'Edit Preset' : 'Create Preset'}</span>
              <div className="form-grid mt-4">
                <div className="form-group">
                  <label className="form-label">Name</label>
                  <input className="form-input" value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="Fast MP4, Archive MKV..." />
                </div>
                <div className="form-group">
                  <label className="form-label">Description</label>
                  <input className="form-input" value={presetDescription} onChange={e => setPresetDescription(e.target.value)} placeholder="Optional note" />
                </div>
              </div>
              
              <span className="form-section-title border-b pb-2 mt-4">Export Options</span>
              <div className="form-grid mt-4">
                <div className="form-group">
                  <label className="form-label">Video Format</label>
                  <select className="form-input" value={settings.video_export} onChange={e => setSettings(s => ({ ...s, video_export: e.target.value as any }))}>
                    <option value="mp4">MP4 (H.264)</option>
                    <option value="mkv">MKV (H.265)</option>
                    <option value="webm">WebM (VP9)</option>
                    <option value="copy">Copy Original</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Audio</label>
                  <select className="form-input" value={settings.audio_export} onChange={e => setSettings(s => ({ ...s, audio_export: e.target.value as any }))}>
                    <option value="aac">AAC (Standard)</option>
                    <option value="mp3">MP3</option>
                    <option value="opus">Opus</option>
                    <option value="copy">Copy Original</option>
                    <option value="remove">Remove Audio</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Subtitles</label>
                  <select className="form-input" value={settings.subtitle_export} onChange={e => setSettings(s => ({ ...s, subtitle_export: e.target.value as any }))}>
                    <option value="none">None</option>
                    <option value="embed">Embed (Soft)</option>
                    <option value="burn">Burn In (Hard)</option>
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">Language Preference</label>
                  <select className="form-input" value={settings.subtitle_language} onChange={e => setSettings(s => ({ ...s, subtitle_language: e.target.value }))}>
                    <option value="auto">Auto Detect</option>
                    <option value="eng">English</option>
                    <option value="tur">Turkish</option>
                    <option value="ger">German</option>
                    <option value="spa">Spanish</option>
                  </select>
                </div>
              </div>
              
              <div className="pt-4 flex gap-2 justify-end">
                <button className="btn btn-outline" onClick={resetPresetForm}>Clear Form</button>
                <button className="btn btn-primary" onClick={() => { savePreset(); showToast(editingPresetId ? 'Preset updated' : 'Preset saved', 'success'); }} disabled={!presetName.trim()}>
                  {editingPresetId ? 'Update Preset' : 'Save Preset'}
                </button>
              </div>
            </div>

            <div className="presets-grid">
              {presets.length ? presets.map(preset => (
                <div className="preset-card" key={preset.id}>
                  <div className="preset-header">
                    <span className="preset-badge">FFmpeg</span>
                    <span className="preset-type">Custom</span>
                  </div>
                  <div className="preset-body">
                    <h4>{preset.name}</h4>
                    <p>{preset.description || 'No description'}</p>
                    <div className="text-xs text-zinc-500 mt-2 font-mono">
                      {preset.settings.video_export}/{preset.settings.audio_export}
                    </div>
                  </div>
                  <div className="mt-auto pt-4 flex gap-2 border-t border-zinc-800">
                    <button className="btn btn-primary flex-grow justify-center" onClick={() => {
                      setSettings(preset.settings);
                      setActivePage('convert');
                      showToast(`Loaded preset ${preset.name}`, 'info');
                    }}>Apply</button>
                    <button className="btn btn-outline" onClick={() => startEditPreset(preset)}>Edit</button>
                    <button className="btn btn-outline text-rose-400 hover:text-rose-500" onClick={() => deletePreset(preset.id)}><Trash2 size={14} /></button>
                  </div>
                </div>
              )) : null}

              <div className="preset-card preset-new cursor-pointer hover:bg-zinc-800/50" onClick={resetPresetForm}>
                <Sliders size={24} className="text-zinc-500 mb-2" />
                <div className="text-sm font-medium text-zinc-300">New Preset</div>
                <div className="text-xs text-zinc-500 mt-1">Clear form to create a new preset.</div>
              </div>
            </div>
          </div>
        )}

        {activePage === 'settings' && (
          <div className="form-container">
            <div>
              <h1 className="text-lg font-semibold tracking-tight text-zinc-50">System Settings</h1>
              <p className="text-xs text-zinc-400 mt-0.5">Configuration and limits.</p>
            </div>
            <div className="form-panel">
              <SettingsPanel />
            </div>
          </div>
        )}
      </main>

      <footer className="app-footer">
        <div className="footer-container">
          <div className="footer-left">
            <span>Last Sync: {lastSync ? formatDate(lastSync) : 'Never'}</span>
            <span className="hidden sm:inline">|</span>
            <span className="flex items-center gap-1">
              <span className={`status-dot ${streamState === 'live' ? 'ok' : 'error'}`}></span>
              <span>{streamState === 'live' ? 'Live Stream Active' : 'Polling'}</span>
            </span>
          </div>
          <div>
            <span>/api/v1/ endpoints are preserved</span>
          </div>
        </div>
      </footer>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
