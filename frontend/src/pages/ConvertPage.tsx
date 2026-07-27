import { Fragment, useMemo } from 'react';
import { FileVideo, Folder, Play, Search, Trash2 } from 'lucide-react';
import { useLocation } from 'wouter';

import { probeSubtitles } from '../api';
import { useAppContext } from '../context/AppContext';
import type { ExportSettings, StagedServerFile } from '../models';
import { uniqueLanguages } from '../utils/helpers';

export default function ConvertPage() {
  const app = useAppContext();
  const [, navigate] = useLocation();
  const selectedRoot = app.roots.find((root) => root.key === app.selectedRootKey);
  const selectedEntries = app.entries.filter(
    (entry) => entry.type === 'file' && app.selectedPaths.has(entry.rel_path),
  );
  const subtitleLanguages = useMemo(() => uniqueLanguages(app.staged), [app.staged]);
  const selectedStageCount = app.staged.filter((item) => item.selected).length;

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
    app.showToast('Added to staging.', 'success');
  };

  return (
    <div className="form-container">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-zinc-50">New Conversion</h1>
        <p className="text-xs text-zinc-400 mt-1">
          Select media files, choose encoding profile, and queue jobs.
        </p>
      </div>
      <div className="form-panel">
        <span className="form-section-title border-b pb-2">1. Source Browser</span>
        <div className="browser-controls">
          <select
            className="form-input root-select"
            value={app.selectedRootKey}
            onChange={(event) => app.setSelectedRootKey(event.target.value)}
            aria-label="Media root"
          >
            {app.roots.length ? (
              app.roots.map((root) => (
                <option key={root.key} value={root.key}>
                  {root.label}
                </option>
              ))
            ) : (
              <option value="">No roots</option>
            )}
          </select>
          <div className="input-wrapper">
            <input
              className="form-input has-icon"
              placeholder="Search files..."
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
            Find
          </button>
        </div>

        <div className="path-bar" aria-label="Current folder">
          <button className="path-btn" onClick={() => void app.openPath('', '')}>
            Root
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
            <div className="p-4 text-center text-zinc-500">Loading...</div>
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
            <div className="p-4 text-center text-zinc-500">No media found.</div>
          )}
        </div>
        <div className="flex justify-end mt-2">
          <button
            className="btn btn-primary"
            onClick={addSelected}
            disabled={!selectedEntries.length}
          >
            Add Selected to Stage
          </button>
        </div>

        {app.staged.length > 0 && (
          <>
            <span className="form-section-title border-b pb-2 mt-4">
              2. Staged Files ({app.staged.length})
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
                    aria-label={`Remove ${item.name} from staging`}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          </>
        )}

        <span className="form-section-title border-b pb-2 mt-4">3. Export Options</span>
        <div className="form-grid">
          <ExportSelect
            label="Video Format"
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
            label="Audio"
            value={app.settings.audio_export}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                audio_export: value as ExportSettings['audio_export'],
              }))
            }
            options={[
              ['copy', 'Copy Original'],
              ['aac', 'AAC'],
              ['mp3', 'MP3'],
              ['opus', 'Opus'],
            ]}
          />
          <ExportSelect
            label="Subtitles"
            value={app.settings.subtitle_export}
            onChange={(value) =>
              app.setSettings((current) => ({
                ...current,
                subtitle_export: value as ExportSettings['subtitle_export'],
              }))
            }
            options={[
              ['none', 'None'],
              ['embedded', 'Embedded'],
              ['separate_srt', 'Separate SRT'],
            ]}
          />
          <ExportSelect
            label="Language Preference"
            value={app.settings.subtitle_language}
            onChange={(value) =>
              app.setSettings((current) => ({ ...current, subtitle_language: value }))
            }
            options={[
              ['', 'Auto Detect'],
              ...subtitleLanguages.map((language) => [language, language] as [string, string]),
            ]}
          />
        </div>
        <div className="border-t border-zinc-800 pt-4 flex items-center justify-end gap-2">
          <button className="btn btn-outline" onClick={() => navigate('/dashboard')}>
            Cancel
          </button>
          <button
            className="btn btn-primary"
            onClick={() => void app.submitBatch()}
            disabled={app.submitting || selectedStageCount === 0}
          >
            <Play size={14} />
            <span>Queue {selectedStageCount} Jobs</span>
          </button>
        </div>
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
        {options.map(([optionValue, text]) => (
          <option value={optionValue} key={optionValue}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}
