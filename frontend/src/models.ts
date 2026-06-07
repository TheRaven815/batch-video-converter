export type JobStatus = 'queued' | 'running' | 'cancelled' | 'completed' | 'failed';

export type VideoExport = 'mp4' | 'mkv' | 'webm';
export type AudioExport = 'copy' | 'aac' | 'mp3' | 'opus';
export type SubtitleExport = 'none' | 'embedded' | 'separate_srt';

export interface HealthResponse {
  status: string;
  redis: string;
}

export interface WorkerHealthResponse {
  status: string;
  redis: string;
  queue_depth: number;
  running_jobs: number;
  checked_at: string;
}

export interface JobCreateRequest {
  input_filename?: string | null;
  source_root_key?: string | null;
  source_path?: string | null;
  profile: string;
  video_export: VideoExport;
  audio_export: AudioExport;
  subtitle_export: SubtitleExport;
  subtitle_language?: string | null;
}

export interface JobRecord extends JobCreateRequest {
  id: string;
  status: JobStatus;
  output_filename?: string | null;
  error_message?: string | null;
  progress_percent?: number | null;
  progress_phase?: string | null;
  progress_message?: string | null;
  progress_updated_at?: string | null;
  progress_eta_seconds?: number | null;
  progress_fps?: number | null;
  progress_speed?: string | null;
  progress_bitrate?: string | null;
  progress_out_time_seconds?: number | null;
  log_tail: string[];
  timeline: Array<{ at?: string; status?: JobStatus; phase?: string; message?: string | null }>;
  archived: boolean;
  cancel_requested: boolean;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  batch_id?: string | null;
  attempt_count: number;
}

export interface JobBatchCreateResponse {
  jobs: JobRecord[];
  idempotency_key?: string | null;
}

export interface JobValidationItem {
  index: number;
  valid: boolean;
  input_filename?: string | null;
  source_root_key?: string | null;
  source_path?: string | null;
  error_code?: string | null;
  message?: string | null;
  recoverable: boolean;
}

export interface JobValidationResponse {
  items: JobValidationItem[];
  valid_count: number;
  invalid_count: number;
}

export interface BatchSummaryDto {
  batch_id: string;
  total: number;
  queued: number;
  running: number;
  cancelled: number;
  completed: number;
  failed: number;
  progress_percent: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface BatchListResponse {
  batches: BatchSummaryDto[];
  next_cursor?: string | null;
}

export interface ErrorEnvelope {
  code: string;
  message: string;
  recoverable: boolean;
  details?: Record<string, unknown> | null;
}

export interface StructuredErrorResponse {
  error: ErrorEnvelope;
}

export interface JobStreamPayload {
  event: 'jobs_snapshot' | 'heartbeat' | string;
  timestamp: string;
  data: { jobs?: JobRecord[] };
}

export interface JobActionSkip {
  job_id: string;
  reason: string;
}

export interface JobBulkActionResponse {
  updated: JobRecord[];
  skipped: JobActionSkip[];
}

export interface MediaRootDto {
  key: string;
  label: string;
}

export interface MediaBrowseEntryDto {
  type: 'dir' | 'file';
  name: string;
  rel_path: string;
}

export interface MediaBrowseResponse {
  root_key: string;
  current_path: string;
  entries: MediaBrowseEntryDto[];
  next_cursor?: string | null;
}

export interface MediaSubtitleTrackDto {
  index: number;
  language: string;
  title?: string | null;
  codec_name?: string | null;
}

export interface MediaSubtitleProbeResponse {
  root_key: string;
  path: string;
  tracks: MediaSubtitleTrackDto[];
}

export interface OutputFileDto {
  filename: string;
  size_bytes: number;
  modified_at: string;
  download_url: string;
}

export interface OutputListResponse {
  outputs: OutputFileDto[];
  next_cursor?: string | null;
}

export interface StagedServerFile {
  id: string;
  rootKey: string;
  rootLabel: string;
  sourcePath: string;
  name: string;
  selected: boolean;
  subtitleTrackCount?: number;
  subtitleLanguages?: string[];
  subtitleProbeStatus?: 'idle' | 'loading' | 'done' | 'error';
}

export interface ExportSettings {
  video_export: VideoExport;
  audio_export: AudioExport;
  subtitle_export: SubtitleExport;
  subtitle_language: string;
}

export interface JobFilters {
  q: string;
  status: JobStatus | 'all';
  sort: 'newest' | 'oldest' | 'progress';
  profile: string;
  sourceType: 'all' | 'server' | 'legacy';
}
