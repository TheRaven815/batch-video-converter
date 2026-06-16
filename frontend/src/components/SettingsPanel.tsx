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
    <div>
      <div className="border-b border-zinc-800 pb-2 mb-4" style={{ marginBottom: '1rem', paddingBottom: '0.5rem' }}>
        <h3 className="text-sm font-semibold text-zinc-100" style={{ margin: 0 }}>Security Settings</h3>
        <p className="text-xs text-zinc-400 mt-0.5" style={{ margin: '0.125rem 0 0 0' }}>Update your username and password.</p>
      </div>

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

        {status && (
          <div className={`text-xs p-2 border rounded ${status.type === 'success' ? 'text-emerald-400 border-emerald-900 bg-emerald-950/30' : 'text-rose-400 border-rose-900 bg-rose-950/30'}`} style={{ maxWidth: '32rem' }}>
            {status.msg}
          </div>
        )}

        <div className="pt-2" style={{ paddingTop: '0.5rem' }}>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? 'Updating...' : 'Update Credentials'}
          </button>
        </div>
      </form>
    </div>
  );
}
