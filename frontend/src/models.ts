import type { components } from './generated/api-schema';

type Schemas = components['schemas'];

export type JobStatus = Schemas['JobStatus'];
export type ExportProfile = Schemas['ExportProfile'];
export type VideoExport = Schemas['VideoExport'];
export type AudioExport = Schemas['AudioExport'];
export type SubtitleExport = Schemas['SubtitleExport'];
export type HealthResponse = Schemas['HealthResponse'];
export type WorkerHealthResponse = Schemas['WorkerHealthResponse'];
export type JobCreateRequest = Schemas['JobCreateRequest'];
export type JobBatchCreateResponse = Schemas['JobBatchCreateResponse'];
export type JobValidationItem = Schemas['JobValidationItem'];
export type JobValidationResponse = Schemas['JobValidationResponse'];
export type BatchSummaryDto = Schemas['BatchSummaryDto'];
export type BatchListResponse = Schemas['BatchListResponse'];
export type ErrorEnvelope = Omit<Schemas['ErrorEnvelope'], 'details'> & {
  details?: Record<string, unknown> | null;
};
export type StructuredErrorResponse = Omit<Schemas['StructuredErrorResponse'], 'error'> & {
  error: ErrorEnvelope;
};
export type JobActionSkip = Schemas['JobActionSkip'];
export type JobBulkActionResponse = Omit<
  Schemas['JobBulkActionResponse'],
  'updated' | 'skipped'
> & {
  updated: JobRecord[];
  skipped: JobActionSkip[];
};
export type MediaRootDto = Schemas['MediaRootDto'];
export type MediaBrowseEntryDto = Omit<Schemas['MediaBrowseEntryDto'], 'type'> & {
  type: 'dir' | 'file';
};
export type MediaBrowseResponse = Omit<Schemas['MediaBrowseResponse'], 'entries'> & {
  entries: MediaBrowseEntryDto[];
};
export type MediaSubtitleTrackDto = Schemas['MediaSubtitleTrackDto'];
export type MediaSubtitleProbeResponse = Schemas['MediaSubtitleProbeResponse'];
export type OutputFileDto = Schemas['OutputFileDto'];
export type OutputListResponse = Schemas['OutputListResponse'];

type ApiJobRecord = Schemas['JobRecord'];
export type JobRecord = Omit<ApiJobRecord, 'timeline'> & {
  timeline?: Array<{
    at?: string;
    status?: JobStatus;
    phase?: string;
    message?: string | null;
  }>;
};

export interface JobStreamPayload {
  event: 'jobs_snapshot' | 'job_updated' | 'job_deleted' | 'heartbeat' | string;
  timestamp: string;
  data: { jobs?: JobRecord[]; job?: JobRecord; job_id?: string };
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

export type UiTheme = 'dark' | 'light' | 'system';
export type UiDensity = 'comfortable' | 'compact';
export type DefaultExportSettings = Schemas['DefaultExportSettings'];
export type AutoCleanupSettings = Schemas['AutoCleanupSettings'];
export type UiPreferences = Omit<Schemas['UiPreferences'], 'theme' | 'density'> & {
  theme: UiTheme;
  density: UiDensity;
};
export type SystemSettings = Omit<
  Schemas['SystemSettings-Output'],
  'default_export' | 'auto_cleanup' | 'ui'
> & {
  default_export: DefaultExportSettings;
  auto_cleanup: AutoCleanupSettings;
  ui: UiPreferences;
};
