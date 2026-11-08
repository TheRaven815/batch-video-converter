import type {
  BatchListResponse,
  BatchActionResponse,
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
  Mp4FixRequest,
  Mp4FixResponse,
  OutputListResponse,
  StructuredErrorResponse,
  WorkerHealthResponse,
  SystemSettings,
  UploadResponse,
} from './models';
import { loadLanguage, translate } from './i18n';

let authToken: string | null = localStorage.getItem('video-converter-auth-token');
let streamTicketCache: { ticket: string; expiresAt: number } | null = null;
let streamTicketRequest: Promise<string> | null = null;

export function setAuthToken(token: string | null) {
  authToken = token;
  streamTicketCache = null;
  streamTicketRequest = null;
  if (token) {
    localStorage.setItem('video-converter-auth-token', token);
  } else {
    localStorage.removeItem('video-converter-auth-token');
  }
}

export function getAuthToken() {
  return authToken;
}

function extractErrorMessage(text: string): string | null {
  // The API returns {"error": {"message": ...}}; validation errors use
  // {"detail": ...}. Anything else (e.g. an HTML error page) yields null so
  // the caller can build a readable fallback instead of a JSON SyntaxError.
  try {
    const parsed = JSON.parse(text) as StructuredErrorResponse & { detail?: unknown };
    if (parsed.error?.message) return parsed.error.message;
    if (typeof parsed.detail === 'string') return parsed.detail;
  } catch {
    // not JSON
  }
  return null;
}

function extractErrorCode(text: string): string | null {
  try {
    const parsed = JSON.parse(text) as StructuredErrorResponse;
    return parsed.error?.code ?? null;
  } catch {
    return null;
  }
}

async function requestWithHeaders<T>(
  path: string,
  init?: RequestInit,
): Promise<{ data: T; headers: Headers }> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
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
    const message = extractErrorMessage(text);
    const code = extractErrorCode(text);

    // Expired/revoked session: drop the token and return to the login page.
    // The same applies when the backend has no credentials at all (for
    // example after a fresh Redis volume): trusting a stale browser token
    // would otherwise leave the app shell open with every request failing.
    if (authToken && (response.status === 401 || code === 'auth_not_configured')) {
      setAuthToken(null);
      window.location.reload();
    }

    throw new Error(
      message ||
        translate(loadLanguage(), 'api.requestFailed', {
          method: init?.method || 'GET',
          status: response.status,
        }),
    );
  }

  if (response.status === 204) return { data: undefined as T, headers: response.headers };
  return { data: (await response.json()) as T, headers: response.headers };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { data } = await requestWithHeaders<T>(path, init);
  return data;
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

export async function browseMedia(
  rootKey: string,
  path = '',
  q = '',
): Promise<MediaBrowseResponse> {
  const params = new URLSearchParams({ root_key: rootKey, path });
  if (q.trim()) params.set('q', q.trim());
  return request<MediaBrowseResponse>(`/api/v1/media/browse?${params.toString()}`);
}

export async function probeSubtitles(
  rootKey: string,
  path: string,
): Promise<MediaSubtitleProbeResponse> {
  const params = new URLSearchParams({ root_key: rootKey, path });
  return request<MediaSubtitleProbeResponse>(`/api/v1/media/subtitles?${params.toString()}`);
}

export async function listJobs(
  filters: Partial<JobFilters>,
  limit = 250,
  cursor?: string | null,
): Promise<{ jobs: JobRecord[]; nextCursor: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  if (filters.status && filters.status !== 'all') params.set('status', filters.status);
  if (filters.q?.trim()) params.set('q', filters.q.trim());
  if (filters.profile?.trim()) params.set('profile', filters.profile.trim());
  if (filters.sourceType && filters.sourceType !== 'all')
    params.set('source_type', filters.sourceType);
  if (filters.includeArchived) params.set('include_archived', 'true');

  const { data, headers } = await requestWithHeaders<JobRecord[]>(
    `/api/v1/jobs?${params.toString()}`,
  );
  return {
    jobs: data,
    nextCursor: headers.get('X-Next-Cursor'),
  };
}

export async function validateJobs(jobs: JobCreateRequest[]): Promise<JobValidationResponse> {
  return request<JobValidationResponse>('/api/v1/jobs/validate', {
    method: 'POST',
    body: JSON.stringify({ jobs }),
  });
}

