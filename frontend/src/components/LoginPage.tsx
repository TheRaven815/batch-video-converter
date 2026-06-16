import React, { useState } from 'react';
import { authLogin, setAuthToken, requestPasswordReset } from '../api';
import { Video } from 'lucide-react';

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [loading, setLoading] = useState(false);

  const handleForgotPassword = async () => {
    try {
      setLoading(true);
      setError('');
      setInfo('');
      await requestPasswordReset();
      setInfo('Temporary password generated and printed to server logs. Please check the logs.');
    } catch (err) {
      setError('Password reset request failed.');
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username || !password) {
      setError('Username and password are required');
      return;
    }

    setLoading(true);
    setError('');
    setInfo('');

    try {
      const token = await authLogin(username, password);
      setAuthToken(token);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid credentials');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-wrapper">
      <div className="login-card">
        <div className="login-header-group">
          <div className="brand-icon" style={{ padding: '0.75rem', marginBottom: '0.5rem' }}>
            <Video size={24} />
          </div>
          <h2 className="text-lg font-semibold text-zinc-100" style={{ margin: 0 }}>Video Converter</h2>
          <p className="text-xs text-zinc-400" style={{ margin: 0 }}>Secure Access</p>
        </div>
        
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div className="form-group">
            <label className="form-label">Username</label>
            <input
              type="text"
              className="form-input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter APP_USERNAME"
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
              placeholder="Enter APP_PASSWORD"
              autoFocus
              disabled={loading}
            />
          </div>
          
          {error && <div className="text-xs text-rose-400 p-2 border border-rose-900 rounded bg-rose-950/30" role="alert">{error}</div>}
          {info && <div className="text-xs text-emerald-400 p-2 border border-emerald-900 rounded bg-emerald-950/30" role="alert">{info}</div>}
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.5rem' }}>
            <button 
              type="submit" 
              className="btn btn-primary w-full justify-center"
              disabled={loading}
              style={{ padding: '0.5rem' }}
            >
              {loading ? 'Authenticating...' : 'Sign In'}
            </button>
            <button 
              type="button" 
              className="btn btn-outline w-full justify-center" 
              onClick={handleForgotPassword}
              disabled={loading}
              style={{ padding: '0.5rem', border: 'none', backgroundColor: 'transparent' }}
            >
              Forgot Password?
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
