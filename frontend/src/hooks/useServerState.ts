import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';

import {
  getStreamTicket,
  getWorkerHealth,
  listJobs,
  listBatches,
  listMediaRoots,
  listOutputs,
} from '../api';
import type { JobFilters, JobRecord, JobStreamPayload } from '../models';
import { pollMs } from '../utils/constants';

export const serverKeys = {
  health: ['server', 'health'] as const,
  jobs: ['server', 'jobs'] as const,
  outputs: ['server', 'outputs'] as const,
  roots: ['server', 'media-roots'] as const,
  batches: ['server', 'batches'] as const,
};

export function useServerState(enabled: boolean, filters: JobFilters) {
  const queryClient = useQueryClient();
  const [streamState, setStreamState] = useState<'connecting' | 'live' | 'fallback'>('connecting');
  const [lastSync, setLastSync] = useState<string | null>(null);
  const invalidateTimer = useRef<number | null>(null);
  const activeFilters = Boolean(
    filters.q ||
    filters.status !== 'all' ||
    filters.profile ||
    filters.sourceType !== 'all' ||
    filters.includeArchived,
  );

  const rootsQuery = useQuery({
    queryKey: serverKeys.roots,
    queryFn: listMediaRoots,
    enabled,
  });

  const jobsQuery = useInfiniteQuery({
    queryKey: [...serverKeys.jobs, filters],
    queryFn: ({ pageParam }) => listJobs(filters, 250, pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (page) => page.nextCursor ?? undefined,
    enabled,
    refetchInterval: streamState === 'live' ? false : pollMs,
  });

  const allJobsQuery = useQuery({
    queryKey: [...serverKeys.jobs, 'all'],
    queryFn: () => listJobs({ status: 'all' }, 250),
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
    // /worker/health already reports API liveness, Redis state and the worker
    // heartbeat; the separate live/ready calls tripled the polling cost.
    queryFn: getWorkerHealth,
    enabled,
    refetchInterval: 5_000,
  });

  useEffect(() => {
    if (!enabled) return;

    let stream: EventSource | null = null;
    let retryTimer: number | null = null;
    let disposed = false;

    const upsertJob = (job: JobRecord) => {
      queryClient.setQueriesData<unknown>({ queryKey: serverKeys.jobs }, (data: unknown) => {
        if (!data || typeof data !== 'object') return data;
        const cached = data as {
          pages?: Array<{ jobs: JobRecord[]; nextCursor: string | null }>;
          jobs?: JobRecord[];
        };
        if (Array.isArray(cached.pages)) {
          let found = false;
          const pages = cached.pages.map((page) => {
            const index = page.jobs.findIndex((item) => item.id === job.id);
            if (index === -1) return page;
            found = true;
            const jobs = page.jobs.slice();
            jobs[index] = { ...page.jobs[index], ...job };
            return { ...page, jobs };
          });
          return found ? { ...cached, pages } : cached;
        }
        if (Array.isArray(cached.jobs)) {
          const index = cached.jobs.findIndex((item) => item.id === job.id);
          if (index === -1) return cached;
          const jobs = cached.jobs.slice();
          jobs[index] = { ...cached.jobs[index], ...job };
          return { ...cached, jobs };
        }
        return cached;
      });
    };

    const removeJob = (jobId: string) => {
      queryClient.setQueriesData<unknown>({ queryKey: serverKeys.jobs }, (data: unknown) => {
        if (!data || typeof data !== 'object') return data;
        const cached = data as {
          pages?: Array<{ jobs: JobRecord[]; nextCursor: string | null }>;
          jobs?: JobRecord[];
        };
        if (Array.isArray(cached.pages)) {
          return {
            ...cached,
            pages: cached.pages.map((page) => ({
              ...page,
              jobs: page.jobs.filter((item) => item.id !== jobId),
            })),
          };
        }
        if (Array.isArray(cached.jobs)) {
          return { ...cached, jobs: cached.jobs.filter((item) => item.id !== jobId) };
        }
        return cached;
      });
    };

    const invalidate = (timestamp?: string) => {
      if (timestamp) setLastSync(timestamp);
      if (invalidateTimer.current !== null) return;
      invalidateTimer.current = window.setTimeout(() => {
        invalidateTimer.current = null;
        void queryClient.invalidateQueries({ queryKey: serverKeys.jobs });
        void queryClient.invalidateQueries({ queryKey: serverKeys.outputs });
        void queryClient.invalidateQueries({ queryKey: serverKeys.batches });
      }, 300);
    };

    const invalidateDerived = (timestamp?: string) => {
      if (timestamp) setLastSync(timestamp);
      if (invalidateTimer.current !== null) return;
      // Only terminal transitions can change outputs/batches; progress ticks
      // are applied in place and must not trigger a full refetch.
      invalidateTimer.current = window.setTimeout(() => {
        invalidateTimer.current = null;
        void queryClient.invalidateQueries({ queryKey: serverKeys.outputs });
        void queryClient.invalidateQueries({ queryKey: serverKeys.batches });
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
        if (payload.event === 'job_updated' && payload.data?.job) {
          const job = payload.data.job;
          upsertJob(job);
          if (['completed', 'failed', 'cancelled', 'done'].includes(job.status)) {
            invalidateDerived(payload.timestamp);
          } else if (payload.timestamp) {
            setLastSync(payload.timestamp);
          }
          return;
        }
        if (payload.event === 'job_deleted' && payload.data?.job_id) {
          removeJob(payload.data.job_id);
          invalidateDerived(payload.timestamp);
          return;
        }
        // Snapshots and heartbeats carry no per-job delta: refresh wholesale.
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

  const refreshAll = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: serverKeys.jobs }),
      queryClient.invalidateQueries({ queryKey: serverKeys.outputs }),
      queryClient.invalidateQueries({ queryKey: serverKeys.health }),
      queryClient.invalidateQueries({ queryKey: serverKeys.roots }),
      queryClient.invalidateQueries({ queryKey: serverKeys.batches }),
    ]);
    setLastSync(new Date().toISOString());
  }, [queryClient]);

  const jobs = useMemo(
    () => jobsQuery.data?.pages.flatMap((page) => page.jobs) ?? [],
    [jobsQuery.data],
  );
  const outputs = useMemo(
    () => outputsQuery.data?.pages.flatMap((page) => page.outputs) ?? [],
    [outputsQuery.data],
  );
  const roots = useMemo(() => rootsQuery.data ?? [], [rootsQuery.data]);
  const batches = useMemo(() => batchesQuery.data?.batches ?? [], [batchesQuery.data]);
  const workerHealth = healthQuery.data ?? null;

  return useMemo(
    () => ({
      roots,
      jobs,
      allJobs: activeFilters ? (allJobsQuery.data?.jobs ?? []) : jobs,
      outputs,
      batches,
      workerHealth,
      apiHealthy: workerHealth?.status === 'ok',
      redisHealthy: workerHealth?.redis === 'ok',
      jobsLoading: jobsQuery.isLoading,
      jobsRefreshing: jobsQuery.isFetching,
      streamState,
      lastSync,
      refreshAll,
      fetchNextJobs: jobsQuery.fetchNextPage,
      hasNextJobs: jobsQuery.hasNextPage,
      fetchNextOutputs: outputsQuery.fetchNextPage,
      hasNextOutputs: outputsQuery.hasNextPage,
    }),
    [
      roots,
      jobs,
      activeFilters,
      allJobsQuery.data,
      outputs,
      batches,
      workerHealth,
      jobsQuery.isLoading,
      jobsQuery.isFetching,
      jobsQuery.fetchNextPage,
      jobsQuery.hasNextPage,
      outputsQuery.fetchNextPage,
      outputsQuery.hasNextPage,
      streamState,
      lastSync,
      refreshAll,
    ],
  );
}
