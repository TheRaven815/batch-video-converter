import type { ExportProfile, JobRecord, JobStatus } from '../models';
import type { AppLanguage } from '../i18n';

export function fileName(path: string): string {
  return path.split('/').filter(Boolean).pop() || path || 'video';
}

export function formatDate(value?: string | null, language: AppLanguage = 'en'): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(language === 'tr' ? 'tr-TR' : 'en-GB', {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (!bytes && bytes !== 0) return '0 B';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  // Keep one decimal except for bytes.
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatEta(seconds?: number | null, language: AppLanguage = 'en'): string {
  const prefix = language === 'tr' ? 'Tahmini' : 'ETA';
  if (seconds === null || seconds === undefined) return `${prefix} —`;
  if (seconds < 0) seconds = 0;
  if (seconds < 60) return `${prefix} ${seconds}${language === 'tr' ? ' sn' : 's'}`;
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = Math.floor(seconds % 60);
  if (days > 0) {
    if (language === 'tr') {
      return `${prefix} ${days} gün ${hours} sa ${minutes} dk`;
    }
    return `${prefix} ${days}d ${hours}h ${minutes}m`;
  }
  if (hours > 0)
    return `${prefix} ${hours} ${language === 'tr' ? 'sa' : 'h'} ${minutes} ${language === 'tr' ? 'dk' : 'm'}`;
  return `${prefix} ${minutes} ${language === 'tr' ? 'dk' : 'm'} ${secs} ${language === 'tr' ? 'sn' : 's'}`;
}

export function deriveProfile(videoExport: string): ExportProfile {
  if (videoExport === 'webm') return 'vp9_webm';
  if (videoExport === 'mkv') return 'h265_mp4';
  return 'h264_mp4';
}

export function normalizeStatus(status: string): JobStatus {
  const value = (status || '').toLowerCase();
  if (value === 'done' || value === 'success' || value === 'succeeded') return 'completed';
  if (value === 'error') return 'failed';
  if (value === 'pending') return 'queued';
  return value as JobStatus;
}

export type StatusVariant = 'done' | 'running' | 'queued' | 'failed' | 'cancelled';

export function statusVariant(status: string): StatusVariant {
  const normalized = normalizeStatus(status);
  if (normalized === 'completed') return 'done';
  if (normalized === 'running' || normalized === 'failed' || normalized === 'cancelled')
    return normalized;
  return 'queued';
}

export function getProgress(job: JobRecord): number {
  return Math.max(0, Math.min(100, Number(job.progress_percent ?? 0)));
}

export function uniqueLanguages(staged: { subtitleLanguages?: string[] }[]): string[] {
  const languages = new Set<string>();
  staged.forEach((item) => item.subtitleLanguages?.forEach((lang) => languages.add(lang)));
  return [...languages].sort((a, b) => a.localeCompare(b));
}

export function sortJobs(jobs: JobRecord[], sort: string): JobRecord[] {
  const copy = [...jobs];
  if (sort === 'oldest')
    return copy.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  if (sort === 'progress') return copy.sort((a, b) => getProgress(b) - getProgress(a));
  return copy.sort(
    (a, b) =>
      new Date(b.created_at).getTime() - new Date(a.created_at).getTime() ||
      b.id.localeCompare(a.id),
  );
}

export function createPresetId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `preset-${Date.now()}`;
}
