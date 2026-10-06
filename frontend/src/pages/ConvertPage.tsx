import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Download, FileVideo, Folder, Play, Search, Trash2, Upload, Wrench } from 'lucide-react';
import { useLocation } from 'wouter';

import { downloadOutput, fixMp4, probeSubtitles, uploadMedia } from '../api';
import { useAppContext } from '../context/AppContext';
import { serverKeys } from '../hooks/useServerState';
import { useI18n } from '../i18n';
import type { ExportSettings, Mp4FixRequest, Mp4FixResponse, StagedServerFile } from '../models';
import { uniqueLanguages } from '../utils/helpers';
import { audioOptions } from '../utils/constants';

export default function ConvertPage() {
  const app = useAppContext();
  const { t } = useI18n();
  const [, navigate] = useLocation();
  const selectedRoot = app.roots.find((root) => root.key === app.selectedRootKey);
  const selectedEntries = app.entries.filter(
    (entry) => entry.type === 'file' && app.selectedPaths.has(entry.rel_path),
  );
  const subtitleLanguages = useMemo(() => uniqueLanguages(app.staged), [app.staged]);
  const selectedStageCount = app.staged.filter((item) => item.selected).length;
  const [uploading, setUploading] = useState(false);

  const uploadFiles = async (files: FileList | File[]) => {
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const uploaded = await uploadMedia(file);
        app.setStaged((current) => [
          ...current,
          {
            id: `upload:${uploaded.input_filename}`,
            rootKey: '',
            rootLabel: t('convert.uploadRoot'),
            sourcePath: uploaded.input_filename,
            name: uploaded.input_filename,
            selected: true,
            uploaded: true,
          },
        ]);
      }
      app.showToast(t('convert.uploadAdded'), 'success');
    } catch (error) {
      app.showToast(error instanceof Error ? error.message : t('convert.uploadFailed'), 'error');
    } finally {
      setUploading(false);
    }
  };

  const addSelected = () => {
    const nextItems: StagedServerFile[] = selectedEntries.map((entry) => ({
      id: `${app.selectedRootKey}:${entry.rel_path}`,
      rootKey: app.selectedRootKey,
      rootLabel: selectedRoot?.label ?? '',
      sourcePath: entry.rel_path,
      name: entry.name,
      selected: true,
      subtitleProbeStatus: 'loading',
    }));
    app.setStaged((current) => {
      const existing = new Set(current.map((item) => item.id));
      return [...current, ...nextItems.filter((item) => !existing.has(item.id))];
    });
    nextItems.forEach((item) => {
      void probeSubtitles(item.rootKey, item.sourcePath)
        .then((result) => {
          const languages = [
            ...new Set(
              result.tracks
                .map((track) => track.language)
                .filter((language) => language && language !== 'und'),
            ),
          ];
          app.setStaged((current) =>
            current.map((staged) =>
              staged.id === item.id
                ? {
                    ...staged,
                    subtitleLanguages: languages,
                    subtitleTrackCount: result.tracks.length,
                    subtitleProbeStatus: 'done',
                  }
                : staged,
            ),
          );
        })
        .catch(() =>
          app.setStaged((current) =>
            current.map((staged) =>
              staged.id === item.id ? { ...staged, subtitleProbeStatus: 'error' } : staged,
            ),
          ),
        );
    });
    app.setSelectedPaths(new Set());
    app.showToast(t('convert.addedToStaging'), 'success');
  };

  return (
    <div className="form-container convert-page">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">{t('convert.title')}</h1>
        <p className="text-xs text-zinc-400 mt-1">{t('convert.subtitle')}</p>
      </div>
      <div className="form-panel">
        <span className="form-section-title border-b pb-2">{t('convert.sourceBrowser')}</span>
        <label
          className="upload-dropzone"
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void uploadFiles(event.dataTransfer.files);
          }}
        >
          <Upload size={18} />
          <span>{uploading ? t('convert.uploading') : t('convert.upload')}</span>
          <input
            type="file"
            accept="video/*,.mkv,.m4v"
            multiple
            hidden
            disabled={uploading}
            onChange={(event) => event.target.files && void uploadFiles(event.target.files)}
          />
        </label>
        <div className="browser-controls">
          <select
            className="form-input root-select"
            value={app.selectedRootKey}
            onChange={(event) => app.setSelectedRootKey(event.target.value)}
            aria-label={t('convert.mediaRoot')}
          >
            {app.roots.length ? (
              app.roots.map((root) => (
                <option key={root.key} value={root.key}>
                  {root.label}
                </option>
              ))
            ) : (
              <option value="">{t('convert.noRoots')}</option>
            )}
          </select>
          <div className="input-wrapper">
            <input
              className="form-input has-icon"
              placeholder={t('convert.searchFiles')}
              value={app.browserQuery}
              onChange={(event) => app.setBrowserQuery(event.target.value)}
              onKeyDown={(event) =>
                event.key === 'Enter' && void app.openPath(app.currentPath, app.browserQuery)
              }
            />
            <Search size={14} className="input-icon" />
          </div>
          <button
            className="btn btn-outline"
            onClick={() => void app.openPath(app.currentPath, app.browserQuery)}
          >
            {t('convert.find')}
          </button>
        </div>

        <div className="path-bar" aria-label={t('convert.currentFolder')}>
          <button className="path-btn" onClick={() => void app.openPath('', '')}>
            {t('convert.root')}
          </button>
          {app.currentPath
            .split('/')
            .filter(Boolean)
            .map((segment, index, segments) => {
              const target = segments.slice(0, index + 1).join('/');
              return (
                <Fragment key={target}>
                  <span className="path-sep">/</span>
                  {index === segments.length - 1 ? (
                    <span className="path-current">{segment}</span>
                  ) : (
                    <button className="path-btn" onClick={() => void app.openPath(target, '')}>
                      {segment}
                    </button>
                  )}
                </Fragment>
              );
            })}
        </div>

        <div className="border border-zinc-800 rounded bg-zinc-950 max-h-64 overflow-y-auto">
          {app.browserLoading ? (
            <div className="p-4 text-center text-zinc-500">{t('convert.loading')}</div>
          ) : app.entries.length ? (
            app.entries.map((entry) => {
              const selected = app.selectedPaths.has(entry.rel_path);
              const activate = () => {
                if (entry.type === 'dir') {
                  void app.openPath(entry.rel_path, '');
                  return;
                }
                app.setSelectedPaths((current) => {
                  const next = new Set(current);
                  if (next.has(entry.rel_path)) next.delete(entry.rel_path);
                  else next.add(entry.rel_path);
                  return next;
                });
              };
              return (
                <div
                  key={entry.rel_path}
                  role="button"
                  tabIndex={0}
                  className={`flex items-center gap-3 p-2 hover:bg-zinc-900 border-b border-zinc-800 cursor-pointer ${selected ? 'bg-zinc-900' : ''}`}
                  onClick={activate}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') activate();
                  }}
                >
                  {entry.type === 'dir' ? (
                    <Folder size={16} className="text-blue-400" />
                  ) : (
                    <FileVideo size={16} className="text-zinc-500" />
                  )}
                  <span className="text-xs text-zinc-200 flex-grow">{entry.name}</span>
                  {entry.type === 'file' && (
                    <input type="checkbox" className="form-checkbox" checked={selected} readOnly />
                  )}
                </div>
              );
            })
          ) : (
            <div className="p-4 text-center text-zinc-500">{t('convert.noMedia')}</div>
          )}
        </div>
        <div className="flex justify-end mt-2">
          <button
            className="btn btn-primary"
            onClick={addSelected}
            disabled={!selectedEntries.length}
          >
            {t('convert.addSelected')}
          </button>
        </div>

        {app.staged.length > 0 && (
          <>
            <span className="form-section-title border-b pb-2 mt-4">
              {t('convert.stagedFiles', { count: app.staged.length })}
            </span>
            <div className="border border-zinc-800 rounded bg-zinc-950 max-h-40 overflow-y-auto">
              {app.staged.map((item) => (
                <div key={item.id} className="flex items-center gap-3 p-2 border-b border-zinc-800">
                  <input
                    type="checkbox"
                    className="form-checkbox"
                    checked={item.selected}
                    onChange={(event) =>
                      app.setStaged((current) =>
                        current.map((staged) =>
                          staged.id === item.id
                            ? { ...staged, selected: event.target.checked }
                            : staged,
                        ),
                      )
                    }
                  />
                  <span className="text-xs text-zinc-200 truncate flex-grow">{item.name}</span>
                  <button
                    className="btn-icon"
                    onClick={() =>
                      app.setStaged((current) => current.filter((staged) => staged.id !== item.id))
                    }
                    aria-label={t('convert.removeStaged', { name: item.name })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </>
        )}

        <span className="form-section-title border-b pb-2 mt-4">{t('convert.exportOptions')}</span>
        <div className="form-grid">
          <ExportSelect
            label={t('convert.videoFormat')}
            value={app.settings.video_export}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                video_export: value as ExportSettings['video_export'],
              }))
            }
            options={[
              ['mp4', 'MP4 (H.264)'],
              ['mkv', 'MKV (H.265)'],
              ['webm', 'WebM (VP9)'],
            ]}
          />
          <ExportSelect
            label={t('convert.audio')}
            value={app.settings.audio_export}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                audio_export: value as ExportSettings['audio_export'],
              }))
            }
            options={audioOptions[app.settings.video_export].map((option) => [
              option, option === 'copy' ? t('convert.copyOriginal') : option.toUpperCase(),
            ])}
          />
          {!audioOptions[app.settings.video_export].includes(app.settings.audio_export) && (
            <p className="form-alert-error" role="alert">{t('convert.incompatibleAudio')}</p>
          )}
          <ExportSelect
            label={t('convert.subtitles')}
            value={app.settings.subtitle_export}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                subtitle_export: value as ExportSettings['subtitle_export'],
              }))
            }
            options={[
              ['none', t('common.none')],
              ['embedded', t('convert.embedded')],
              ['separate_srt', t('convert.separateSrt')],
            ]}
          />
          <ExportSelect
            label={t('convert.languagePreference')}
            value={app.settings.subtitle_language}
            onChange={(value) =>
              app.setSettings((current) => ({ ...current, subtitle_language: value }))
            }
            options={[
              ['', t('convert.autoDetect')],
              ...subtitleLanguages.map((language) => [language, language] as [string, string]),
            ]}
          />
          <ExportSelect
            label={t('convert.resolution')}
            value={app.settings.resolution}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                resolution: value as ExportSettings['resolution'],
              }))
            }
            options={[
              ['original', t('convert.original')],
              ['1080p', '1080p'],
              ['720p', '720p'],
              ['480p', '480p'],
            ]}
          />
          <ExportSelect
            label={t('convert.encoderPreset')}
            value={app.settings.encoder_preset}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                encoder_preset: value as ExportSettings['encoder_preset'],
              }))
            }
            options={[
              ['ultrafast', t('convert.ultraFast')],
              ['veryfast', t('convert.veryFast')],
              ['fast', t('convert.fast')],
              ['medium', t('convert.medium')],
              ['slow', t('convert.slow')],
            ]}
          />
          <div className="form-group">
            <label className="form-label" htmlFor="quality-crf">{t('convert.quality')}</label>
            <input
              id="quality-crf"
              disabled={Boolean(app.settings.target_video_bitrate.trim())}
              aria-describedby={app.settings.target_video_bitrate.trim() ? 'quality-bitrate-note' : undefined}
              className="form-input"
              type="number"
              min={0}
              max={51}
              value={app.settings.quality_crf}
              onChange={(event) =>
                app.setSettings((current) => ({
                  ...current,
                  quality_crf: Number(event.target.value),
                }))
              }
            />
            {app.settings.target_video_bitrate.trim() && (
              <p id="quality-bitrate-note" className="text-xs text-zinc-400">
                Hedef video bitrate seçiliyken CRF kullanılmaz.
              </p>
            )}
          </div>
          <div className="form-group">
            <label className="form-label">{t('convert.videoBitrate')}</label>
            <input
              className="form-input"
              placeholder={t('convert.videoBitrateHint')}
              value={app.settings.target_video_bitrate}
              onChange={(event) =>
                app.setSettings((current) => ({
                  ...current,
                  target_video_bitrate: event.target.value,
                }))
              }
            />
          </div>
          <div className="form-group">
            <label className="form-label">{t('convert.audioBitrate')}</label>
            <input
              className="form-input"
              type="number"
              min={32}
              max={512}
              value={app.settings.audio_bitrate_kbps}
              onChange={(event) =>
                app.setSettings((current) => ({
                  ...current,
                  audio_bitrate_kbps: Number(event.target.value),
                }))
              }
            />
          </div>
          <ExportSelect
            label={t('convert.hardwareAcceleration')}
            value={app.settings.hardware_acceleration}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                hardware_acceleration: value as ExportSettings['hardware_acceleration'],
              }))
            }
            options={[
              ['auto', t('convert.autoDetect')],
              ['disabled', t('convert.disabled')],
              ['v4l2m2m', 'Raspberry Pi V4L2'],
            ]}
          />
          <div className="form-group">
            <label className="form-label">{t('convert.retryAttempts')}</label>
            <input
              className="form-input"
              type="number"
              min={1}
              max={10}
              value={app.settings.max_attempts}
              onChange={(event) =>
                app.setSettings((current) => ({
                  ...current,
                  max_attempts: Number(event.target.value),
                }))
              }
            />
          </div>
          <ExportSelect
            label={t('convert.queuePriority')}
            value={String(app.settings.priority)}
            onChange={(value) =>
              app.setSettings((current) => ({ ...current, priority: Number(value) }))
            }
            options={[
              ['0', t('convert.normal')],
              ['5', t('convert.high')],
              ['10', t('convert.urgent')],
              ['-5', t('convert.low')],
            ]}
          />
        </div>
        <div className="convert-actions border-t border-zinc-800 pt-4 flex items-center justify-end gap-2">
          <button className="btn btn-outline" onClick={() => navigate('/dashboard')}>
            {t('common.cancel')}
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void app.submitBatch()}
            disabled={app.submitting || selectedStageCount === 0 || !audioOptions[app.settings.video_export].includes(app.settings.audio_export)}
          >
            <Play size={14} />
            <span>{t('convert.queueJobs', { count: selectedStageCount })}</span>
          </button>
        </div>

        <Mp4RepairCard />
      </div>
    </div>
  );
}

