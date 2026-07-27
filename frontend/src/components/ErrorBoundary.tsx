import React from 'react';

type State = { error: Error | null };

export class ErrorBoundary extends React.Component<React.PropsWithChildren, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('Uncaught render error', error, info);
  }

  render(): React.ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <main className="login-shell">
        <section className="login-card" role="alert">
          <h1 className="text-lg font-semibold text-zinc-50">Something went wrong</h1>
          <p className="text-sm text-zinc-400">
            The interface could not be rendered. Your queued jobs are still safe on the server.
          </p>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            Reload application
          </button>
        </section>
      </main>
    );
  }
}
