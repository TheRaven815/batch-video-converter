import React, { useState } from 'react';
import { updateCredentials } from '../api';

export function SettingsPanel() {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [status, setStatus] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentPassword) {
      setStatus({ type: 'error', msg: 'Please enter your current password to proceed.' });
      return;
    }
    if (!newUsername && !newPassword) {
      setStatus({ type: 'error', msg: 'Please enter a new username or a new password.' });
      return;
    }

    setLoading(true);
    setStatus(null);

    try {
      await updateCredentials(currentPassword, newUsername, newPassword);
      setStatus({ type: 'success', msg: 'Credentials updated successfully!' });
      setCurrentPassword('');
      setNewUsername('');
      setNewPassword('');
    } catch (err) {
      setStatus({ type: 'error', msg: err instanceof Error ? err.message : 'Update failed.' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="settings-container" style={{ display: 'flex', justifyContent: 'center', padding: 'var(--space-6) 0' }}>
      <div className="card" style={{ width: '100%', maxWidth: '480px' }}>
        <div className="card-header">
          <h3 className="card-title">Security Settings</h3>
          <p className="card-subtitle">Update your username and password.</p>
        </div>

        <form onSubmit={handleSubmit} style={{ padding: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }} autoComplete="off">
          <div className="field-grid">
            <label>
              Current Password
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                placeholder="Your current password"
                disabled={loading}
                autoComplete="current-password"
                name="current-password"
              />
            </label>
          </div>

          <div className="field-grid">
            <label>
              New Username
              <input
                type="text"
                value={newUsername}
                onChange={(e) => setNewUsername(e.target.value)}
                placeholder="Leave blank to keep current"
                disabled={loading}
                autoComplete="off"
                name="new-username"
                data-lpignore="true"
              />
            </label>
            <label>
              New Password
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                placeholder="Leave blank to keep current"
                disabled={loading}
                autoComplete="new-password"
                name="new-password"
              />
            </label>
          </div>

          {status && (
            <div className={`login-error`} style={{
              background: status.type === 'success' ? 'rgba(72, 255, 122, 0.1)' : undefined,
              borderColor: status.type === 'success' ? 'rgba(72, 255, 122, 0.25)' : undefined,
              color: status.type === 'success' ? 'var(--green)' : undefined,
              margin: '0',
            }}>
              {status.msg}
            </div>
          )}

          <div className="submit-actions" style={{ marginTop: 'var(--space-2)' }}>
            <button type="submit" className="primary-button full" disabled={loading}>
              {loading ? 'Updating...' : 'Update'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
