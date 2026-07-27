import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot } from 'react-dom/client';
import { Toaster } from 'sonner';
import { Router } from 'wouter';
import { useHashLocation } from 'wouter/use-hash-location';

import { App } from './App';
import { ErrorBoundary } from './components/ErrorBoundary';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5_000,
      refetchOnWindowFocus: true,
    },
  },
});

const root = document.getElementById('root');
if (!root) throw new Error('Application root element was not found');

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/ui/sw.js');
  });
}

createRoot(root).render(
  <React.StrictMode>
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <Router hook={useHashLocation}>
          <App />
          <Toaster theme="dark" richColors closeButton position="top-right" />
        </Router>
      </QueryClientProvider>
    </ErrorBoundary>
  </React.StrictMode>,
);
