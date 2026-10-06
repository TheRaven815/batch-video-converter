import React, { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { updateCredentials, getSystemSettings, updateSystemSettings, setAuthToken } from '../api';
import type { SystemSettings } from '../models';
import { audioOptions, defaultSettings, subtitleOptions, videoOptions } from '../utils/constants';
import { useI18n } from '../i18n';

const defaultSystemSettings: SystemSettings = {
  worker_concurrency: 1,
  worker_concurrency_limit: 1,
  default_export: {
    profile: 'h264_mp4',
    video_export: defaultSettings.video_export,
    audio_export: defaultSettings.audio_export,
    subtitle_export: defaultSettings.subtitle_export,
    subtitle_language: null,
  },
  auto_cleanup: {
    enabled: false,
    retention_days: 30,
    keep_minimum_outputs: 10,
    delete_terminal_jobs: true,
    job_retention_days: 90,
  },
  retry: {
    enabled: true,
    max_attempts: 3,
    initial_backoff_seconds: 10,
    max_backoff_seconds: 300,
  },
  disk_safety: { minimum_free_bytes: 536870912 },
  ui: {
    theme: 'dark',
    density: 'comfortable',
  },
};

export function SettingsPanel({
  showToast,
}: {
  showToast: (msg: string, type: 'success' | 'error' | 'info') => void;
}) {
  const { t } = useI18n();
  const [currentPassword, setCurrentPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const [systemSettings, setSystemSettings] = useState<SystemSettings>(defaultSystemSettings);
  const queryClient = useQueryClient();
  const settingsQuery = useQuery({
    queryKey: ['server', 'settings'],
    queryFn: getSystemSettings,
  });
  const settingsMutation = useMutation({
    mutationFn: updateSystemSettings,
    onSuccess: (saved) => {
      queryClient.setQueryData(['server', 'settings'], saved);
      setSystemSettings(saved);
      showToast(t('settings.updated'), 'success');
      const theme =
        saved.ui.theme === 'system'
          ? window.matchMedia('(prefers-color-scheme: light)').matches
            ? 'light'
            : 'dark'
          : saved.ui.theme;
      document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.density = saved.ui.density;
    },
    onError: (error) => {
      showToast(error instanceof Error ? error.message : t('settings.updateFailed'), 'error');
    },
  });
  const settingsLoading = settingsQuery.isLoading || settingsMutation.isPending;
  const settingsLoadError = settingsQuery.isError;

  useEffect(() => {
    if (!settingsQuery.data) return;
    setSystemSettings({
      ...defaultSystemSettings,
      ...settingsQuery.data,
      default_export: {
        ...defaultSystemSettings.default_export,
        ...settingsQuery.data.default_export,
      },
      auto_cleanup: { ...defaultSystemSettings.auto_cleanup, ...settingsQuery.data.auto_cleanup },
      ui: { ...defaultSystemSettings.ui, ...settingsQuery.data.ui },
    });
  }, [settingsQuery.data]);

  const patchSettings = (patch: Partial<SystemSettings>) => {
    setSystemSettings((current) => ({ ...current, ...patch }));
  };

  const handleSettingsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const payload: SystemSettings = {
      ...systemSettings,
      default_export: {
        ...systemSettings.default_export,
        subtitle_language: systemSettings.default_export.subtitle_language?.trim() || null,
      },
    };
    settingsMutation.mutate(payload);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPassword) {
      showToast(t('settings.currentPasswordRequired'), 'error');
      return;
    }
    if (!newUsername && !newPassword) {
      showToast(t('settings.newCredentialRequired'), 'error');
      return;
    }

    setLoading(true);

    try {
      await updateCredentials(currentPassword, newUsername, newPassword);
      // Changing credentials revokes all previously issued tokens server-side,
      // so drop the local session and return to the login page.
      showToast(t('settings.credentialsUpdated'), 'success');
      setAuthToken(null);
      window.location.reload();
    } catch (err) {
      showToast(err instanceof Error ? err.message : t('settings.updateFailed'), 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="settings-stack">
      <form onSubmit={handleSettingsSubmit} className="settings-form">
        <div className="settings-toolbar">
          <div>
            <span className="form-section-title">{t('settings.conversionPreferences')}</span>
            <p className="settings-help">{t('settings.preferencesHelp')}</p>
          </div>
          <button type="submit" className="btn btn-primary" disabled={settingsLoading || !audioOptions[systemSettings.default_export.video_export].includes(systemSettings.default_export.audio_export)}>
            {settingsLoading ? t('settings.wait') : t('settings.save')}
          </button>
        </div>

        {settingsLoadError && (
          <div className="form-alert-error" role="alert">
            {t('settings.loadFailed')}
          </div>
        )}

        <div className="settings-section-grid">
          <section
            className="settings-section settings-section-wide"
            aria-labelledby="processing-settings-title"
          >
            <div className="settings-section-header">
              <span className="form-section-title" id="processing-settings-title">
                {t('settings.processing')}
              </span>
              <span className="badge badge-running">
                {t('settings.activeCount', { count: systemSettings.worker_concurrency })}
              </span>
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label" htmlFor="worker-concurrency">
                  {t('settings.parallelConversions')}
                </label>
                <select
                  id="worker-concurrency"
                  className="form-input"
                  value={systemSettings.worker_concurrency}
                  onChange={(e) =>
                    patchSettings({ worker_concurrency: parseInt(e.target.value) || 1 })
                  }
                  disabled={settingsLoading}
                >
                  {Array.from({ length: systemSettings.worker_concurrency_limit }, (_, index) => index + 1).map((num) => (
                    <option key={num} value={num}>
                      {t('settings.jobsAtTime', { count: num })}
                    </option>
                  ))}
                </select>
                <p className="settings-help">{t('settings.concurrencyHelp')}</p>
                <p className="settings-help">{t('settings.concurrencyLimit', { count: systemSettings.worker_concurrency_limit })}</p>
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="default-profile">
                  {t('settings.defaultProfile')}
                </label>
                <select
                  id="default-profile"
                  className="form-input"
                  value={systemSettings.default_export.profile}
                  onChange={(e) =>
                    patchSettings({
                      default_export: {
                        ...systemSettings.default_export,
                        profile: e.target.value as SystemSettings['default_export']['profile'],
                      },
                    })
                  }
                  disabled={settingsLoading}
                >
                  <option value="h264_mp4">H.264 / MP4</option>
                  <option value="h265_mp4">H.265 / MP4</option>
                  <option value="vp9_webm">VP9 / WebM</option>
                </select>
              </div>
            </div>
          </section>

          <section
            className="settings-section settings-section-wide"
            aria-labelledby="export-defaults-title"
          >
            <span className="form-section-title" id="export-defaults-title">
              {t('settings.exportDefaults')}
            </span>
            <div className="settings-fields four-columns">
              <div className="form-group">
                <label className="form-label" htmlFor="default-container">
                  {t('settings.defaultContainer')}
                </label>
                <select
                  id="default-container"
                  className="form-input"
                  value={systemSettings.default_export.video_export}
                  onChange={(e) =>
                    patchSettings({
                      default_export: {
                        ...systemSettings.default_export,
                        video_export: e.target
                          .value as SystemSettings['default_export']['video_export'],
                      },
                    })
                  }
                  disabled={settingsLoading}
                >
                  {videoOptions.map((option) => (
                    <option key={option} value={option}>
                      {option.toUpperCase()}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="default-audio">
                  {t('settings.defaultAudio')}
                </label>
                <select
                  id="default-audio"
                  className="form-input"
                  value={systemSettings.default_export.audio_export}
                  onChange={(e) =>
                    patchSettings({
                      default_export: {
                        ...systemSettings.default_export,
                        audio_export: e.target
                          .value as SystemSettings['default_export']['audio_export'],
                      },
                    })
                  }
                  disabled={settingsLoading}
                >
                  {!audioOptions[systemSettings.default_export.video_export].includes(systemSettings.default_export.audio_export) && (
                    <option value={systemSettings.default_export.audio_export} disabled>{systemSettings.default_export.audio_export.toUpperCase()}</option>
                  )}
                  {audioOptions[systemSettings.default_export.video_export].map((option) => (
                    <option key={option} value={option}>
                      {option === 'copy' ? t('convert.copyOriginal') : option.toUpperCase()}
                    </option>
                  ))}
                </select>
                {!audioOptions[systemSettings.default_export.video_export].includes(systemSettings.default_export.audio_export) && (
                  <p className="form-alert-error" role="alert">{t('convert.incompatibleAudio')}</p>
                )}
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="default-subtitle-mode">
                  {t('settings.defaultSubtitleMode')}
                </label>
                <select
                  id="default-subtitle-mode"
                  className="form-input"
                  value={systemSettings.default_export.subtitle_export}
                  onChange={(e) =>
                    patchSettings({
                      default_export: {
                        ...systemSettings.default_export,
                        subtitle_export: e.target
                          .value as SystemSettings['default_export']['subtitle_export'],
                      },
                    })
                  }
                  disabled={settingsLoading}
                >
                  {subtitleOptions.map((option) => (
                    <option key={option} value={option}>
                      {option === 'none'
                        ? t('common.none')
                        : option === 'embedded'
                          ? t('convert.embedded')
                          : t('convert.separateSrt')}
                    </option>
                  ))}
                </select>
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="default-subtitle-language">
                  {t('settings.defaultSubtitleLanguage')}
                </label>
                <input
                  id="default-subtitle-language"
                  className="form-input"
                  value={systemSettings.default_export.subtitle_language || ''}
                  onChange={(e) =>
                    patchSettings({
                      default_export: {
                        ...systemSettings.default_export,
                        subtitle_language: e.target.value,
                      },
                    })
                  }
                  disabled={settingsLoading}
                  placeholder={t('settings.subtitleLanguageHint')}
                />
              </div>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="cleanup-settings-title">
            <span className="form-section-title" id="cleanup-settings-title">
              {t('settings.cleanup')}
            </span>
            <label className="form-toggle-row settings-toggle-row" htmlFor="auto-cleanup-enabled">
              <span className="form-toggle-info">
                <p>{t('settings.autoCleanup')}</p>
                <p>{t('settings.autoCleanupHelp')}</p>
              </span>
              <input
                id="auto-cleanup-enabled"
                className="form-checkbox"
                type="checkbox"
                checked={systemSettings.auto_cleanup.enabled}
                onChange={(e) =>
                  patchSettings({
                    auto_cleanup: { ...systemSettings.auto_cleanup, enabled: e.target.checked },
                  })
                }
                disabled={settingsLoading}
              />
            </label>
            <div className="form-grid settings-mini-grid">
              <div className="form-group">
                <label className="form-label" htmlFor="retention-days">
                  {t('settings.retentionDays')}
                </label>
                <input
                  id="retention-days"
                  type="number"
                  min={1}
                  max={365}
                  className="form-input"
                  value={systemSettings.auto_cleanup.retention_days}
                  onChange={(e) =>
                    patchSettings({
                      auto_cleanup: {
                        ...systemSettings.auto_cleanup,
                        retention_days: parseInt(e.target.value) || 30,
                      },
                    })
                  }
                  disabled={settingsLoading}
                />
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="keep-minimum-outputs">
                  {t('settings.keepMinimumOutputs')}
                </label>
                <input
                  id="keep-minimum-outputs"
                  type="number"
                  min={0}
                  max={10000}
                  className="form-input"
                  value={systemSettings.auto_cleanup.keep_minimum_outputs}
                  onChange={(e) =>
                    patchSettings({
                      auto_cleanup: {
                        ...systemSettings.auto_cleanup,
                        keep_minimum_outputs: parseInt(e.target.value) || 0,
                      },
                    })
                  }
                  disabled={settingsLoading}
                />
              </div>
              <label className="form-toggle-row settings-toggle-row">
                <span className="form-toggle-info">
                  <p>{t('settings.deleteTerminalJobs')}</p>
                  <p>{t('settings.deleteTerminalJobsHelp')}</p>
                </span>
                <input
                  className="form-checkbox"
                  type="checkbox"
                  checked={systemSettings.auto_cleanup.delete_terminal_jobs ?? true}
                  onChange={(event) =>
                    patchSettings({
                      auto_cleanup: {
                        ...systemSettings.auto_cleanup,
                        delete_terminal_jobs: event.target.checked,
                      },
                    })
                  }
                />
              </label>
              <div className="form-group">
                <label className="form-label">{t('settings.jobRetentionDays')}</label>
                <input
                  className="form-input"
                  type="number"
                  min={1}
                  max={3650}
                  value={systemSettings.auto_cleanup.job_retention_days ?? 90}
                  onChange={(event) =>
                    patchSettings({
                      auto_cleanup: {
                        ...systemSettings.auto_cleanup,
                        job_retention_days: Number(event.target.value),
                      },
                    })
                  }
                />
              </div>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="retry-settings-title">
            <span className="form-section-title" id="retry-settings-title">
              {t('settings.retrySafety')}
            </span>
            <label className="form-toggle-row settings-toggle-row">
              <span className="form-toggle-info">
                <p>{t('settings.retryFfmpeg')}</p>
                <p>{t('settings.retryHelp')}</p>
              </span>
              <input
                className="form-checkbox"
                type="checkbox"
                checked={systemSettings.retry?.enabled ?? true}
                onChange={(event) =>
                  patchSettings({
                    retry: {
                      ...(systemSettings.retry ?? defaultSystemSettings.retry!),
                      enabled: event.target.checked,
                    },
                  })
                }
              />
            </label>
            <div className="form-grid settings-mini-grid">
              <div className="form-group">
                <label className="form-label">{t('settings.maximumAttempts')}</label>
                <input
                  className="form-input"
                  type="number"
                  min={1}
                  max={10}
                  value={systemSettings.retry?.max_attempts ?? 3}
                  onChange={(event) =>
                    patchSettings({
                      retry: {
                        ...(systemSettings.retry ?? defaultSystemSettings.retry!),
                        max_attempts: Number(event.target.value),
                      },
                    })
                  }
                />
              </div>
              <div className="form-group">
                <label className="form-label">{t('settings.minimumDisk')}</label>
                <input
                  className="form-input"
                  type="number"
                  min={0}
                  value={Math.round(
                    (systemSettings.disk_safety?.minimum_free_bytes ?? 536870912) / 1048576,
                  )}
                  onChange={(event) =>
                    patchSettings({
                      disk_safety: {
                        minimum_free_bytes: Number(event.target.value) * 1048576,
                      },
                    })
                  }
                />
              </div>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="appearance-settings-title">
            <span className="form-section-title" id="appearance-settings-title">
              {t('settings.appearance')}
            </span>
            <div className="form-grid settings-mini-grid">
              <div className="form-group">
                <label className="form-label">{t('settings.theme')}</label>
                <select
                  className="form-input"
                  value={systemSettings.ui.theme}
                  onChange={(event) =>
                    patchSettings({
                      ui: {
                        ...systemSettings.ui,
                        theme: event.target.value as SystemSettings['ui']['theme'],
                      },
                    })
                  }
                >
                  <option value="dark">{t('settings.themeDark')}</option>
                  <option value="light">{t('settings.themeLight')}</option>
                  <option value="system">{t('settings.themeSystem')}</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">{t('settings.density')}</label>
                <select
                  className="form-input"
                  value={systemSettings.ui.density}
                  onChange={(event) =>
                    patchSettings({
                      ui: {
                        ...systemSettings.ui,
                        density: event.target.value as SystemSettings['ui']['density'],
                      },
                    })
                  }
                >
                  <option value="comfortable">{t('settings.densityComfortable')}</option>
                  <option value="compact">{t('settings.densityCompact')}</option>
                </select>
              </div>
            </div>
          </section>
        </div>
      </form>

      <details className="settings-security-panel">
        <summary className="settings-summary">
          <span>
            <span className="form-section-title">{t('settings.security')}</span>
            <small className="settings-help">{t('settings.securityHelp')}</small>
          </span>
          <span className="settings-chevron">▼</span>
        </summary>
        <form
          onSubmit={handleSubmit}
          className="settings-fields security-fields"
          autoComplete="off"
        >
          <div className="form-group">
            <label className="form-label" htmlFor="current-password">
              {t('settings.currentPassword')}
            </label>
            <input
              id="current-password"
              type="password"
              className="form-input"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder={t('settings.currentPasswordHint')}
              disabled={loading}
              autoComplete="current-password"
              name="current-password"
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="new-username">
              {t('settings.newUsername')}
            </label>
            <input
              id="new-username"
              type="text"
              className="form-input"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              placeholder={t('settings.keepCurrentHint')}
              disabled={loading}
              autoComplete="off"
              name="new-username"
              data-lpignore="true"
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="new-password">
              {t('settings.newPassword')}
            </label>
            <input
              id="new-password"
              type="password"
              className="form-input"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder={t('settings.keepCurrentHint')}
              disabled={loading}
              autoComplete="new-password"
              name="new-password"
            />
          </div>
          <div className="settings-footer security-footer">
            <button type="submit" className="btn btn-primary" disabled={loading}>
              {loading ? t('settings.updating') : t('settings.updateCredentials')}
            </button>
          </div>
        </form>
      </details>
    </div>
  );
}
