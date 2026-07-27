import { Sliders, Trash2 } from 'lucide-react';
import { useLocation } from 'wouter';

import { useAppContext } from '../context/AppContext';
import type { ExportSettings } from '../models';

const choices = {
  video_export: [
    ['mp4', 'MP4 (H.264)'],
    ['mkv', 'MKV (H.265)'],
    ['webm', 'WebM (VP9)'],
  ],
  audio_export: [
    ['copy', 'Copy Original'],
    ['aac', 'AAC'],
    ['mp3', 'MP3'],
    ['opus', 'Opus'],
  ],
  subtitle_export: [
    ['none', 'None'],
    ['embedded', 'Embedded'],
    ['separate_srt', 'Separate SRT'],
  ],
} as const;

export default function PresetsPage() {
  const app = useAppContext();
  const [, navigate] = useLocation();

  return (
    <div className="form-container">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">Presets</h1>
        <p className="text-xs text-zinc-400 mt-1">Manage your saved conversion profiles.</p>
      </div>
      <div className="form-panel mb-6">
        <span className="form-section-title border-b pb-2">
          {app.editingPresetId ? 'Edit Preset' : 'Create Preset'}
        </span>
        <div className="form-grid mt-4">
          <TextField label="Name" value={app.presetName} onChange={app.setPresetName} />
          <TextField
            label="Description"
            value={app.presetDescription}
            onChange={app.setPresetDescription}
          />
        </div>
        <span className="form-section-title border-b pb-2 mt-4">Export Options</span>
        <div className="form-grid mt-4">
          <PresetSelect
            label="Video Format"
            value={app.presetSettings.video_export}
            options={choices.video_export}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                video_export: value as ExportSettings['video_export'],
              }))
            }
          />
          <PresetSelect
            label="Audio"
            value={app.presetSettings.audio_export}
            options={choices.audio_export}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                audio_export: value as ExportSettings['audio_export'],
              }))
            }
          />
          <PresetSelect
            label="Subtitles"
            value={app.presetSettings.subtitle_export}
            options={choices.subtitle_export}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                subtitle_export: value as ExportSettings['subtitle_export'],
              }))
            }
          />
          <TextField
            label="Language Preference"
            value={app.presetSettings.subtitle_language}
            onChange={(subtitle_language) =>
              app.setPresetSettings((current) => ({ ...current, subtitle_language }))
            }
          />
        </div>
        <div className="pt-4 flex gap-2 justify-end">
          <button className="btn btn-outline" onClick={app.resetPresetForm}>
            Clear Form
          </button>
          <button
            className="btn btn-primary"
            disabled={!app.presetName.trim()}
            onClick={() => {
              app.savePreset();
              app.showToast(app.editingPresetId ? 'Preset updated' : 'Preset saved', 'success');
            }}
          >
            {app.editingPresetId ? 'Update Preset' : 'Save Preset'}
          </button>
        </div>
      </div>

      <div className="presets-grid">
        {app.presets.map((preset) => (
          <div className="preset-card" key={preset.id}>
            <div className="preset-header">
              <span className="preset-badge">FFmpeg</span>
              <span className="preset-type">Custom</span>
            </div>
            <div className="preset-body">
              <h4>{preset.name}</h4>
              <p>{preset.description || 'No description'}</p>
              <div className="text-xs text-zinc-500 mt-2 font-mono">
                {preset.settings.video_export}/{preset.settings.audio_export}
              </div>
            </div>
            <div className="mt-auto pt-4 flex gap-2 border-t border-zinc-800">
              <button
                className="btn btn-primary flex-grow justify-center"
                onClick={() => {
                  app.setSettings(preset.settings);
                  navigate('/convert');
                  app.showToast(`Loaded preset ${preset.name}`, 'info');
                }}
              >
                Apply
              </button>
              <button className="btn btn-outline" onClick={() => app.startEditPreset(preset)}>
                Edit
              </button>
              <button
                className="btn btn-danger"
                onClick={() => app.deletePreset(preset.id)}
                aria-label={`Delete preset ${preset.name}`}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        <button type="button" className="preset-card preset-new" onClick={app.resetPresetForm}>
          <Sliders size={24} className="text-zinc-500" />
          <div className="text-sm font-medium text-zinc-300 mt-2">New Preset</div>
          <div className="text-xs text-zinc-500 mt-1">Clear form to create a new preset.</div>
        </button>
      </div>
    </div>
  );
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <input
        className="form-input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function PresetSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: ReadonlyArray<readonly [string, string]>;
  onChange: (value: string) => void;
}) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <select
        className="form-input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map(([optionValue, text]) => (
          <option value={optionValue} key={optionValue}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}
