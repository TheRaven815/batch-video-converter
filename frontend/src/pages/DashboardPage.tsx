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

export default function DashboardPage() {
  const app = useAppContext();
  const [, navigate] = useLocation();
  const { jobId } = useParams();
  const detailJob = jobId ? (app.jobs.find((job) => job.id === jobId) ?? null) : null;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-zinc-50">Dashboard</h1>
          <p className="text-xs text-zinc-400">Monitor conversion queue and system metrics.</p>
        </div>
        <button className="btn btn-primary" onClick={() => navigate('/convert')}>
          <Plus size={14} />
          <span>New Job</span>
        </button>
      </div>

      <div className="metrics-grid">
        {[
          ['Total Jobs', app.summary.all, 'active / done', ''],
          ['Queued', app.summary.queued, 'waiting', 'queued'],
          ['Running', app.summary.running, 'processing', 'running'],
          ['Completed', app.summary.completed, 'success', 'done'],
          ['Failed', app.summary.failed, 'errors', 'failed'],
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
                Load more jobs
              </button>
            )}
          </div>
        </div>
        <div className="dashboard-sidebar">
          <OutputsPanel
            outputs={app.outputs}
            onDownload={(filename) =>
              void downloadOutput(filename).catch(() =>
                app.showToast(`Failed to download ${filename}.`, 'error'),
              )
            }
            onClear={app.handleClearOutputs}
            onDelete={app.handleDeleteOutput}
          />
          {app.hasNextOutputs && (
            <button className="btn btn-outline load-more" onClick={app.loadMoreOutputs}>
              Load more outputs
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
