import React, { useEffect, useState } from 'react';
import { authLogin, setAuthToken, getSetupStatus, completeSetup } from '../api';
import { Video } from 'lucide-react';

type Mode = 'loading' | 'login' | 'setup';

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [mode, setMode] = useState<Mode>('loading');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSetupStatus()
      .then((needsSetup) => {
        if (!cancelled) setMode(needsSetup ? 'setup' : 'login');
      })
      .catch(() => {
        if (!cancelled) setMode('login');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError('Username and password are required');
      return;
    }

    if (mode === 'setup') {
      if (username.trim().length < 3) {
        setError('Username must be at least 3 characters');
        return;
      }
      if (password.length < 8) {
        setError('Password must be at least 8 characters');
        return;
      }
      if (password !== confirmPassword) {
        setError('Passwords do not match');
        return;
      }
    }

    setLoading(true);
    setError('');

    try {
      const token =
        mode === 'setup'
          ? await completeSetup(username.trim(), password)
          : await authLogin(username, password);
      setAuthToken(token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  const isSetup = mode === 'setup';

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <div className="login-header-group">
          <div className="brand-icon" style={{ padding: '0.75rem', marginBottom: '0.5rem' }}>
            <Video size={24} />
          </div>
          <h2 className="text-lg font-semibold text-zinc-100" style={{ margin: 0 }}>
            Video Converter
          </h2>
          <p className="text-xs text-zinc-400" style={{ margin: 0 }}>
            {isSetup ? 'First-time setup — create your admin account' : 'Secure Access'}
          </p>
        </div>

        {mode === 'loading' ? (
          <p className="text-xs text-zinc-500" style={{ textAlign: 'center', padding: '1rem 0' }}>
            Loading…
          </p>
        ) : (
          <form
            onSubmit={handleSubmit}
            style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}
          >
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Username"
                autoComplete="username"
                autoFocus
                disabled={loading}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Password</label>
              <input
                type="password"
                className="form-input"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={isSetup ? 'At least 8 characters' : 'Password'}
                autoComplete={isSetup ? 'new-password' : 'current-password'}
                disabled={loading}
              />
            </div>

            {isSetup && (
              <div className="form-group">
                <label className="form-label">Confirm Password</label>
                <input
                  type="password"
                  className="form-input"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Repeat the password"
                  autoComplete="new-password"
                  disabled={loading}
                />
              </div>
            )}

            {error && (
              <div
                className="text-xs text-rose-400 p-2 border border-rose-900 rounded bg-rose-950/30"
                role="alert"
              >
                {error}
              </div>
            )}

            <div
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '0.75rem',
                marginTop: '0.5rem',
              }}
            >
              <button
                type="submit"
                className="btn btn-primary w-full justify-center"
                disabled={loading}
                style={{ padding: '0.5rem' }}
              >
                {loading
                  ? isSetup
                    ? 'Creating account…'
                    : 'Authenticating…'
                  : isSetup
                    ? 'Create Admin Account'
                    : 'Sign In'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