export async function createJobsBatch(jobs: JobCreateRequest[]): Promise<JobBatchCreateResponse> {
  const idempotencyKey =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `${Date.now()}`;
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

export async function actOnBatch(
  batchId: string,
  action: 'cancel' | 'retry' | 'archive' | 'delete',
): Promise<BatchActionResponse> {
  return request<BatchActionResponse>(`/api/v1/batches/${encodeURIComponent(batchId)}/${action}`, {
    method: 'POST',
  });
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

export async function listOutputs(limit = 50, cursor?: string | null): Promise<OutputListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  return request<OutputListResponse>(`/api/v1/outputs?${params.toString()}`);
}

export async function clearOutputs(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>('/api/v1/outputs', { method: 'DELETE' });
}

export async function deleteOutput(filename: string): Promise<void> {
  await request<{ deleted: string }>(`/api/v1/outputs/${encodeURIComponent(filename)}`, {
    method: 'DELETE',
  });
}

export async function uploadMedia(
  file: File,
  options?: {
    onProgress?: (percent: number) => void;
    signal?: AbortSignal;
  },
): Promise<UploadResponse> {
  return new Promise((resolve, reject) => {
    if (options?.signal?.aborted) {
      reject(new DOMException('Upload aborted', 'AbortError'));
      return;
    }

    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/v1/media/uploads');
    xhr.setRequestHeader('Accept', 'application/json');
    if (authToken) xhr.setRequestHeader('Authorization', `Bearer ${authToken}`);

    const onAbort = () => xhr.abort();
    options?.signal?.addEventListener('abort', onAbort, { once: true });
    const finish = () => options?.signal?.removeEventListener('abort', onAbort);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        options?.onProgress?.(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onerror = () => {
      finish();
      reject(new Error(translate(loadLanguage(), 'convert.uploadFailed')));
    };
    xhr.onabort = () => {
      finish();
      reject(new DOMException('Upload aborted', 'AbortError'));
    };
    xhr.onload = () => {
      finish();
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as UploadResponse);
        } catch {
          reject(new Error(translate(loadLanguage(), 'convert.uploadFailed')));
        }
        return;
      }
      if (xhr.status === 401 && authToken) {
        setAuthToken(null);
        window.location.reload();
      }
      reject(
        new Error(
          extractErrorMessage(xhr.responseText) ??
            translate(loadLanguage(), 'api.requestFailed', {
              method: 'POST',
              status: xhr.status,
            }),
        ),
      );
    };

    const form = new FormData();
    form.append('file', file);
    xhr.send(form);
  });
}

export async function getProtectedMediaUrl(path: string): Promise<string> {
  const ticket = await getStreamTicket();
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}ticket=${encodeURIComponent(ticket)}`;
}

export async function downloadJobLog(jobId: string): Promise<void> {
  const headers = new Headers();
  if (authToken) headers.set('Authorization', `Bearer ${authToken}`);
  const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}/log`, { headers });
  if (!response.ok)
    throw new Error(
      translate(loadLanguage(), 'api.logDownloadFailed', { status: response.status }),
    );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${jobId}.ffmpeg.log`;
  link.click();
  URL.revokeObjectURL(url);
}

export async function downloadOutput(filename: string): Promise<void> {
  // A plain <a href> cannot send the Authorization header, so fetch the file
  // with auth and hand it to the browser as a blob download.
  const headers = new Headers();
  if (authToken) {
    headers.set('Authorization', `Bearer ${authToken}`);
  }
  const response = await fetch(`/api/v1/outputs/${encodeURIComponent(filename)}/download`, {
    headers,
  });
  if (!response.ok) {
    throw new Error(translate(loadLanguage(), 'api.downloadFailed', { status: response.status }));
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export async function getSetupStatus(): Promise<boolean> {
  const data = await request<{ needs_setup: boolean }>('/api/v1/auth/setup-status');
  return data.needs_setup;
}

export async function completeSetup(username: string, password: string): Promise<string> {
  const data = await request<{ access_token: string }>('/api/v1/auth/setup', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  return data.access_token;
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
    const text = await response.text().catch(() => '');
    throw new Error(
      extractErrorMessage(text) || translate(loadLanguage(), 'auth.invalidCredentials'),
    );
  }

  const data = await response.json();
  return data.access_token;
}

export async function getStreamTicket(): Promise<string> {
  // EventSource cannot send an Authorization header, so media and SSE use a
  // scoped ticket. Cache it for its lifetime: one dashboard render used to
  // fire a POST per thumbnail, and a 60s ticket broke video seeking.
  if (streamTicketCache && streamTicketCache.expiresAt > Date.now()) {
    return streamTicketCache.ticket;
  }
  if (streamTicketRequest) return streamTicketRequest;
  streamTicketRequest = (async () => {
    const data = await request<{ ticket: string; expires_in: number }>(
      '/api/v1/auth/stream-ticket',
      { method: 'POST' },
    );
    const ttlMs = Math.max(30, data.expires_in - 30) * 1000;
    streamTicketCache = { ticket: data.ticket, expiresAt: Date.now() + ttlMs };
    return data.ticket;
  })().finally(() => {
    streamTicketRequest = null;
  });
  return streamTicketRequest;
}

export async function updateCredentials(
  currentPassword: string,
  newUsername?: string,
  newPassword?: string,
): Promise<void> {
  const body: Record<string, string> = { current_password: currentPassword };
  if (newUsername) body.new_username = newUsername;
  if (newPassword) body.new_password = newPassword;

  await request<{ status: string }>('/api/v1/auth/credentials', {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export async function fixMp4(
  payload: Mp4FixRequest,
  signal?: AbortSignal,
): Promise<Mp4FixResponse> {
  return request<Mp4FixResponse>('/api/v1/tools/mp4-fix', {
    method: 'POST',
    signal,
    body: JSON.stringify(payload),
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
