import type {
  BatchListResponse,
  HealthResponse,
  JobBatchCreateResponse,
  JobBulkActionResponse,
  JobCreateRequest,
  JobFilters,
  JobRecord,
  JobValidationResponse,
  MediaBrowseResponse,
  MediaRootDto,
  MediaSubtitleProbeResponse,
  OutputListResponse,
  StructuredErrorResponse,
  WorkerHealthResponse,
  SystemSettings,
} from './models';

let authToken: string | null = localStorage.getItem('video-converter-auth-token');

export function setAuthToken(token: string | null) {
  authToken = token;
  if (token) {
    localStorage.setItem('video-converter-auth-token', token);
  } else {
    localStorage.removeItem('video-converter-auth-token');
  }
}

export function getAuthToken() {
  return authToken;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  if (authToken) {
    headers.set('Authorization', `Bearer ${authToken}`);
  }

  const response = await fetch(path, {
    ...init,
    headers,
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    try {
      const parsed = JSON.parse(text) as StructuredErrorResponse;
      if (parsed.error?.message) throw new Error(`${parsed.error.code}: ${parsed.error.message}`);
    } catch (error) {
      if (error instanceof Error && error.message.includes(':')) throw error;
    }
    throw new Error(`${init?.method || 'GET'} ${path} failed with ${response.status}${text ? `: ${text}` : ''}`);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export async function getLiveHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health/live');
}

export async function getReadyHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health/ready');
}

export async function getWorkerHealth(): Promise<WorkerHealthResponse> {
  return request<WorkerHealthResponse>('/api/v1/worker/health');
}

export async function listMediaRoots(): Promise<MediaRootDto[]> {
  return request<MediaRootDto[]>('/api/v1/media/roots');
}

export async function browseMedia(rootKey: string, path = '', q = ''): Promise<MediaBrowseResponse> {
  const params = new URLSearchParams({ root_key: rootKey, path });
  if (q.trim()) params.set('q', q.trim());
  return request<MediaBrowseResponse>(`/api/v1/media/browse?${params.toString()}`);
}

export async function probeSubtitles(rootKey: string, path: string): Promise<MediaSubtitleProbeResponse> {
  const params = new URLSearchParams({ root_key: rootKey, path });
  return request<MediaSubtitleProbeResponse>(`/api/v1/media/subtitles?${params.toString()}`);
}

export async function listJobs(filters: Partial<JobFilters>, limit = 250, cursor?: string | null): Promise<{ jobs: JobRecord[]; nextCursor: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.q?.trim()) params.set('q', filters.q.trim());
  if (filters.profile?.trim()) params.set('profile', filters.profile.trim());
  if (filters.sourceType && filters.sourceType !== 'all') params.set('source_type', filters.sourceType);
  
  const headers = new Headers();
  headers.set('Accept', 'application/json');
  if (authToken) {
    headers.set('Authorization', `Bearer ${authToken}`);
  }
  
  const response = await fetch(`/api/v1/jobs?${params.toString()}`, { headers });
  if (!response.ok) {
    if (response.status === 401) {
       setAuthToken(null);
       window.location.reload();
    }
    throw new Error(`GET /api/v1/jobs failed with ${response.status}`);
  }
  return { jobs: (await response.json()) as JobRecord[], nextCursor: response.headers.get('X-Next-Cursor') };
}

export async function validateJobs(jobs: JobCreateRequest[]): Promise<JobValidationResponse> {
  return request<JobValidationResponse>('/api/v1/jobs/validate', {
    method: 'POST',
    body: JSON.stringify({ jobs }),
  });
}

export async function createJobsBatch(jobs: JobCreateRequest[]): Promise<JobBatchCreateResponse> {
  const idempotencyKey = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}`;
  return request<JobBatchCreateResponse>('/api/v1/jobs/batch', {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ jobs }),
  });
}

export async function cancelJob(jobId: string): Promise<JobRecord> {
  return request<JobRecord>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' });
}

export async function bulkCancel(jobIds: string[]): Promise<JobBulkActionResponse> {
  return request<JobBulkActionResponse>('/api/v1/jobs/bulk/cancel', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function bulkStart(jobIds: string[]): Promise<JobBulkActionResponse> {
  return request<JobBulkActionResponse>('/api/v1/jobs/bulk/start', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function listBatches(limit = 50): Promise<BatchListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<BatchListResponse>(`/api/v1/batches?${params.toString()}`);
}

export async function bulkArchive(jobIds: string[]): Promise<JobBulkActionResponse> {
  return request<JobBulkActionResponse>('/api/v1/jobs/bulk/archive', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function bulkDelete(jobIds: string[]): Promise<JobBulkActionResponse> {
  return request<JobBulkActionResponse>('/api/v1/jobs/bulk/delete', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  });
}

export async function listOutputs(limit = 50): Promise<OutputListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  return request<OutputListResponse>(`/api/v1/outputs?${params.toString()}`);
}

export async function clearOutputs(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>('/api/v1/outputs', { method: 'DELETE' });
}

export async function authLogin(username: string, password: string): Promise<string> {
  const params = new URLSearchParams();
  params.append('username', username);
  params.append('password', password);

  const response = await fetch('/api/v1/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: params.toString(),
  });

  if (!response.ok) {
    throw new Error('Invalid username or password');
  }

  const data = await response.json();
  return data.access_token;
}

export async function requestPasswordReset(): Promise<void> {
  const response = await fetch('/api/v1/auth/forgot-password', {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error('Failed to request password reset');
  }
}

export async function updateCredentials(currentPassword: string, newUsername?: string, newPassword?: string): Promise<void> {
  const body: Record<string, string> = { current_password: currentPassword };
  if (newUsername) body.new_username = newUsername;
  if (newPassword) body.new_password = newPassword;

  await request<any>('/api/v1/auth/credentials', {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export async function getSystemSettings(): Promise<SystemSettings> {
  return request<SystemSettings>('/api/v1/settings');
}

export async function updateSystemSettings(settings: SystemSettings): Promise<SystemSettings> {
  return request<SystemSettings>('/api/v1/settings', {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}
