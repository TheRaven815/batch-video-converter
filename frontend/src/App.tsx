import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { LogOut, Menu, RotateCw, Video, X } from 'lucide-react';
import { toast } from 'sonner';
import { Link, Redirect, Route, Switch, useLocation } from 'wouter';

import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import './styles.css';

import {
  browseMedia,
  actOnBatch,
  bulkArchive,
  bulkCancel,
  bulkDelete,
  bulkStart,
  cancelJob,
  clearOutputs,
  createJobsBatch,
  getAuthToken,
  getSystemSettings,
  deleteOutput,
  setAuthToken,
  validateJobs,
} from './api';
import { ConfirmDialog, HealthPill } from './components/ui';
import { LoginPage } from './components/LoginPage';
import { AppProvider, type ToastKind } from './context/AppContext';
import { useServerState } from './hooks/useServerState';
import type {
  ExportSettings,
  JobFilters,
  JobStatus,
  MediaBrowseEntryDto,
  StagedServerFile,
} from './models';
import {
  defaultSettings,
  loadStoredPresets,
  presetStorageKey,
  type LocalPreset,
} from './utils/constants';
import {
  createPresetId,
  deriveProfile,
  formatDate,
  normalizeStatus,
  sortJobs,
} from './utils/helpers';
import { loadLanguage, translate } from './i18n';

