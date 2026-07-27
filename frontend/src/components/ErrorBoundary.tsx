import React from 'react';
import { useI18n } from '../i18n';

type State = { error: Error | null };

function ErrorFallback() {
  const { t } = useI18n();
  return (
    <main className="login-shell">
      <section className="login-card" role="alert">
        <h1 className="text-lg font-semibold text-zinc-50">{t('errorBoundary.title')}</h1>
        <p className="text-sm text-zinc-400">{t('errorBoundary.body')}</p>
        <button className="btn btn-primary" onClick={() => window.location.reload()}>
          {t('errorBoundary.reload')}
        </button>
      </section>
    </main>
  );
}

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
    return <ErrorFallback />;
  }
}
