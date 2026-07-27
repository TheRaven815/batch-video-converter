import { createContext, useContext } from 'react';
import type { Dispatch, SetStateAction } from 'react';

import type {
  ExportSettings,
  BatchSummaryDto,
  JobFilters,
  JobRecord,
  JobStatus,
  MediaBrowseEntryDto,
  MediaRootDto,
  OutputFileDto,
  StagedServerFile,
  WorkerHealthResponse,
} from '../models';
import type { LocalPreset } from '../utils/constants';

export type ToastKind = 'success' | 'error' | 'info';

export interface AppContextValue {
  roots: MediaRootDto[];
  selectedRootKey: string;
  setSelectedRootKey: Dispatch<SetStateAction<string>>;
  currentPath: string;
  browserQuery: string;
  setBrowserQuery: Dispatch<SetStateAction<string>>;
  entries: MediaBrowseEntryDto[];
  selectedPaths: Set<string>;
  setSelectedPaths: Dispatch<SetStateAction<Set<string>>>;
  staged: StagedServerFile[];
  setStaged: Dispatch<SetStateAction<StagedServerFile[]>>;
  browserLoading: boolean;
  openPath: (path?: string, query?: string) => Promise<void>;
  settings: ExportSettings;
  setSettings: Dispatch<SetStateAction<ExportSettings>>;
  submitting: boolean;
  submitBatch: () => Promise<void>;
  jobs: JobRecord[];
  filteredJobs: JobRecord[];
  jobsLoading: boolean;
  selectedJobIds: Set<string>;
  setSelectedJobIds: Dispatch<SetStateAction<Set<string>>>;
  filters: JobFilters;
  setFilters: Dispatch<SetStateAction<JobFilters>>;
  summary: Record<JobStatus | 'all', number>;
  outputs: OutputFileDto[];
  batches: BatchSummaryDto[];
  workerHealth: WorkerHealthResponse | null;
  hasNextJobs: boolean;
  hasNextOutputs: boolean;
  loadMoreJobs: () => void;
  loadMoreOutputs: () => void;
  runBulkAction: (action: 'cancel' | 'start' | 'archive' | 'delete') => void;
  handleCancelJob: (id: string) => void;
  handleDeleteJob: (id: string) => void;
  handleClearOutputs: () => void;
  handleDeleteOutput: (filename: string) => void;
  runBatchAction: (batchId: string, action: 'cancel' | 'retry' | 'archive' | 'delete') => void;
  showToast: (message: string, kind?: ToastKind) => void;
  presets: LocalPreset[];
  editingPresetId: string | null;
  presetName: string;
  setPresetName: Dispatch<SetStateAction<string>>;
  presetDescription: string;
  setPresetDescription: Dispatch<SetStateAction<string>>;
  presetSettings: ExportSettings;
  setPresetSettings: Dispatch<SetStateAction<ExportSettings>>;
  resetPresetForm: () => void;
  savePreset: () => void;
  startEditPreset: (preset: LocalPreset) => void;
  deletePreset: (presetId: string) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export const AppProvider = AppContext.Provider;

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) throw new Error('useAppContext must be used inside AppProvider');
  return context;
}
