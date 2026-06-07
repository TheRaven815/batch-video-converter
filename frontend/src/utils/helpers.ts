import type { JobRecord, OutputFileDto } from '../models';

export function fileName(path: string): string {
  return path.split('/').filter(Boolean).pop() || path || 'video';
}

export function formatDate(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = bytes;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatEta(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return 'ETA —';
  if (seconds < 60) return `ETA ${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `ETA ${minutes}m ${remaining}s`;
}

export function deriveProfile(videoExport: string): string {
  if (videoExport === 'webm') return 'vp9_webm';
  if (videoExport === 'mkv') return 'h265_mp4';
  return 'h264_mp4';
}

export function normalizeStatus(status: string): string {
  return status;
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
  if (sort === 'oldest') return copy.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
  if (sort === 'progress') return copy.sort((a, b) => getProgress(b) - getProgress(a));
  return copy.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime() || b.id.localeCompare(a.id));
}

function sameJob(left: JobRecord, right: JobRecord): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function mergeJobs(current: JobRecord[], incoming: JobRecord[]): JobRecord[] {
  const currentById = new Map(current.map((job) => [job.id, job]));
  let changed = current.length !== incoming.length;
  const next = incoming.map((job) => {
    const existing = currentById.get(job.id);
    if (existing && sameJob(existing, job)) return existing;
    changed = true;
    return job;
  });
  if (!changed) {
    for (let index = 0; index < current.length; index += 1) {
      if (current[index]?.id !== next[index]?.id) {
        changed = true;
        break;
      }
    }
  }
  return changed ? next : current;
}

function sameOutput(left: OutputFileDto, right: OutputFileDto): boolean {
  return left.filename === right.filename && left.size_bytes === right.size_bytes && left.modified_at === right.modified_at && left.download_url === right.download_url;
}

export function mergeOutputs(current: OutputFileDto[], incoming: OutputFileDto[]): OutputFileDto[] {
  const currentByName = new Map(current.map((output) => [output.filename, output]));
  let changed = current.length !== incoming.length;
  const next = incoming.map((output) => {
    const existing = currentByName.get(output.filename);
    if (existing && sameOutput(existing, output)) return existing;
    changed = true;
    return output;
  });
  if (!changed) {
    for (let index = 0; index < current.length; index += 1) {
      if (current[index]?.filename !== next[index]?.filename) {
        changed = true;
        break;
      }
    }
  }
  return changed ? next : current;
}

export function createPresetId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `preset-${Date.now()}`;
}