function Mp4RepairCard() {
  const app = useAppContext();
  const queryClient = useQueryClient();
  const [fixing, setFixing] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [repaired, setRepaired] = useState<Mp4FixResponse | null>(null);
  const [downloading, setDownloading] = useState(false);
  const repairController = useRef<AbortController | null>(null);
  useEffect(() => () => repairController.current?.abort(), []);

  // Prefer staged selection, fallback to media browser selection
  const stagedOptions = app.staged.filter((s) => s.name.toLowerCase().endsWith('.mp4'));
  const canUseBrowser = app.selectedPaths.size > 0;

  const handleRepair = async () => {
    let payload: Mp4FixRequest | null = null;
    if (selected && stagedOptions.find((s) => s.id === selected)) {
      const item = stagedOptions.find((s) => s.id === selected)!;
      if (item.uploaded) {
        payload = { input_filename: item.sourcePath };
      } else {
        payload = { source_root_key: item.rootKey, source_path: item.sourcePath };
      }
    } else if (canUseBrowser) {
      const first = Array.from(app.selectedPaths)[0];
      // Only handle mp4 files
      if (first && first.toLowerCase().endsWith('.mp4')) {
        payload = { source_root_key: app.selectedRootKey, source_path: first };
      }
    }
    if (!payload) {
      app.showToast('Onarılacak MP4 dosyası seçin (hazırlananlar veya tarayıcıdan).', 'error');
      return;
    }
    setFixing(true);
    setRepaired(null);
    const controller = new AbortController();
    repairController.current = controller;
    try {
      const res = await fixMp4(payload, controller.signal);
      setRepaired(res);
      void queryClient.invalidateQueries({ queryKey: serverKeys.outputs });
      app.showToast(res.message, 'success');
    } catch (error) {
      if (controller.signal.aborted) {
        app.showToast('Onarım iptal edildi.', 'info');
      } else {
        app.showToast(error instanceof Error ? error.message : 'Onarım başarısız.', 'error');
      }
    } finally {
      repairController.current = null;
      setFixing(false);
    }
  };

  return (
    <div className="form-panel">
      <span className="form-section-title border-b pb-2 flex items-center gap-2">
        <Wrench size={14} /> MP4 Onar (faststart + genpts)
      </span>
      <p className="text-xs text-zinc-400">
        MP4 dosyasını faststart + genpts ile yeniden paketleyip Çıktılar bölümünde yeni bir
        onarılmış kopya oluşturur. Kaynak dosya değiştirilmez. Eksik moov verisini yeniden
        oluşturamaz. Kaynak tarayıcıdan veya hazırlanan dosyalardan bir MP4 seçin.
      </p>
      {stagedOptions.length > 0 && (
        <div className="form-group">
          <label className="form-label" htmlFor="mp4-repair-source">Hazırlanan MP4 dosyası</label>
          <select
            id="mp4-repair-source"
            className="form-input"
            value={selected ?? ''}
            onChange={(e) => setSelected(e.target.value || null)}
            disabled={fixing}
          >
            <option value="">— Seçin (veya tarayıcı seçimini kullan) —</option>
            {stagedOptions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name} [{item.rootLabel}]
              </option>
            ))}
          </select>
        </div>
      )}
      <p role="status" className="text-xs break-all">
        {repaired ? `Onarılmış kopya: ${repaired.filename}` : fixing ? 'Onarılmış kopya oluşturuluyor…' : ''}
      </p>
      {repaired && (
        <button
          className="btn btn-outline"
          disabled={downloading}
          onClick={() => {
            setDownloading(true);
            void downloadOutput(repaired.filename)
              .catch((error: unknown) => app.showToast(error instanceof Error ? error.message : 'İndirme başarısız.', 'error'))
              .finally(() => setDownloading(false));
          }}
        >
          <Download size={14} aria-hidden="true" />
          {downloading ? 'İndiriliyor…' : 'Onarılmış kopyayı indir'}
        </button>
      )}
      <div className="flex justify-end">
        {fixing && (
          <button className="btn btn-outline" onClick={() => repairController.current?.abort()}>
            Onarımı iptal et
          </button>
        )}
        <button className="btn btn-outline" onClick={() => void handleRepair()} disabled={fixing}>
          <Wrench size={14} />
          <span>{fixing ? 'Kopya oluşturuluyor…' : 'Onarılmış kopya oluştur'}</span>
        </button>
      </div>
    </div>
  );
}

function ExportSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<[string, string]>;
}) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <select
        className="form-input"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
            {!options.some(([optionValue]) => optionValue === value) && (
              <option value={value} disabled>{value.toUpperCase()}</option>
            )}
        {options.map(([optionValue, text]) => (
          <option value={optionValue} key={optionValue}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}
