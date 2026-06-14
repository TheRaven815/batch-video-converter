import React, { useState } from 'react';
import { authLogin, setAuthToken, requestPasswordReset } from '../api';

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
    <div className="login-container">
      <div className="login-card card">
        <div className="login-header">
          <div className="login-logo">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polygon points="5 3 19 12 5 21 5 3"></polygon>
            </svg>
          </div>
          <h2>Video Converter</h2>
          <p className="eyebrow">Secure Access</p>
        </div>
        
        <form onSubmit={handleSubmit} className="login-form">
          <div className="field-grid">
            <label>
              Username
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Enter APP_USERNAME"
                disabled={loading}
              />
            </label>
            <label>
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Enter APP_PASSWORD"
                autoFocus
                disabled={loading}
              />
            </label>
          </div>
          
          {error && <div className="login-error" role="alert">{error}</div>}
          {info && <div className="login-error" style={{ background: 'rgba(72, 255, 122, 0.1)', borderColor: 'rgba(72, 255, 122, 0.25)', color: 'var(--green)' }} role="alert">{info}</div>}
          
          <div className="submit-actions" style={{ display: 'flex', flexDirection: 'column', gap: '1rem', alignItems: 'center' }}>
            <button 
              type="submit" 
              className={`primary-button glow full ${loading ? 'loading' : ''}`}
              disabled={loading}
            >
              {loading ? 'Authenticating...' : 'Sign In'}
            </button>
            <button 
              type="button" 
              className="text-button" 
              onClick={handleForgotPassword}
              disabled={loading}
            >
              Forgot Password?
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
