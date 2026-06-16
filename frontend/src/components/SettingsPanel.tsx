import React, { useState, useEffect } from 'react';
import { CheckCircle2, AlertTriangle } from 'lucide-react';
import { updateCredentials, getSystemSettings, updateSystemSettings } from '../api';

export function SettingsPanel({ showToast }: { showToast: (msg: string, type: 'success' | 'error' | 'info') => void }) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const [workerConcurrency, setWorkerConcurrency] = useState(1);
  const [settingsLoading, setSettingsLoading] = useState(false);

  useEffect(() => {
    getSystemSettings()
      .then((settings) => {
        setWorkerConcurrency(settings.worker_concurrency || 1);
      })
      .catch((err) => {
        console.error('Failed to load system settings:', err);
      });
  }, []);

  const handleSettingsSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSettingsLoading(true);
    try {
      await updateSystemSettings({ worker_concurrency: workerConcurrency });
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Performance Settings */}
      <div>
        <div className="border-b border-zinc-800 pb-2 mb-4">
          <h3 className="text-sm font-semibold text-zinc-100">Performance Settings</h3>
          <p className="text-xs text-zinc-400 mt-1">Adjust system resources and concurrency limits.</p>
        </div>
        <form onSubmit={handleSettingsSubmit} className="flex flex-col gap-4">
          <div className="form-group" style={{ maxWidth: '24rem' }}>
            <label className="form-label">Parallel Conversions (Worker Concurrency)</label>
            <select
              className="form-input"
              value={workerConcurrency}
              onChange={(e) => setWorkerConcurrency(parseInt(e.target.value) || 1)}
              disabled={settingsLoading}
            >
              {[1, 2, 3, 4, 5, 6, 7, 8].map(num => (
                <option key={num} value={num}>
                  {num} {num === 1 ? 'Job' : 'Jobs'} at a time
                </option>
              ))}
            </select>
            <p className="text-xs text-zinc-500 mt-2">
              For resource-constrained devices like Raspberry Pi, it is recommended to set this to 1.
            </p>
          </div>

          <div>
            <button type="submit" className="btn btn-primary" disabled={settingsLoading}>
              {settingsLoading ? 'Saving...' : 'Save Settings'}
            </button>
          </div>
        </form>
      </div>

      {/* Security Settings (Compact) */}
      <details className="group border border-zinc-800 rounded-md p-4 bg-zinc-900/50">
        <summary className="text-sm font-semibold text-zinc-100 cursor-pointer list-none flex items-center justify-between">
          <span>Security Settings</span>
          <span className="text-zinc-500 text-xs transition-transform duration-200 group-open:rotate-180">
            ▼
          </span>
        </summary>
        <div className="pt-4 mt-4 border-t border-zinc-800">
          <p className="text-xs text-zinc-400 mb-4">Update your username and password.</p>
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }} autoComplete="off">
            <div className="form-group" style={{ maxWidth: '24rem' }}>
              <label className="form-label">Current Password</label>
              <input
                type="password"
                className="form-input"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="Your current password"
                disabled={loading}
                autoComplete="current-password"
                name="current-password"
              />
            </div>

            <div className="form-grid" style={{ maxWidth: '32rem' }}>
              <div className="form-group">
                <label className="form-label">New Username</label>
                <input
                  type="text"
                  className="form-input"
                  value={newUsername}
                  onChange={(e) => setNewUsername(e.target.value)}
                  placeholder="Leave blank to keep current"
                  disabled={loading}
                  autoComplete="off"
                  name="new-username"
                  data-lpignore="true"
                />
              </div>
              <div className="form-group">
                <label className="form-label">New Password</label>
                <input
                  type="password"
                  className="form-input"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Leave blank to keep current"
                  disabled={loading}
                  autoComplete="new-password"
                  name="new-password"
                />
              </div>
            </div>

            <div className="pt-2">
              <button type="submit" className="btn btn-primary" disabled={loading}>
                {loading ? 'Updating...' : 'Update Credentials'}
              </button>
            </div>
          </form>
        </div>
      </details>
    </div>
  );
}
