import { Sliders, Trash2 } from 'lucide-react';
import { useLocation } from 'wouter';

import { useAppContext } from '../context/AppContext';
import { useI18n } from '../i18n';
import type { ExportSettings } from '../models';

export default function PresetsPage() {
  const app = useAppContext();
  const { t } = useI18n();
  const [, navigate] = useLocation();
  const choices = {
    video_export: [
      ['mp4', 'MP4 (H.264)'],
      ['mkv', 'MKV (H.265)'],
      ['webm', 'WebM (VP9)'],
    ],
    audio_export: [
      ['copy', t('convert.copyOriginal')],
      ['aac', 'AAC'],
      ['mp3', 'MP3'],
      ['opus', 'Opus'],
    ],
    subtitle_export: [
      ['none', t('common.none')],
      ['embedded', t('convert.embedded')],
      ['separate_srt', t('convert.separateSrt')],
    ],
  } as const;

  return (
    <div className="form-container presets-page">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">{t('presets.title')}</h1>
        <p className="text-xs text-zinc-400 mt-1">{t('presets.subtitle')}</p>
      </div>
      <div className="form-panel mb-6">
        <span className="form-section-title border-b pb-2">
          {app.editingPresetId ? t('presets.edit') : t('presets.create')}
        </span>
        <div className="form-grid preset-identity-grid mt-4">
          <TextField
            label={t('presets.name')}
            value={app.presetName}
            onChange={app.setPresetName}
          />
          <TextField
            label={t('presets.description')}
            value={app.presetDescription}
            onChange={app.setPresetDescription}
          />
        </div>
        <span className="form-section-title border-b pb-2 mt-4">{t('presets.exportOptions')}</span>
        <div className="form-grid preset-options-grid mt-4">
          <PresetSelect
            label={t('convert.videoFormat')}
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
            label={t('convert.audio')}
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
            label={t('convert.subtitles')}
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
            label={t('convert.languagePreference')}
            value={app.presetSettings.subtitle_language}
            onChange={(subtitle_language) =>
              app.setPresetSettings((current) => ({ ...current, subtitle_language }))
            }
          />
        </div>
        <div className="pt-4 flex gap-2 justify-end">
          <button className="btn btn-outline" onClick={app.resetPresetForm}>
            {t('presets.clearForm')}
          </button>
          <button
            className="btn btn-primary"
            disabled={!app.presetName.trim()}
            onClick={() => {
              app.savePreset();
              app.showToast(
                app.editingPresetId ? t('presets.updated') : t('presets.saved'),
                'success',
              );
            }}
          >
            {app.editingPresetId ? t('presets.update') : t('presets.save')}
          </button>
        </div>
      </div>

      <div className="presets-grid">
        {app.presets.map((preset) => (
          <div className="preset-card" key={preset.id}>
            <div className="preset-header">
              <span className="preset-badge">FFmpeg</span>
              <span className="preset-type">{t('presets.custom')}</span>
            </div>
            <div className="preset-body">
              <h4>{preset.name}</h4>
              <p>{preset.description || t('presets.noDescription')}</p>
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
                  app.showToast(t('presets.loaded', { name: preset.name }), 'info');
                }}
              >
                {t('presets.apply')}
              </button>
              <button className="btn btn-outline" onClick={() => app.startEditPreset(preset)}>
                {t('common.edit')}
              </button>
              <button
                className="btn btn-danger"
                onClick={() => app.deletePreset(preset.id)}
                aria-label={t('presets.deleteNamed', { name: preset.name })}
              >
                <Trash2 size={14} />
              </button>
            </div>
          </div>
        ))}
        <button type="button" className="preset-card preset-new" onClick={app.resetPresetForm}>
          <Sliders size={24} className="text-zinc-500" />
          <div className="text-sm font-medium text-zinc-300 mt-2">{t('presets.new')}</div>
          <div className="text-xs text-zinc-500 mt-1">{t('presets.newHelp')}</div>
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
