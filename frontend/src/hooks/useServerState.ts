import { useEffect, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getLiveHealth,
  getReadyHealth,
  getStreamTicket,
  getWorkerHealth,
  listJobs,
  listBatches,
  listMediaRoots,
  listOutputs,
} from '../api';
import type { JobStreamPayload } from '../models';
import { pollMs } from '../utils/constants';

export const serverKeys = {
  health: ['server', 'health'] as const,
  jobs: ['server', 'jobs'] as const,
  outputs: ['server', 'outputs'] as const,
  roots: ['server', 'media-roots'] as const,
  batches: ['server', 'batches'] as const,
};

export function useServerState(enabled: boolean) {
  const queryClient = useQueryClient();
  const [streamState, setStreamState] = useState<'connecting' | 'live' | 'fallback'>('connecting');
  const [lastSync, setLastSync] = useState<string | null>(null);
  const invalidateTimer = useRef<number | null>(null);

  const rootsQuery = useQuery({
    queryKey: serverKeys.roots,
    queryFn: listMediaRoots,
    enabled,
  });

  const jobsQuery = useInfiniteQuery({
    queryKey: serverKeys.jobs,
    queryFn: ({ pageParam }) => listJobs({ status: 'all' }, 250, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled,
    refetchInterval: streamState === 'live' ? false : pollMs,
  });

  const outputsQuery = useInfiniteQuery({
    queryKey: serverKeys.outputs,
    queryFn: ({ pageParam }) => listOutputs(50, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled,
    refetchInterval: streamState === 'live' ? false : pollMs,
  });
  const batchesQuery = useQuery({
    queryKey: serverKeys.batches,
    queryFn: () => listBatches(50),
    enabled,
    refetchInterval: streamState === 'live' ? false : pollMs,
  });

  const healthQuery = useQuery({
    queryKey: serverKeys.health,
    queryFn: async () => {
      const [live, ready, worker] = await Promise.all([
        getLiveHealth(),
        getReadyHealth(),
        getWorkerHealth(),
      ]);
      return { live, ready, worker };
    },
    enabled,
    refetchInterval: 5_000,
  });

  useEffect(() => {
    if (!enabled) return;

    let stream: EventSource | null = null;
    let retryTimer: number | null = null;
    let disposed = false;

    const invalidate = (timestamp?: string) => {
      if (timestamp) setLastSync(timestamp);
      if (invalidateTimer.current !== null) return;
      invalidateTimer.current = window.setTimeout(() => {
        invalidateTimer.current = null;
        void queryClient.invalidateQueries({ queryKey: serverKeys.jobs });
        void queryClient.invalidateQueries({ queryKey: serverKeys.outputs });
      }, 300);
    };

    const fallBack = () => {
      stream?.close();
      stream = null;
      setStreamState('fallback');
      if (!disposed && retryTimer === null) {
        retryTimer = window.setTimeout(() => {
          retryTimer = null;
          void openStream();
        }, 30_000);
      }
    };

    const handleEvent = (event: Event) => {
      try {
        const payload = JSON.parse((event as MessageEvent<string>).data) as JobStreamPayload;
        invalidate(payload.timestamp);
      } catch {
        fallBack();
      }
    };

    const openStream = async () => {
      if (disposed || typeof EventSource === 'undefined') {
        fallBack();
        return;
      }
      setStreamState('connecting');
      try {
        const ticket = await getStreamTicket();
        if (disposed) return;
        stream = new EventSource(`/api/v1/jobs/stream?ticket=${encodeURIComponent(ticket)}`);
        stream.onopen = () => setStreamState('live');
        stream.onerror = fallBack;
        stream.addEventListener('jobs_snapshot', handleEvent);
        stream.addEventListener('job_updated', handleEvent);
        stream.addEventListener('job_deleted', handleEvent);
      } catch {
        fallBack();
      }
    };

    void openStream();
    return () => {
      disposed = true;
      stream?.close();
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      if (invalidateTimer.current !== null) window.clearTimeout(invalidateTimer.current);
      invalidateTimer.current = null;
    };
  }, [enabled, queryClient]);

  const refreshAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: serverKeys.jobs }),
      queryClient.invalidateQueries({ queryKey: serverKeys.outputs }),
      queryClient.invalidateQueries({ queryKey: serverKeys.health }),
      queryClient.invalidateQueries({ queryKey: serverKeys.roots }),
      queryClient.invalidateQueries({ queryKey: serverKeys.batches }),
    ]);
    setLastSync(new Date().toISOString());
  };

  return {
    roots: rootsQuery.data ?? [],
    jobs: jobsQuery.data?.pages.flatMap((page) => page.jobs) ?? [],
    outputs: outputsQuery.data?.pages.flatMap((page) => page.outputs) ?? [],
    batches: batchesQuery.data?.batches ?? [],
    workerHealth: healthQuery.data?.worker ?? null,
    apiHealthy: healthQuery.data?.live.status === 'ok',
    redisHealthy: healthQuery.data?.ready.redis === 'ok',
    jobsLoading: jobsQuery.isLoading,
    jobsRefreshing: jobsQuery.isFetching,
    streamState,
    lastSync,
    refreshAll,
    fetchNextJobs: jobsQuery.fetchNextPage,
    hasNextJobs: jobsQuery.hasNextPage,
    fetchNextOutputs: outputsQuery.fetchNextPage,
    hasNextOutputs: outputsQuery.hasNextPage,
  };
}
