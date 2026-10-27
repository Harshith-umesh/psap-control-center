import { ArrowPathIcon, QueueListIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { Link } from 'react-router-dom'
import type { ClusterOverview, FournosJobSummary } from '../types'
import { groupClusterActivity } from '../utils/clusterActivity'

function gpuDescription(job: FournosJobSummary): string | null {
  const parts: string[] = []
  if (job.gpu_type) parts.push(job.gpu_type.toUpperCase())
  if (job.gpu_count != null) parts.push(`${job.gpu_count} GPU${job.gpu_count === 1 ? '' : 's'}`)
  return parts.length ? parts.join(' · ') : null
}

function localStartTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

function JobRow({ job, label, tone }: { job: FournosJobSummary; label: string; tone: string }) {
  const gpu = gpuDescription(job)
  const step = job.current_step?.displayName || job.current_step?.name
  const scheduledAt = job.scheduled_start_time ? Date.parse(job.scheduled_start_time) : Number.NaN
  const detail = Number.isFinite(scheduledAt) && scheduledAt > Date.now()
    ? `Starts ${localStartTime(job.scheduled_start_time as string)}`
    : step || job.preset

  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1 border-t border-gray-100 py-2 first:border-t-0">
      <div className="min-w-0 flex-1">
        <Link to={`/testing/jobs/${encodeURIComponent(job.name)}`} className="break-all text-sm font-medium text-indigo-700 hover:underline">
          {job.name}
        </Link>
        <p className="mt-0.5 break-words text-xs text-gray-500">
          {[job.project, job.owner, gpu].filter(Boolean).join(' · ') || 'Job details unavailable'}
        </p>
        {detail && <p className="mt-0.5 break-words text-xs text-gray-500">{detail}</p>}
      </div>
      <span className={clsx('shrink-0 rounded-full px-2 py-0.5 text-xs font-medium', tone)}>{label}</span>
    </li>
  )
}

export default function AcceleratorQueueSummary({
  cluster,
  accelerator,
  gpuCapacity,
  overview,
  isLoading,
  isFetching,
  isError,
  onRefresh,
}: {
  cluster: string
  accelerator?: string
  gpuCapacity?: string
  overview?: ClusterOverview
  isLoading: boolean
  isFetching: boolean
  isError: boolean
  onRefresh: () => void
}) {
  const groups = groupClusterActivity(overview?.current_jobs || [])
  const totalActive = groups.queued.length + groups.running.length + groups.other.length
  const rows: Array<{ job: FournosJobSummary; label: string; tone: string }> = [
    ...groups.running.map((job) => ({ job, label: 'Running', tone: 'bg-blue-50 text-blue-700' })),
    ...groups.queued.map((job) => ({ job, label: job.status, tone: 'bg-amber-50 text-amber-800' })),
    ...groups.scheduled.map((job) => ({ job, label: 'Scheduled', tone: 'bg-violet-50 text-violet-700' })),
    ...groups.other.map((job) => ({ job, label: job.status || 'Active', tone: 'bg-gray-100 text-gray-700' })),
  ]

  return (
    <section aria-label={`${accelerator || 'Accelerator'} queue and running jobs`} className="rounded-lg border border-indigo-100 bg-indigo-50/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <QueueListIcon className="h-4 w-4 shrink-0 text-indigo-600" aria-hidden="true" />
          <h3 className="truncate text-sm font-semibold text-gray-900">
            {accelerator ? `${accelerator.toUpperCase()} queue & activity` : 'Accelerator queue & activity'}
          </h3>
          <span className="truncate text-xs text-gray-500">{cluster}</span>
          {gpuCapacity && <span className="shrink-0 text-xs text-gray-500">{gpuCapacity} total GPUs</span>}
        </div>
        <button type="button" onClick={onRefresh} disabled={isFetching} className="inline-flex items-center gap-1 text-xs font-medium text-indigo-700 hover:text-indigo-900 disabled:opacity-50">
          <ArrowPathIcon className={clsx('h-3.5 w-3.5', isFetching && 'animate-spin')} aria-hidden="true" />
          Refresh
        </button>
      </div>

      {isLoading && !overview ? (
        <p className="mt-3 text-sm text-gray-500">Loading accelerator activity…</p>
      ) : isError ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          The Control Center could not read live Fournos job data. Check its cluster access and retry.
        </div>
      ) : (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              { label: 'Queued', value: groups.queued.length, style: 'bg-amber-50 text-amber-900' },
              { label: 'Running', value: groups.running.length, style: 'bg-blue-50 text-blue-900' },
              { label: 'Scheduled', value: groups.scheduled.length, style: 'bg-violet-50 text-violet-900' },
              { label: 'Active now', value: totalActive, style: 'bg-white text-gray-900' },
            ].map((item) => (
              <div key={item.label} className={clsx('rounded-md px-3 py-2', item.style)}>
                <div className="text-lg font-semibold leading-5">{item.value}</div>
                <div className="mt-1 text-[11px] font-medium">{item.label}</div>
              </div>
            ))}
          </div>

          {rows.length ? (
            <ul className="mt-2 max-h-64 divide-y divide-gray-100 overflow-y-auto rounded-md border border-gray-200 bg-white px-3">
              {rows.map(({ job, label, tone }) => <JobRow key={job.name} job={job} label={label} tone={tone} />)}
            </ul>
          ) : (
            <p className="mt-3 rounded-md border border-gray-200 bg-white px-3 py-3 text-sm text-gray-500">
              No jobs are queued, running, or scheduled on this accelerator.
            </p>
          )}
          <p className="mt-2 text-[11px] text-gray-500">
            Activity refreshes every 15 seconds. GPU requests are shown per job when available; activity is scoped to this cluster.
          </p>
        </>
      )}
    </section>
  )
}
