import React, { useState, useEffect } from 'react';
import { updateCredentials, getSystemSettings, updateSystemSettings } from '../api';
import type { SystemSettings } from '../models';
import { audioOptions, defaultSettings, subtitleOptions, videoOptions } from '../utils/constants';

const defaultSystemSettings: SystemSettings = {
  worker_concurrency: 1,
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
  },
  ui: {
    theme: 'dark',
    density: 'comfortable',
  },
};

export function SettingsPanel({ showToast }: { showToast: (msg: string, type: 'success' | 'error' | 'info') => void }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const [systemSettings, setSystemSettings] = useState<SystemSettings>(defaultSystemSettings);
  const [settingsLoading, setSettingsLoading] = useState(false);

  useEffect(() => {
    getSystemSettings()
      .then((settings) => {
        setSystemSettings({
          ...defaultSystemSettings,
          ...settings,
          default_export: { ...defaultSystemSettings.default_export, ...settings.default_export },
          auto_cleanup: { ...defaultSystemSettings.auto_cleanup, ...settings.auto_cleanup },
          ui: { ...defaultSystemSettings.ui, ...settings.ui },
        });
      })
      .catch((err) => {
        console.error('Failed to load system settings:', err);
      });
  }, []);

  const patchSettings = (patch: Partial<SystemSettings>) => {
    setSystemSettings((current) => ({ ...current, ...patch }));
  };

  const handleSettingsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSettingsLoading(true);
    try {
      const payload: SystemSettings = {
        ...systemSettings,
        default_export: {
          ...systemSettings.default_export,
          subtitle_language: systemSettings.default_export.subtitle_language?.trim() || null,
        },
      };
      const saved = await updateSystemSettings(payload);
      setSystemSettings(saved);
      showToast('Settings updated successfully!', 'success');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Update failed.', 'error');
    } finally {
      setSettingsLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPassword) {
      showToast('Please enter your current password to proceed.', 'error');
      return;
    }
    if (!newUsername && !newPassword) {
      showToast('Please enter a new username or a new password.', 'error');
      return;
    }

    setLoading(true);

    try {
      await updateCredentials(currentPassword, newUsername, newPassword);
      showToast('Credentials updated successfully!', 'success');
      setCurrentPassword('');
      setNewUsername('');
      setNewPassword('');
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Update failed.', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="settings-stack">
      <form onSubmit={handleSettingsSubmit} className="settings-form">
        <div className="settings-toolbar">
          <div>
            <span className="form-section-title">Conversion preferences</span>
            <p className="settings-help">Persist worker limits, export defaults, cleanup rules, and UI preferences.</p>
          </div>
          <button type="submit" className="btn btn-primary" disabled={settingsLoading}>{settingsLoading ? 'Saving...' : 'Save Settings'}</button>
        </div>

        <div className="settings-section-grid">
          <section className="settings-section settings-section-wide" aria-labelledby="processing-settings-title">
            <div className="settings-section-header">
              <span className="form-section-title" id="processing-settings-title">Processing</span>
              <span className="badge badge-running">{systemSettings.worker_concurrency} active</span>
            </div>
            <div className="form-grid">
              <div className="form-group">
                <label className="form-label" htmlFor="worker-concurrency">Parallel Conversions</label>
                <select id="worker-concurrency" className="form-input" value={systemSettings.worker_concurrency} onChange={(e) => patchSettings({ worker_concurrency: parseInt(e.target.value) || 1 })} disabled={settingsLoading}>
                  {[1, 2, 3, 4, 5, 6, 7, 8].map(num => <option key={num} value={num}>{num} {num === 1 ? 'Job' : 'Jobs'} at a time</option>)}
                </select>
                <p className="settings-help">For resource-constrained devices like Raspberry Pi, keep this at 1.</p>
              </div>
              <div className="form-group">
                <label className="form-label" htmlFor="default-profile">Default Export Profile</label>
                <input id="default-profile" className="form-input" value={systemSettings.default_export.profile} onChange={(e) => patchSettings({ default_export: { ...systemSettings.default_export, profile: e.target.value } })} disabled={settingsLoading} placeholder="h264_mp4" />
              </div>
            </div>
          </section>

          <section className="settings-section settings-section-wide" aria-labelledby="export-defaults-title">
            <span className="form-section-title" id="export-defaults-title">Export Defaults</span>
            <div className="settings-fields four-columns">
              <div className="form-group"><label className="form-label" htmlFor="default-container">Default Container</label><select id="default-container" className="form-input" value={systemSettings.default_export.video_export} onChange={(e) => patchSettings({ default_export: { ...systemSettings.default_export, video_export: e.target.value as SystemSettings['default_export']['video_export'] } })} disabled={settingsLoading}>{videoOptions.map(option => <option key={option} value={option}>{option.toUpperCase()}</option>)}</select></div>
              <div className="form-group"><label className="form-label" htmlFor="default-audio">Default Audio</label><select id="default-audio" className="form-input" value={systemSettings.default_export.audio_export} onChange={(e) => patchSettings({ default_export: { ...systemSettings.default_export, audio_export: e.target.value as SystemSettings['default_export']['audio_export'] } })} disabled={settingsLoading}>{audioOptions.map(option => <option key={option} value={option}>{option}</option>)}</select></div>
              <div className="form-group"><label className="form-label" htmlFor="default-subtitle-mode">Default Subtitle Mode</label><select id="default-subtitle-mode" className="form-input" value={systemSettings.default_export.subtitle_export} onChange={(e) => patchSettings({ default_export: { ...systemSettings.default_export, subtitle_export: e.target.value as SystemSettings['default_export']['subtitle_export'] } })} disabled={settingsLoading}>{subtitleOptions.map(option => <option key={option} value={option}>{option}</option>)}</select></div>
              <div className="form-group"><label className="form-label" htmlFor="default-subtitle-language">Default Subtitle Language</label><input id="default-subtitle-language" className="form-input" value={systemSettings.default_export.subtitle_language || ''} onChange={(e) => patchSettings({ default_export: { ...systemSettings.default_export, subtitle_language: e.target.value } })} disabled={settingsLoading} placeholder="eng, tur, or blank" /></div>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="cleanup-settings-title">
            <span className="form-section-title" id="cleanup-settings-title">Cleanup</span>
            <label className="form-toggle-row settings-toggle-row" htmlFor="auto-cleanup-enabled">
              <span className="form-toggle-info"><p>Enable automatic output cleanup</p><p>Remove old outputs while preserving a minimum number of files.</p></span>
              <input id="auto-cleanup-enabled" className="form-checkbox" type="checkbox" checked={systemSettings.auto_cleanup.enabled} onChange={(e) => patchSettings({ auto_cleanup: { ...systemSettings.auto_cleanup, enabled: e.target.checked } })} disabled={settingsLoading} />
            </label>
            <div className="form-grid settings-mini-grid">
              <div className="form-group"><label className="form-label" htmlFor="retention-days">Retention Days</label><input id="retention-days" type="number" min={1} max={365} className="form-input" value={systemSettings.auto_cleanup.retention_days} onChange={(e) => patchSettings({ auto_cleanup: { ...systemSettings.auto_cleanup, retention_days: parseInt(e.target.value) || 30 } })} disabled={settingsLoading} /></div>
              <div className="form-group"><label className="form-label" htmlFor="keep-minimum-outputs">Keep Minimum Outputs</label><input id="keep-minimum-outputs" type="number" min={0} max={10000} className="form-input" value={systemSettings.auto_cleanup.keep_minimum_outputs} onChange={(e) => patchSettings({ auto_cleanup: { ...systemSettings.auto_cleanup, keep_minimum_outputs: parseInt(e.target.value) || 0 } })} disabled={settingsLoading} /></div>
            </div>
          </section>

          <section className="settings-section" aria-labelledby="ui-settings-title">
            <span className="form-section-title" id="ui-settings-title">Interface</span>
            <div className="form-grid settings-mini-grid">
              <div className="form-group"><label className="form-label" htmlFor="theme-preference">Theme Preference</label><select id="theme-preference" className="form-input" value={systemSettings.ui.theme} onChange={(e) => patchSettings({ ui: { ...systemSettings.ui, theme: e.target.value as SystemSettings['ui']['theme'] } })} disabled={settingsLoading}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option></select></div>
              <div className="form-group"><label className="form-label" htmlFor="ui-density">UI Density</label><select id="ui-density" className="form-input" value={systemSettings.ui.density} onChange={(e) => patchSettings({ ui: { ...systemSettings.ui, density: e.target.value as SystemSettings['ui']['density'] } })} disabled={settingsLoading}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></div>
            </div>
          </section>
        </div>
      </form>

      <details className="settings-security-panel">
        <summary className="settings-summary">
          <span><span className="form-section-title">Security / Credentials</span><small className="settings-help">Update username or password with your current password.</small></span>
          <span className="settings-chevron">▼</span>
        </summary>
        <form onSubmit={handleSubmit} className="settings-fields security-fields" autoComplete="off">
          <div className="form-group"><label className="form-label" htmlFor="current-password">Current Password</label><input id="current-password" type="password" className="form-input" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} placeholder="Your current password" disabled={loading} autoComplete="current-password" name="current-password" /></div>
          <div className="form-group"><label className="form-label" htmlFor="new-username">New Username</label><input id="new-username" type="text" className="form-input" value={newUsername} onChange={(e) => setNewUsername(e.target.value)} placeholder="Leave blank to keep current" disabled={loading} autoComplete="off" name="new-username" data-lpignore="true" /></div>
          <div className="form-group"><label className="form-label" htmlFor="new-password">New Password</label><input id="new-password" type="password" className="form-input" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="Leave blank to keep current" disabled={loading} autoComplete="new-password" name="new-password" /></div>
          <div className="settings-footer security-footer"><button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Updating...' : 'Update Credentials'}</button></div>
        </form>
      </details>
    </div>
  );
}
