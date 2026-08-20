import type { AudioExport, ExportSettings, SubtitleExport, VideoExport } from '../models';

export const allowedExtensions = new Set([
  'mp4',
  'mov',
  'mkv',
  'avi',
  'webm',
  'm4v',
  'mpg',
  'mpeg',
]);
export const pollMs = 5000;
export const presetStorageKey = 'video-converter-presets-v1';

export type AppPage = 'dashboard' | 'convert' | 'presets' | 'settings';
export type DashboardView = 'queue' | 'history' | 'outputs' | 'advanced';

export type LocalPreset = {
  id: string;
  name: string;
  description: string;
  settings: ExportSettings;
  createdAt: string;
  updatedAt: string;
};

export const videoOptions: VideoExport[] = ['mp4', 'mkv', 'webm'];
export const audioOptions: AudioExport[] = ['copy', 'aac', 'mp3', 'opus'];
export const subtitleOptions: SubtitleExport[] = ['none', 'embedded', 'separate_srt'];

export const defaultSettings: ExportSettings = {
  video_export: 'mp4',
  audio_export: 'copy',
  subtitle_export: 'none',
  subtitle_language: '',
  quality_crf: 23,
  target_video_bitrate: '',
  audio_bitrate_kbps: 128,
  resolution: 'original',
  encoder_preset: 'veryfast',
  hardware_acceleration: 'auto',
  max_attempts: 3,
  priority: 0,
  audio_stream_indexes: null,
  subtitle_stream_indexes: null,
  audio_channel_mode: 'preserve',
  skip_existing_output: false,
  audioStreamIndexes: null,
  subtitleStreamIndexes: null,
  audioChannelMode: 'preserve',
  skipExistingOutput: false,
};

export function loadStoredPresets(): LocalPreset[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(presetStorageKey) || '[]') as LocalPreset[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