const DashboardPage = lazy(() => import('./pages/DashboardPage'));
const ConvertPage = lazy(() => import('./pages/ConvertPage'));
const PresetsPage = lazy(() => import('./pages/PresetsPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));

type ConfirmRequest = {
  title: string;
  body: string;
  confirmLabel: string;
  action: () => void | Promise<void>;
};

function initialJobFilters(): JobFilters {
  const query = window.location.hash.split('?')[1] ?? '';
  const params = new URLSearchParams(query);
  const status = params.get('status');
  const sort = params.get('sort');
  const source = params.get('source');
  return {
    q: params.get('q') ?? '',
    status: ['queued', 'running', 'cancelled', 'completed', 'failed'].includes(status ?? '')
      ? (status as JobStatus)
      : 'all',
    sort: ['oldest', 'progress'].includes(sort ?? '') ? (sort as JobFilters['sort']) : 'newest',
    profile: params.get('profile') ?? '',
    sourceType: ['server', 'legacy'].includes(source ?? '')
      ? (source as JobFilters['sourceType'])
      : 'all',
  };
}

export function App() {
  const [isAuthenticated, setIsAuthenticated] = useState(Boolean(getAuthToken()));
  const server = useServerState(isAuthenticated);
  const [location, navigate] = useLocation();

  const [selectedRootKey, setSelectedRootKey] = useState('');
  const [currentPath, setCurrentPath] = useState('');
  const [browserQuery, setBrowserQuery] = useState('');
  const [entries, setEntries] = useState<MediaBrowseEntryDto[]>([]);
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());
  const [staged, setStaged] = useState<StagedServerFile[]>([]);
  const [browserLoading, setBrowserLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [selectedJobIds, setSelectedJobIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<JobFilters>(initialJobFilters);
  const [settings, setSettings] = useState<ExportSettings>(defaultSettings);
  const [presetSettings, setPresetSettings] = useState<ExportSettings>(defaultSettings);
  const [presets, setPresets] = useState<LocalPreset[]>(loadStoredPresets);
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [language, setLanguage] = useState(loadLanguage);

  const showToast = useCallback((message: string, kind: ToastKind = 'info') => {
    toast[kind](message, { id: `${kind}:${message}` });
  }, []);

  useEffect(() => {
    if (!server.roots.length) return;
    setSelectedRootKey((current) =>
      current && server.roots.some((root) => root.key === current) ? current : server.roots[0].key,
    );
  }, [server.roots]);

  const openPath = useCallback(
    async (path = '', query = '') => {
      if (!selectedRootKey) return;
      setBrowserLoading(true);
      try {
        const data = await browseMedia(selectedRootKey, path, query);
        setCurrentPath(data.current_path ?? '');
        setEntries(data.entries ?? []);
        setSelectedPaths(new Set());
      } catch {
        showToast('Failed to browse media.', 'error');
      } finally {
        setBrowserLoading(false);
      }
    },
    [selectedRootKey, showToast],
  );

  useEffect(() => {
    if (isAuthenticated && selectedRootKey) void openPath();
  }, [isAuthenticated, openPath, selectedRootKey]);

  useEffect(() => {
    setSelectedJobIds((current) => {
      const validIds = new Set(server.jobs.map((job) => job.id));
      const next = new Set([...current].filter((id) => validIds.has(id)));
      return next.size === current.size ? current : next;
    });
  }, [server.jobs]);

  useEffect(() => {
    if (!isAuthenticated) return;
    void getSystemSettings().then((stored) => {
      const theme = stored.ui.theme;
      const resolved =
        theme === 'system'
          ? window.matchMedia('(prefers-color-scheme: light)').matches
            ? 'light'
            : 'dark'
          : theme;
      document.documentElement.dataset.theme = resolved;
      document.documentElement.dataset.density = stored.ui.density;
    });
  }, [isAuthenticated]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const params = new URLSearchParams();
      if (filters.q) params.set('q', filters.q);
      if (filters.status !== 'all') params.set('status', filters.status);
      if (filters.sort !== 'newest') params.set('sort', filters.sort);
      if (filters.profile) params.set('profile', filters.profile);
      if (filters.sourceType !== 'all') params.set('source', filters.sourceType);
      const query = params.toString();
      const hashPath = window.location.hash.split('?')[0] || '#/dashboard';
      window.history.replaceState(
        null,
        '',
        `${window.location.pathname}${window.location.search}${hashPath}${query ? `?${query}` : ''}`,
      );
    }, 300);
    return () => window.clearTimeout(timer);
  }, [filters]);

  useEffect(() => {
    if (!isMobileMenuOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsMobileMenuOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    document.body.classList.add('drawer-open');
    return () => {
      window.removeEventListener('keydown', closeOnEscape);
      document.body.classList.remove('drawer-open');
    };
  }, [isMobileMenuOpen]);

  const filteredJobs = useMemo(
    () =>
      sortJobs(
        server.jobs.filter((job) => {
          const statusMatches =
            filters.status === 'all' || normalizeStatus(job.status) === filters.status;
          const queryMatches =
            !filters.q ||
            (job.input_filename ?? job.source_path ?? job.id)
              .toLowerCase()
              .includes(filters.q.toLowerCase());
          const profileMatches = !filters.profile || job.profile === filters.profile;
          const sourceMatches =
            filters.sourceType === 'all' ||
            (filters.sourceType === 'server' ? Boolean(job.source_path) : !job.source_path);
          return statusMatches && queryMatches && profileMatches && sourceMatches;
        }),
        filters.sort,
      ),
    [filters, server.jobs],
  );

  const summary = useMemo(() => {
    const counts: Record<JobStatus | 'all', number> = {
      all: server.jobs.length,
      queued: 0,
      running: 0,
      cancelled: 0,
      completed: 0,
      failed: 0,
    };
    server.jobs.forEach((job) => {
      const status = normalizeStatus(job.status);
      counts[status] += 1;
    });
    return counts;
  }, [server.jobs]);

  const executeBulkAction = useCallback(
    async (action: 'cancel' | 'start' | 'archive' | 'delete', ids: string[]) => {
      const actions = {
        cancel: bulkCancel,
        start: bulkStart,
        archive: bulkArchive,
        delete: bulkDelete,
      };
      const pastTense = {
        cancel: 'cancelled',
        start: 'started',
        archive: 'archived',
        delete: 'deleted',
      };
      try {
        const result = await actions[action](ids);
        setSelectedJobIds(new Set());
        const message = `${result.updated.length} job(s) ${pastTense[action]}. ${
          result.skipped.length ? `${result.skipped.length} skipped.` : ''
        }`;
        showToast(message.trim(), result.updated.length ? 'success' : 'info');
        await server.refreshAll();
      } catch (error) {
        showToast(error instanceof Error ? error.message : `Action ${action} failed.`, 'error');
      }
    },
    [server, showToast],
  );

  const runBulkAction = useCallback(
    (action: 'cancel' | 'start' | 'archive' | 'delete') => {
      const ids = [...selectedJobIds];
      if (!ids.length) {
        showToast('Select at least one job first.', 'error');
        return;
      }
      if (action === 'delete') {
        setConfirmRequest({
          title: `Delete ${ids.length} job(s)?`,
          body: 'The job records will be permanently removed. Output files are not affected.',
          confirmLabel: 'Delete',
          action: () => executeBulkAction(action, ids),
        });
      } else {
        void executeBulkAction(action, ids);
      }
    },
    [executeBulkAction, selectedJobIds, showToast],
  );

  const handleCancelJob = useCallback(
    (id: string) => {
      void cancelJob(id)
        .then(server.refreshAll)
        .catch(() => showToast('Failed to cancel job.', 'error'));
    },
    [server.refreshAll, showToast],
  );

  const handleDeleteJob = useCallback(
    (id: string) =>
      setConfirmRequest({
        title: 'Delete job?',
        body: 'The job record will be permanently removed. Output files are not affected.',
        confirmLabel: 'Delete',
        action: () => executeBulkAction('delete', [id]),
      }),
    [executeBulkAction],
  );

  const handleClearOutputs = useCallback(
    () =>
      setConfirmRequest({
        title: 'Delete all output files?',
        body: 'Every converted file in the outputs folder will be permanently deleted.',
        confirmLabel: 'Delete All',
        action: async () => {
          try {
            const result = await clearOutputs();
            showToast(`Deleted ${result.deleted} output files.`, 'success');
            await server.refreshAll();
          } catch {
            showToast('Failed to clear outputs.', 'error');
          }
        },
      }),
    [server, showToast],
  );

  const handleDeleteOutput = useCallback(
    (filename: string) =>
      setConfirmRequest({
        title: `Delete ${filename}?`,
        body: 'This converted output will be permanently deleted.',
        confirmLabel: 'Delete',
        action: async () => {
          await deleteOutput(filename);
          showToast('Output deleted.', 'success');
          await server.refreshAll();
        },
      }),
    [server, showToast],
  );

  const runBatchAction = useCallback(
    (batchId: string, action: 'cancel' | 'retry' | 'archive' | 'delete') => {
      const execute = async () => {
        const response = await actOnBatch(batchId, action);
        showToast(
          `${response.result.updated?.length ?? 0} job(s) ${
            action === 'retry' ? 'queued' : `${action}ed`
          }.`,
          'success',
        );
        await server.refreshAll();
      };
      if (action === 'delete') {
        setConfirmRequest({
          title: 'Delete this batch?',
          body: 'All non-running job records in this batch will be removed.',
          confirmLabel: 'Delete Batch',
          action: execute,
        });
      } else {
        void execute().catch((error) =>
          showToast(error instanceof Error ? error.message : 'Batch action failed.', 'error'),
        );
      }
    },
    [server, showToast],
  );

  const submitBatch = useCallback(async () => {
    const selected = staged.filter((item) => item.selected);
    if (!selected.length) {
      showToast('Select staged items.', 'error');
      return;
    }
    setSubmitting(true);
    try {
      const payload = selected.map((item) => ({
        input_filename: item.name,
        source_root_key: item.uploaded ? undefined : item.rootKey,
        source_path: item.uploaded ? undefined : item.sourcePath,
        profile: deriveProfile(settings.video_export),
        video_export: settings.video_export,
        audio_export: settings.audio_export,
        subtitle_export: settings.subtitle_export,
        subtitle_language: settings.subtitle_language || null,
        quality_crf: settings.quality_crf,
        target_video_bitrate: settings.target_video_bitrate || null,
        audio_bitrate_kbps: settings.audio_bitrate_kbps,
        resolution: settings.resolution,
        encoder_preset: settings.encoder_preset,
        hardware_acceleration: settings.hardware_acceleration,
        max_attempts: settings.max_attempts,
        priority: settings.priority,
      }));
      const validation = await validateJobs(payload);
      if (validation.invalid_count) {
        const first = validation.items.find((item) => !item.valid);
        showToast(
          `${validation.invalid_count} file(s) failed validation${first?.message ? `: ${first.message}` : '.'}`,
          'error',
        );
        return;
      }
      const response = await createJobsBatch(payload);
      setStaged((current) =>
        current.filter((item) => !selected.some((submitted) => submitted.id === item.id)),
      );
      showToast(`${response.jobs.length} jobs queued successfully.`, 'success');
      await server.refreshAll();
      navigate('/dashboard');
    } catch (error) {
      showToast(error instanceof Error ? error.message : 'Failed to create jobs.', 'error');
    } finally {
      setSubmitting(false);
    }
  }, [navigate, server, settings, showToast, staged]);

  const persistPresets = useCallback((next: LocalPreset[]) => {
    localStorage.setItem(presetStorageKey, JSON.stringify(next));
    setPresets(next);
  }, []);
  const resetPresetForm = useCallback(() => {
    setEditingPresetId(null);
    setPresetName('');
    setPresetDescription('');
    setPresetSettings(defaultSettings);
  }, []);
  const startEditPreset = useCallback((preset: LocalPreset) => {
    setEditingPresetId(preset.id);
    setPresetName(preset.name);
    setPresetDescription(preset.description ?? '');
    setPresetSettings(preset.settings);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);
  const savePreset = useCallback(() => {
    const name = presetName.trim();
    if (!name) return;
    const now = new Date().toISOString();
    const next = editingPresetId
      ? presets.map((preset) =>
          preset.id === editingPresetId
            ? {
                ...preset,
                name,
                description: presetDescription.trim(),
                settings: { ...presetSettings },
                updatedAt: now,
              }
            : preset,
        )
      : [
          ...presets,
          {
            id: createPresetId(),
            name,
            description: presetDescription.trim(),
            settings: { ...presetSettings },
            createdAt: now,
            updatedAt: now,
          },
        ];
    persistPresets(next);
    resetPresetForm();
  }, [
    editingPresetId,
    persistPresets,
    presetDescription,
    presetName,
    presetSettings,
    presets,
    resetPresetForm,
  ]);
  const deletePreset = useCallback(
    (id: string) => {
      persistPresets(presets.filter((preset) => preset.id !== id));
      if (editingPresetId === id) resetPresetForm();
    },
    [editingPresetId, persistPresets, presets, resetPresetForm],
  );

  if (!isAuthenticated) {
    return <LoginPage onLogin={() => setIsAuthenticated(true)} />;
  }

  const contextValue = {
    roots: server.roots,
    selectedRootKey,
    setSelectedRootKey,
    currentPath,
    browserQuery,
    setBrowserQuery,
    entries,
    selectedPaths,
    setSelectedPaths,
    staged,
    setStaged,
    browserLoading,
    openPath,
    settings,
    setSettings,
    submitting,
    submitBatch,
    jobs: server.jobs,
    filteredJobs,
    jobsLoading: server.jobsLoading,
    selectedJobIds,
    setSelectedJobIds,
    filters,
    setFilters,
    summary,
    outputs: server.outputs,
    batches: server.batches,
    workerHealth: server.workerHealth,
    hasNextJobs: Boolean(server.hasNextJobs),
    hasNextOutputs: Boolean(server.hasNextOutputs),
    loadMoreJobs: () => void server.fetchNextJobs(),
    loadMoreOutputs: () => void server.fetchNextOutputs(),
    runBulkAction,
    handleCancelJob,
    handleDeleteJob,
    handleClearOutputs,
    handleDeleteOutput,
    runBatchAction,
    showToast,
    presets,
    editingPresetId,
    presetName,
    setPresetName,
    presetDescription,
    setPresetDescription,
    presetSettings,
    setPresetSettings,
    resetPresetForm,
    savePreset,
    startEditPreset,
    deletePreset,
  };

  return (
    <AppProvider value={contextValue}>
      <header className="app-header">
        <div className="header-container">
          <div className="header-left">
            <button className="mobile-menu-btn" onClick={() => setIsMobileMenuOpen(true)}>
              <Menu size={20} />
            </button>
            <div className="brand">
              <div className="brand-icon">
                <Video size={16} />
              </div>
              <span className="font-semibold text-sm tracking-tight text-zinc-100 hidden-xs">
                Video Converter
              </span>
              <span className="brand-version hidden-xs">v{__APP_VERSION__}</span>
            </div>
            <nav className={`nav-tabs ${isMobileMenuOpen ? 'mobile-open' : ''}`}>
              <div className="mobile-nav-header">
                <span className="font-semibold text-zinc-100">Menu</span>
                <button className="btn-icon" onClick={() => setIsMobileMenuOpen(false)}>
                  <X size={18} />
                </button>
              </div>
              {[
                ['/dashboard', translate(language, 'dashboard')],
                ['/convert', translate(language, 'convert')],
                ['/presets', translate(language, 'presets')],
                ['/settings', translate(language, 'settings')],
              ].map(([path, label]) => (
                <Link
                  key={path}
                  href={path}
                  className={`nav-tab ${
                    location === path || (path === '/dashboard' && location.startsWith('/jobs/'))
                      ? 'active'
                      : ''
                  }`}
                  onClick={() => setIsMobileMenuOpen(false)}
                >
                  {label}
                </Link>
              ))}
            </nav>
            {isMobileMenuOpen && (
              <button
                className="mobile-overlay"
                onClick={() => setIsMobileMenuOpen(false)}
                aria-label="Close navigation"
              />
            )}
          </div>
          <div className="header-right">
            <div className="service-status">
              <HealthPill label="API" ok={server.apiHealthy} />
              <HealthPill label="Redis" ok={server.redisHealthy} />
              <HealthPill
                label="Worker"
                ok={server.workerHealth?.status === 'ok'}
                meta={
                  server.workerHealth ? `${server.workerHealth.running_jobs} Active` : undefined
                }
              />
            </div>
            <button
              className="btn btn-outline"
              onClick={() =>
                void server
                  .refreshAll()
                  .then(() => showToast('Data refreshed successfully.', 'success'))
              }
              disabled={server.jobsRefreshing}
            >
              <RotateCw size={14} className={server.jobsRefreshing ? 'spin' : ''} />
              <span className="hidden-xs">{translate(language, 'refresh')}</span>
            </button>
            <button
              className="btn btn-outline"
              onClick={() => {
                const next = language === 'en' ? 'tr' : 'en';
                localStorage.setItem('video-converter-language', next);
                document.documentElement.lang = next;
                setLanguage(next);
              }}
              aria-label="Change language"
            >
              {language.toUpperCase()}
            </button>
            <button
              className="btn btn-outline"
              onClick={() => {
                setAuthToken(null);
                window.location.reload();
              }}
              aria-label={translate(language, 'signOut')}
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </header>

      <main className="main-container">
        <Suspense fallback={<div className="p-4 text-zinc-500">Loading page...</div>}>
          <Switch>
            <Route path="/dashboard">
              <DashboardPage />
            </Route>
            <Route path="/jobs/:jobId">
              <DashboardPage />
            </Route>
            <Route path="/convert">
              <ConvertPage />
            </Route>
            <Route path="/presets">
              <PresetsPage />
            </Route>
            <Route path="/settings">
              <SettingsPage />
            </Route>
            <Route>
              <Redirect to="/dashboard" replace />
            </Route>
          </Switch>
        </Suspense>
      </main>

      <footer className="app-footer">
        <div className="footer-container">
          <div className="footer-left">
            <span>Last Sync: {server.lastSync ? formatDate(server.lastSync) : 'Never'}</span>
            <span className="footer-sep">|</span>
            <span className="flex items-center gap-1">
              <span className={`status-dot ${server.streamState === 'live' ? 'ok' : 'error'}`} />
              <span>{server.streamState === 'live' ? 'Live Stream Active' : 'Polling'}</span>
            </span>
          </div>
          <span>v{__APP_VERSION__}</span>
        </div>
      </footer>

      <ConfirmDialog
        open={confirmRequest !== null}
        title={confirmRequest?.title ?? ''}
        body={confirmRequest?.body ?? ''}
        confirmLabel={confirmRequest?.confirmLabel}
        onCancel={() => setConfirmRequest(null)}
        onConfirm={() => {
          const action = confirmRequest?.action;
          setConfirmRequest(null);
          void action?.();
        }}
      />
    </AppProvider>
  );
}
