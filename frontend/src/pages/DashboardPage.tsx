import { Plus } from 'lucide-react';
import { useLocation, useParams } from 'wouter';

import { downloadOutput } from '../api';
import {
  JobControls,
  BatchPanel,
  JobDetailDrawer,
  JobList,
  OutputsPanel,
  SystemResourcesPanel,
} from '../components/ui';
import { useAppContext } from '../context/AppContext';
import { useI18n } from '../i18n';

export default function DashboardPage() {
  const app = useAppContext();
  const { t } = useI18n();
  const [, navigate] = useLocation();
  const { jobId } = useParams();
  const detailJob = jobId ? (app.jobs.find((job) => job.id === jobId) ?? null) : null;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-50">
            {t('dashboard.title')}
          </h1>
          <p className="text-xs text-zinc-400">{t('dashboard.subtitle')}</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/convert')}>
          <Plus size={14} />
          <span>{t('dashboard.newJob')}</span>
        </button>
      </div>

      <div className="metrics-grid">
        {[
          [t('dashboard.totalJobs'), app.summary.all, t('dashboard.activeDone'), ''],
          [t('dashboard.queued'), app.summary.queued, t('dashboard.waiting'), 'queued'],
          [t('dashboard.running'), app.summary.running, t('dashboard.processing'), 'running'],
          [t('dashboard.completed'), app.summary.completed, t('dashboard.success'), 'done'],
          [t('dashboard.failed'), app.summary.failed, t('dashboard.errors'), 'failed'],
        ].map(([title, value, caption, className]) => (
          <div className={`metric-card ${className}`} key={title}>
            <span className="metric-title">{title}</span>
            <div className="metric-value-row">
              <span className="text-2xl font-semibold font-mono tracking-tight text-zinc-100">
                {value}
              </span>
              <span className="text-2xs text-zinc-500">{caption}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-main">
          <div className="panel">
            <JobControls
              filters={app.filters}
              setFilters={app.setFilters}
              selectedCount={app.selectedJobIds.size}
              setSelectedJobIds={app.setSelectedJobIds}
              runBulkAction={app.runBulkAction}
            />
            <JobList
              jobsLoading={app.jobsLoading}
              jobs={app.filteredJobs}
              selectedJobIds={app.selectedJobIds}
              setSelectedJobIds={app.setSelectedJobIds}
              onOpenDetail={(job) => navigate(`/jobs/${encodeURIComponent(job.id)}`)}
              onCancelJob={app.handleCancelJob}
              onDeleteJob={app.handleDeleteJob}
            />
            {app.hasNextJobs && (
              <button className="btn btn-outline load-more" onClick={app.loadMoreJobs}>
                {t('dashboard.loadMoreJobs')}
              </button>
            )}
          </div>
        </div>
        <div className="dashboard-sidebar">
          <OutputsPanel
            outputs={app.outputs}
            onDownload={(filename) =>
              void downloadOutput(filename).catch(() =>
                app.showToast(t('toast.downloadFailed', { filename }), 'error'),
              )
            }
            onClear={app.handleClearOutputs}
            onDelete={app.handleDeleteOutput}
          />
          {app.hasNextOutputs && (
            <button className="btn btn-outline load-more" onClick={app.loadMoreOutputs}>
              {t('dashboard.loadMoreOutputs')}
            </button>
          )}
          <SystemResourcesPanel workerHealth={app.workerHealth} />
        </div>
      </div>
      <BatchPanel batches={app.batches} onAction={app.runBatchAction} />

      <JobDetailDrawer job={detailJob} onClose={() => navigate('/dashboard')} />
    </>
  );
}
