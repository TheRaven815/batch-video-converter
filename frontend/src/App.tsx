import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import {
  LayoutDashboard,
  LogOut,
  RotateCw,
  Settings,
  SlidersHorizontal,
  Video,
  WandSparkles,
} from 'lucide-react';
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
import { useI18n } from './i18n';

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
  const { language, t, toggleLanguage } = useI18n();
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
        showToast(t('toast.browseFailed'), 'error');
      } finally {
        setBrowserLoading(false);
      }
    },
    [selectedRootKey, showToast, t],
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
      const resultKey = {
        cancel: 'result.cancelled',
        start: 'result.started',
        archive: 'result.archived',
        delete: 'result.deleted',
      } as const;
      const actionLabel = {
        cancel: t('common.cancel'),
        start: t('common.start'),
        archive: t('common.archive'),
        delete: t('common.delete'),
      };
      try {
        const result = await actions[action](ids);
        setSelectedJobIds(new Set());
        const message = t('toast.bulkResult', {
          updated: result.updated.length,
          action: t(resultKey[action]),
          skipped: result.skipped.length
            ? t('toast.bulkSkipped', { count: result.skipped.length })
            : '',
        });
        showToast(message.trim(), result.updated.length ? 'success' : 'info');
        await server.refreshAll();
      } catch (error) {
        showToast(
          error instanceof Error
            ? error.message
            : t('toast.actionFailed', { action: actionLabel[action] }),
          'error',
        );
      }
    },
    [server, showToast, t],
  );

  const runBulkAction = useCallback(
    (action: 'cancel' | 'start' | 'archive' | 'delete') => {
      const ids = [...selectedJobIds];
      if (!ids.length) {
        showToast(t('toast.selectJob'), 'error');
        return;
      }
      if (action === 'delete') {
        setConfirmRequest({
          title: t('confirm.deleteJobs.title', { count: ids.length }),
          body: t('confirm.deleteJobs.body'),
          confirmLabel: t('common.delete'),
          action: () => executeBulkAction(action, ids),
        });
      } else {
        void executeBulkAction(action, ids);
      }
    },
    [executeBulkAction, selectedJobIds, showToast, t],
  );

  const handleCancelJob = useCallback(
    (id: string) => {
      void cancelJob(id)
        .then(server.refreshAll)
        .catch(() => showToast(t('toast.cancelFailed'), 'error'));
    },
    [server.refreshAll, showToast, t],
  );

  const handleDeleteJob = useCallback(
    (id: string) =>
      setConfirmRequest({
        title: t('confirm.deleteJob.title'),
        body: t('confirm.deleteJob.body'),
        confirmLabel: t('common.delete'),
        action: () => executeBulkAction('delete', [id]),
      }),
    [executeBulkAction, t],
  );

  const handleClearOutputs = useCallback(
    () =>
      setConfirmRequest({
        title: t('confirm.deleteOutputs.title'),
        body: t('confirm.deleteOutputs.body'),
        confirmLabel: t('confirm.deleteOutputs.action'),
        action: async () => {
          try {
            const result = await clearOutputs();
            showToast(t('toast.outputsDeleted', { count: result.deleted }), 'success');
            await server.refreshAll();
          } catch {
            showToast(t('toast.outputsClearFailed'), 'error');
          }
        },
      }),
    [server, showToast, t],
  );

  const handleDeleteOutput = useCallback(
    (filename: string) =>
      setConfirmRequest({
        title: t('confirm.deleteOutput.title', { filename }),
        body: t('confirm.deleteOutput.body'),
        confirmLabel: t('common.delete'),
        action: async () => {
          await deleteOutput(filename);
          showToast(t('toast.outputDeleted'), 'success');
          await server.refreshAll();
        },
      }),
    [server, showToast, t],
  );

  const runBatchAction = useCallback(
    (batchId: string, action: 'cancel' | 'retry' | 'archive' | 'delete') => {
      const execute = async () => {
        const response = await actOnBatch(batchId, action);
        const resultKey = {
          cancel: 'result.cancelled',
          retry: 'result.queued',
          archive: 'result.archived',
          delete: 'result.deleted',
        } as const;
        showToast(
          t('toast.batchResult', {
            count: response.result.updated?.length ?? 0,
            action: t(resultKey[action]),
          }),
          'success',
        );
        await server.refreshAll();
      };
      if (action === 'delete') {
        setConfirmRequest({
          title: t('confirm.deleteBatch.title'),
          body: t('confirm.deleteBatch.body'),
          confirmLabel: t('confirm.deleteBatch.action'),
          action: execute,
        });
      } else {
        void execute().catch((error) =>
          showToast(error instanceof Error ? error.message : t('toast.batchFailed'), 'error'),
        );
      }
    },
    [server, showToast, t],
  );

  const submitBatch = useCallback(async () => {
    const selected = staged.filter((item) => item.selected);
    if (!selected.length) {
      showToast(t('toast.selectStaged'), 'error');
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
        audio_stream_indexes:
          settings.audio_stream_indexes ?? settings.audioStreamIndexes ?? null,
        subtitle_stream_indexes:
          settings.subtitle_stream_indexes ?? settings.subtitleStreamIndexes ?? null,
        audio_channel_mode:
          settings.audio_channel_mode ?? settings.audioChannelMode ?? 'preserve',
        skip_existing_output:
          settings.skip_existing_output ?? settings.skipExistingOutput ?? false,
      }));
      const validation = await validateJobs(payload);
      if (validation.invalid_count) {
        const first = validation.items.find((item) => !item.valid);
        showToast(
          t('toast.validationFailed', {
            count: validation.invalid_count,
            detail: first?.message ? `: ${first.message}` : '.',
          }),
          'error',
        );
        return;
      }
      const response = await createJobsBatch(payload);
      setStaged((current) =>
        current.filter((item) => !selected.some((submitted) => submitted.id === item.id)),
      );
      showToast(t('toast.jobsQueued', { count: response.jobs.length }), 'success');
      await server.refreshAll();
      navigate('/dashboard');
    } catch (error) {
      showToast(error instanceof Error ? error.message : t('toast.jobsCreateFailed'), 'error');
    } finally {
      setSubmitting(false);
    }
  }, [navigate, server, settings, showToast, staged, t]);

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
            <div className="brand">
              <div className="brand-icon">
                <Video size={16} />
              </div>
              <span className="brand-name font-semibold text-sm tracking-tight text-zinc-100">
                {t('app.name')}
              </span>
              <span className="brand-version hidden-xs">v{__APP_VERSION__}</span>
            </div>
            <nav className="nav-tabs">
              {[
                ['/dashboard', t('nav.dashboard'), LayoutDashboard],
                ['/convert', t('nav.convert'), WandSparkles],
                ['/presets', t('nav.presets'), SlidersHorizontal],
                ['/settings', t('nav.settings'), Settings],
              ].map(([path, label, Icon]) => {
                const NavIcon = Icon as typeof LayoutDashboard;
                return (
                  <Link
                    key={path as string}
                    href={path as string}
                    className={`nav-tab ${
                      location === path || (path === '/dashboard' && location.startsWith('/jobs/'))
                        ? 'active'
                        : ''
                    }`}
                  >
                    <NavIcon className="nav-icon" size={15} aria-hidden="true" />
                    <span>{label as string}</span>
                  </Link>
                );
              })}
            </nav>
          </div>
          <div className="header-right">
            <div className="service-status">
              <HealthPill label="API" ok={server.apiHealthy} />
              <HealthPill label="Redis" ok={server.redisHealthy} />
              <HealthPill
                label={t('service.worker')}
                ok={server.workerHealth?.status === 'ok'}
                meta={
                  server.workerHealth
                    ? `${server.workerHealth.running_jobs} ${t('common.active')}`
                    : undefined
                }
              />
            </div>
            <button
              className="btn btn-outline header-action"
              onClick={() =>
                void server.refreshAll().then(() => showToast(t('toast.refreshSuccess'), 'success'))
              }
              disabled={server.jobsRefreshing}
              title={t('common.refresh')}
            >
              <RotateCw size={14} className={server.jobsRefreshing ? 'spin' : ''} />
              <span className="hidden-xs">{t('common.refresh')}</span>
            </button>
            <button
              className="btn btn-outline header-action language-action"
              onClick={toggleLanguage}
              aria-label={t('nav.changeLanguage')}
            >
              {language.toUpperCase()}
            </button>
            <button
              className="btn btn-outline header-action"
              onClick={() => {
                setAuthToken(null);
                window.location.reload();
              }}
              aria-label={t('nav.signOut')}
              title={t('nav.signOut')}
            >
              <LogOut size={14} />
            </button>
          </div>
        </div>
      </header>

      <main className="main-container">
        <Suspense fallback={<div className="p-4 text-zinc-500">{t('loading.page')}</div>}>
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
            <span>
              {t('footer.lastSync', {
                value: server.lastSync ? formatDate(server.lastSync, language) : t('common.never'),
              })}
            </span>
            <span className="footer-sep">|</span>
            <span className="flex items-center gap-1">
              <span className={`status-dot ${server.streamState === 'live' ? 'ok' : 'error'}`} />
              <span>{server.streamState === 'live' ? t('footer.live') : t('footer.polling')}</span>
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
