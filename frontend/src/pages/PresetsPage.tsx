import { useId } from 'react';
import { Sliders, Trash2 } from 'lucide-react';
import { useLocation } from 'wouter';

import { SelectField } from '../components/ui';
import { useAppContext } from '../context/AppContext';
import { useI18n } from '../i18n';
import type { ExportSettings } from '../models';
import { audioOptions, defaultSettings } from '../utils/constants';

export default function PresetsPage() {
  const app = useAppContext();
  const { t } = useI18n();
  const [, navigate] = useLocation();
  const choices = {
    video_export: [
      ['mp4', 'MP4 (H.264)'],
      ['mkv', 'MKV (H.264)'],
      ['webm', 'WebM (VP9)'],
    ],
    audio_export: audioOptions[app.presetSettings.video_export].map(
      (option) =>
        [option, option === 'copy' ? t('convert.copyOriginal') : option.toUpperCase()] as const,
    ),
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
            placeholder={t('presets.namePlaceholder')}
            onChange={app.setPresetName}
          />
          <TextField
            label={t('presets.description')}
            value={app.presetDescription}
            placeholder={t('presets.descriptionPlaceholder')}
            onChange={app.setPresetDescription}
          />
        </div>
        <span className="form-section-title border-b pb-2 mt-4">{t('presets.exportOptions')}</span>
        <div className="form-grid preset-options-grid mt-4">
          <SelectField
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
          <SelectField
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
          {!audioOptions[app.presetSettings.video_export].includes(
            app.presetSettings.audio_export,
          ) && (
            <p className="form-alert-error" role="alert">
              {t('convert.incompatibleAudio')}
            </p>
          )}
          <SelectField
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
          <SelectField
            label={t('convert.resolution')}
            value={app.presetSettings.resolution}
            options={[
              ['original', t('convert.original')],
              ['1080p', '1080p'],
              ['720p', '720p'],
              ['480p', '480p'],
            ]}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                resolution: value as ExportSettings['resolution'],
              }))
            }
          />
          <SelectField
            label={t('convert.encoderPreset')}
            value={app.presetSettings.encoder_preset}
            options={[
              ['ultrafast', t('convert.ultraFast')],
              ['veryfast', t('convert.veryFast')],
              ['fast', t('convert.fast')],
              ['medium', t('convert.medium')],
              ['slow', t('convert.slow')],
            ]}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                encoder_preset: value as ExportSettings['encoder_preset'],
              }))
            }
          />
          <SelectField
            label={t('convert.hardwareAcceleration')}
            value={app.presetSettings.hardware_acceleration}
            options={[
              ['auto', t('convert.autoDetect')],
              ['disabled', t('convert.disabled')],
              ['v4l2m2m', 'V4L2 M2M'],
            ]}
            onChange={(value) =>
              app.setPresetSettings((current) => ({
                ...current,
                hardware_acceleration: value as ExportSettings['hardware_acceleration'],
              }))
            }
          />
          <NumberField
            label={t('convert.quality')}
            value={app.presetSettings.quality_crf}
            min={0}
            max={51}
            placeholder="Auto"
            onChange={(quality_crf) =>
              app.setPresetSettings((current) => ({ ...current, quality_crf }))
            }
          />
          <TextField
            label={t('convert.videoBitrate')}
            value={app.presetSettings.target_video_bitrate}
            onChange={(target_video_bitrate) =>
              app.setPresetSettings((current) => ({ ...current, target_video_bitrate }))
            }
          />
          <NumberField
            label={t('convert.audioBitrate')}
            value={app.presetSettings.audio_bitrate_kbps}
            min={32}
            max={512}
            placeholder="Auto"
            onChange={(audio_bitrate_kbps) =>
              app.setPresetSettings((current) => ({ ...current, audio_bitrate_kbps }))
            }
          />
        </div>
        <div className="pt-4 flex gap-2 justify-end">
          <button className="btn btn-outline" onClick={app.resetPresetForm}>
            {t('presets.clearForm')}
          </button>
          <button
            className="btn btn-primary"
            disabled={
              !app.presetName.trim() ||
              !audioOptions[app.presetSettings.video_export].includes(
                app.presetSettings.audio_export,
              )
            }
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
                  app.setSettings({ ...defaultSettings, ...preset.settings });
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
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  const id = useId();
  return (
    <div className="form-group">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="form-input"
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

function NumberField({
  label,
  value,
  onChange,
  placeholder,
  min,
  max,
}: {
  label: string;
  value: number | null;
  onChange: (value: number | null) => void;
  placeholder?: string;
  min?: number;
  max?: number;
}) {
  const id = useId();
  return (
    <div className="form-group">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="form-input"
        type="number"
        min={min}
        max={max}
        placeholder={placeholder}
        value={value ?? ''}
        onChange={(event) =>
          onChange(event.target.value === '' ? null : Number(event.target.value))
        }
      />
    </div>
  );
}
