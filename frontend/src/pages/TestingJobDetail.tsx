import { useState, useEffect, useMemo, useRef, Fragment } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeftIcon,
  PlayIcon,
  StopIcon,
  ArrowPathIcon,
  DocumentTextIcon,
  ChevronRightIcon,
  ServerIcon,
  Square3Stack3DIcon,
  UserIcon,
  CalendarIcon,
  CubeIcon,
  CodeBracketIcon,
  ClockIcon,
  ExclamationTriangleIcon,
} from '@heroicons/react/24/outline'
import {
  CheckCircleIcon,
  XCircleIcon,
  MinusCircleIcon,
  ForwardIcon,
} from '@heroicons/react/24/solid'
import clsx from 'clsx'
import { isAdmin, isAuthenticated } from '../stores/authStore'
import {
  useFournosJob,
  useCancelJob,
  useRerunJob,
  useUpdateJobWorkItems,
  useUpdateJobRunGroups,
} from '../hooks/useFournos'
import type { PipelineStage, FournosPod } from '../types'
import { workItemFromInput } from '../utils/workItems'
import RunGroupSelector from '../components/RunGroupSelector'

function formatDuration(startStr: string | null, endStr: string | null): string {
  if (!startStr) return ''
  const start = new Date(startStr).getTime()
  const end = endStr ? new Date(endStr).getTime() : Date.now()
  const s = Math.max(0, Math.floor((end - start) / 1000))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

// ─── Pipeline Timeline ──────────────────────────────────────────────────
//
// Every step this pipeline will ever run is shown up front (sourced from
// Forge's Tekton Pipeline definition — see backend/app/services/
// pipeline_definitions.py) — not just the ones that have started so far —
// so watching a live job shows the full plan, not a list that grows one
// item at a time.

const STAGE_STYLES: Record<string, { ring: string; chip: string; text: string; icon: 'check' | 'x' | 'minus' | 'forward' | 'spin' | 'none' }> = {
  Succeeded: { ring: 'ring-green-200', chip: 'bg-green-500', text: 'text-green-700', icon: 'check' },
  Failed: { ring: 'ring-red-200', chip: 'bg-red-500', text: 'text-red-700', icon: 'x' },
  Running: { ring: 'ring-blue-200', chip: 'bg-blue-500', text: 'text-blue-700', icon: 'spin' },
  Cancelled: { ring: 'ring-gray-200', chip: 'bg-gray-400', text: 'text-gray-500', icon: 'minus' },
  Skipped: { ring: 'ring-gray-200', chip: 'bg-gray-300', text: 'text-gray-400', icon: 'forward' },
  NotRun: { ring: 'ring-gray-200', chip: 'bg-gray-300', text: 'text-gray-400', icon: 'minus' },
  Unknown: { ring: 'ring-amber-200', chip: 'bg-amber-400', text: 'text-amber-700', icon: 'minus' },
  Pending: { ring: 'ring-gray-200', chip: 'bg-white border-2 border-gray-300', text: 'text-gray-400', icon: 'none' },
}

function StageChip({ stage, index }: { stage: PipelineStage; index: number }) {
  const style = STAGE_STYLES[stage.status] || STAGE_STYLES.Pending
  const outcomeLabel = stage.outcome === 'infrastructure_error'
    ? 'infrastructure error'
    : stage.outcome === 'not_run'
      ? 'not run'
      : stage.outcome || (stage.status === 'Pending' ? 'queued' : stage.status)
  return (
    <div
      className={clsx(
        'group relative flex items-center gap-2.5 rounded-xl border bg-white px-3 py-2 shadow-sm transition-shadow hover:shadow-md',
        stage.status === 'Pending' ? 'border-dashed border-gray-200' : 'border-gray-200'
      )}
    >
      <span className={clsx('flex h-7 w-7 shrink-0 items-center justify-center rounded-full ring-4', style.chip, style.ring)}>
        {style.icon === 'check' && <CheckCircleIcon className="h-7 w-7 -m-0.5 text-white" />}
        {style.icon === 'x' && <XCircleIcon className="h-7 w-7 -m-0.5 text-white" />}
        {style.icon === 'minus' && <MinusCircleIcon className="h-7 w-7 -m-0.5 text-white" />}
        {style.icon === 'forward' && <ForwardIcon className="h-3.5 w-3.5 text-white" />}
        {style.icon === 'spin' && <span className="block h-2.5 w-2.5 rounded-full bg-white animate-pulse" />}
        {style.icon === 'none' && <span className="text-xs font-semibold text-gray-400">{index + 1}</span>}
      </span>
      <div className="min-w-0 max-w-xs">
        <p className={clsx('truncate text-xs font-semibold', stage.status === 'Pending' ? 'text-gray-400' : 'text-gray-800')}>
          {stage.displayName}
        </p>
        <p className={clsx('text-[10px] font-medium uppercase tracking-wide', style.text)}>
          {outcomeLabel}
          {stage.startTime && stage.status !== 'Pending' && (
            <span className="ml-1 font-normal normal-case text-gray-400">· {formatDuration(stage.startTime, stage.completionTime)}</span>
          )}
        </p>
        {stage.reason && stage.outcome && !['succeeded', 'skipped', 'not_run'].includes(stage.outcome) && (
          <p className="mt-1 line-clamp-2 text-[11px] font-normal text-gray-500" title={stage.reason}>
            {stage.failedStep && <span className="font-medium">{stage.failedStep}: </span>}
            {stage.reason}
          </p>
        )}
      </div>
      {stage.status === 'Running' && (
        <span className="absolute -inset-px rounded-xl ring-2 ring-blue-400 animate-pulse pointer-events-none" />
      )}
    </div>
  )
}

function PipelineTimeline({ stages }: { stages: PipelineStage[] }) {
  if (!stages.length) return null

  const mainStages = stages.filter((s) => !s.finally)
  const finallyStages = stages.filter((s) => s.finally)
  const doneCount = stages.filter((s) => s.status === 'Succeeded' || s.status === 'Skipped').length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-900">Pipeline Timeline</h3>
        <span className="text-xs text-gray-400">{doneCount}/{stages.length} steps complete</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {mainStages.map((stage, i) => (
          <Fragment key={stage.name}>
            <StageChip stage={stage} index={i} />
            {i < mainStages.length - 1 && <ChevronRightIcon className="h-4 w-4 shrink-0 text-gray-300" />}
          </Fragment>
        ))}
      </div>

      {finallyStages.length > 0 && (
        <div className="space-y-2 border-t border-dashed border-gray-200 pt-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-gray-400">Finally (always runs)</p>
          <div className="flex flex-wrap items-center gap-2">
            {finallyStages.map((stage, i) => (
              <Fragment key={stage.name}>
                <StageChip stage={stage} index={mainStages.length + i} />
                {i < finallyStages.length - 1 && <ChevronRightIcon className="h-4 w-4 shrink-0 text-gray-300" />}
              </Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ─── Pods ───────────────────────────────────────────────────────────────
// Only shown while a job is live — once it's archived to history the pods
// themselves are long gone from the cluster, so there's nothing to select
// or stream logs from anymore (see isHistorical gating in the tab bar
// below).

function PodsPanel({
  pods,
  onSelectPod,
  selectedPod,
}: {
  pods: FournosPod[]
  onSelectPod: (name: string) => void
  selectedPod: string
}) {
  if (!pods.length) return null

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-gray-900">Pods ({pods.length})</h3>
      <div className="space-y-1.5">
        {pods.map((pod) => {
          const failed = pod.exit_code != null && pod.exit_code !== 0
          const completed = pod.term_reason === 'Completed' || pod.exit_code === 0
          const active = selectedPod === pod.name
          return (
            <button
              key={pod.name}
              onClick={() => onSelectPod(pod.name)}
              className={clsx(
                'w-full rounded-lg border px-3 py-2.5 text-left text-xs transition-colors',
                active ? 'border-indigo-300 bg-indigo-50 ring-1 ring-indigo-200' : 'border-gray-200 hover:border-gray-300 hover:bg-gray-50'
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <span
                    className={clsx(
                      'h-2 w-2 shrink-0 rounded-full',
                      pod.ready || completed
                        ? 'bg-green-500'
                        : pod.phase === 'Running'
                        ? 'bg-blue-500 animate-pulse'
                        : failed
                        ? 'bg-red-500'
                        : 'bg-gray-300'
                    )}
                  />
                  <span className="truncate font-mono text-gray-700">{pod.name}</span>
                </div>
                <span className="shrink-0 text-gray-400">{pod.age_minutes}m</span>
              </div>
              {(pod.term_reason || pod.exit_code != null) && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5 pl-4">
                  {pod.term_reason && (
                    <span
                      className={clsx(
                        'rounded px-1.5 py-0.5 text-[10px] font-medium',
                        pod.term_reason === 'Completed' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
                      )}
                    >
                      {pod.term_reason}
                    </span>
                  )}
                  {pod.exit_code != null && (
                    <code className={clsx('text-[10px]', pod.exit_code !== 0 ? 'text-red-600' : 'text-gray-400')}>
                      exit {pod.exit_code}
                    </code>
                  )}
                </div>
              )}
              {pod.term_message && failed && (
                <p className="mt-1 truncate pl-4 text-[11px] text-red-600" title={pod.term_message}>
                  {pod.term_message}
                </p>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ─── Log Viewer ─────────────────────────────────────────────────────────

function LogViewer({ jobName, podName }: { jobName: string; podName: string }) {
  const [lines, setLines] = useState<string[]>([])
  const [connected, setConnected] = useState(false)
  const logEndRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    setLines([])
    setConnected(false)
    const evtSource = new EventSource(`/api/v1/fournos/jobs/${jobName}/logs/${podName}`)
    evtSource.onopen = () => setConnected(true)
    evtSource.onmessage = (event) => {
      setLines((prev) => [...prev, event.data])
    }
    evtSource.onerror = () => {
      setConnected(false)
      evtSource.close()
    }
    return () => evtSource.close()
  }, [jobName, podName])

  useEffect(() => {
    if (autoScroll) logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines, autoScroll])

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
          <DocumentTextIcon className="h-4 w-4" />
          Logs <span className="text-gray-300">—</span> <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-600">{podName}</span>
        </h3>
        <div className="flex items-center gap-3 text-xs">
          <span className={clsx('flex items-center gap-1 font-medium', connected ? 'text-green-600' : 'text-gray-400')}>
            <span className={clsx('h-1.5 w-1.5 rounded-full', connected ? 'bg-green-500 animate-pulse' : 'bg-gray-300')} />
            {connected ? 'Live' : 'Disconnected'}
          </span>
          <label className="flex items-center gap-1.5 text-gray-500">
            <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} className="h-3 w-3 rounded border-gray-300 text-indigo-600" />
            Auto-scroll
          </label>
        </div>
      </div>
      <div className="h-96 overflow-y-auto rounded-lg bg-gray-950 p-4 font-mono text-xs leading-relaxed text-gray-300 shadow-inner">
        {lines.length === 0 && !connected && <span className="text-gray-500">Waiting for logs...</span>}
        {lines.map((line, i) => (
          <div key={i} className="hover:bg-gray-800/50">{line}</div>
        ))}
        <div ref={logEndRef} />
      </div>
    </div>
  )
}

// ─── Main Component ─────────────────────────────────────────────────────

function jobLoadErrorMessage(error: unknown): string {
  const status = (error as { response?: { status?: number } } | null)?.response?.status
  return status === 404
    ? 'Job not found'
    : 'Unable to load job details. Please retry.'
}

export default function TestingJobDetail() {
  const { name } = useParams<{ name: string }>()
  const { data, isLoading, error, refetch } = useFournosJob(name)
  const cancelJob = useCancelJob()
  const rerunJob = useRerunJob()
  const updateRunGroups = useUpdateJobRunGroups(name)
  const updateWorkItems = useUpdateJobWorkItems(name)
  const [workItemDraft, setWorkItemDraft] = useState('')
  const [selectedPod, setSelectedPod] = useState('')
  const [autoSelected, setAutoSelected] = useState(false)
  const [activeTab, setActiveTab] = useState<'timeline' | 'pods' | 'spec'>('timeline')

  const pods = useMemo(() => data?.pods ?? [], [data?.pods])
  const phase = (data?.job.status as Record<string, unknown> | undefined)?.phase as string | undefined
  // Once a job is archived to history its pods are long gone from the
  // cluster — no point offering a tab that can only ever say "no pods".
  const isHistorical = data?.job.source === 'history'

  useEffect(() => {
    if (autoSelected || selectedPod || !pods.length) return
    if (phase !== 'Failed') return
    const failedPod = pods.find((p) => p.exit_code != null && p.exit_code !== 0) || pods.find((p) => p.term_reason && p.term_reason !== 'Completed')
    if (failedPod) {
      setSelectedPod(failedPod.name)
      setAutoSelected(true)
    }
  }, [pods, phase, autoSelected, selectedPod])

  useEffect(() => {
    if (isHistorical && activeTab === 'pods') setActiveTab('timeline')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isHistorical])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <ArrowPathIcon className="h-8 w-8 text-gray-300 animate-spin" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="space-y-4">
        <Link to="/testing" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
          <ArrowLeftIcon className="h-4 w-4" /> Back
        </Link>
        <div className="card p-8 text-center text-gray-500">
          <p>{error ? jobLoadErrorMessage(error) : 'Job not found'}</p>
          {error && (error as { response?: { status?: number } }).response?.status !== 404 && (
            <button
              type="button"
              onClick={() => void refetch()}
              className="mt-3 inline-flex items-center gap-1 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
            >
              <ArrowPathIcon className="h-4 w-4" /> Retry
            </button>
          )}
        </div>
      </div>
    )
  }

  const { job, stages, forge_info, failure_summary, forge_execution } = data
  const workItems = data.work_items ?? []
  const runGroups = data.run_groups ?? []
  const forgeExecution = forge_execution ?? { images: [], gitVersions: [], observedAt: null }
  const meta = job.metadata as Record<string, unknown>
  const spec = job.spec as Record<string, unknown>
  const status = job.status as Record<string, unknown>
  const displayPhase = (status.phase as string) || 'Unknown'
  const isRunning = ['Running', 'Pending', 'Admitted', 'Resolving'].includes(displayPhase)

  const PHASE_BANNER: Record<string, string> = {
    Running: 'from-blue-500 to-indigo-600',
    Succeeded: 'from-green-500 to-emerald-600',
    Failed: 'from-red-500 to-rose-600',
    Stopped: 'from-yellow-400 to-amber-500',
    Pending: 'from-gray-400 to-gray-500',
    Resolving: 'from-purple-500 to-fuchsia-600',
    Admitted: 'from-indigo-500 to-purple-600',
  }

  return (
    <div className="space-y-6">
      <Link to="/testing" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeftIcon className="h-4 w-4" /> Back to Testing
      </Link>

      {/* Header banner */}
      <div className={clsx('rounded-2xl bg-gradient-to-r p-5 text-white shadow-sm', PHASE_BANNER[displayPhase] || 'from-gray-500 to-gray-600')}>
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white/20 px-2.5 py-1 text-xs font-semibold backdrop-blur-sm">
                {isRunning && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-white" />}
                {displayPhase}
              </span>
              {forge_info.project && (
                <span className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-1 text-xs font-medium">
                  <Square3Stack3DIcon className="h-3.5 w-3.5" /> {forge_info.project}
                </span>
              )}
              {job.source === 'history' && <span className="text-xs text-white/70">(from history)</span>}
            </div>
            <h1 className="mt-2 truncate font-mono text-lg font-bold sm:text-xl">{name}</h1>
            {forge_info.args?.length > 0 && (
              <p className="mt-1 truncate text-sm text-white/80">{forge_info.args.join(' ')}</p>
            )}
          </div>

          {isAdmin() && (
            <div className="flex shrink-0 gap-2">
              {isRunning && (
                <button onClick={() => { if (confirm(`Cancel "${name}"?`)) cancelJob.mutate(name!) }} className="inline-flex items-center gap-1 rounded-md bg-white/15 px-3 py-1.5 text-sm font-medium text-white backdrop-blur-sm hover:bg-white/25">
                  <StopIcon className="h-4 w-4" /> Cancel
                </button>
              )}
              <button onClick={() => rerunJob.mutate(name!)} disabled={rerunJob.isPending} className="inline-flex items-center gap-1 rounded-md bg-white px-3 py-1.5 text-sm font-medium text-gray-900 shadow-sm hover:bg-white/90 disabled:opacity-60">
                <PlayIcon className="h-4 w-4" /> Re-run
              </button>
            </div>
          )}
        </div>
      </div>

      {failure_summary && (
        <div className={clsx(
          'rounded-xl border p-4',
          failure_summary.outcome === 'cancelled'
            ? 'border-amber-200 bg-amber-50'
            : 'border-red-200 bg-red-50'
        )}>
          <div className="flex items-start gap-3">
            <ExclamationTriangleIcon className={clsx(
              'mt-0.5 h-5 w-5 shrink-0',
              failure_summary.outcome === 'cancelled' ? 'text-amber-600' : 'text-red-600'
            )} />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-gray-900">
                {failure_summary.outcome === 'infrastructure_error'
                  ? 'Infrastructure error'
                  : failure_summary.outcome === 'cancelled'
                    ? 'Execution cancelled'
                    : failure_summary.outcome === 'unknown'
                      ? 'Failure details unavailable'
                      : 'Test or execution failure'}
              </p>
              <p className="mt-1 text-sm text-gray-700">{failure_summary.reason}</p>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
                {failure_summary.stageDisplayName && <span>Stage: <strong>{failure_summary.stageDisplayName}</strong></span>}
                {failure_summary.step && <span>Step: <strong>{failure_summary.step}</strong></span>}
                <span>Source: <strong>{failure_summary.source.replace(/_/g, ' ')}</strong></span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
        {[
          { label: 'Namespace', value: (meta.namespace as string) || '-', icon: CubeIcon },
          { label: 'Cluster', value: (spec.cluster as string) || '-', icon: ServerIcon },
          { label: 'Pipeline', value: (spec.pipeline as string) || '-', icon: Square3Stack3DIcon },
          { label: 'Owner', value: (spec.owner as string) || (isAuthenticated() ? '-' : 'Sign in to view'), icon: UserIcon },
          { label: 'Created', value: meta.creationTimestamp ? new Date(meta.creationTimestamp as string).toLocaleString() : '-', icon: CalendarIcon },
        ].map(({ label, value, icon: Icon }) => (
          <div key={label} className="card flex items-start gap-3 p-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-100 text-gray-500">
              <Icon className="h-5 w-5" />
            </span>
            <div className="min-w-0">
              <p className="text-xs text-gray-500">{label}</p>
              <p className="truncate text-sm font-semibold text-gray-900" title={value}>{value}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Forge info & MLflow */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-5">
        {(forge_info.pr_url || forge_info.repository || forge_info.requested_sha) && (
          <div className="card p-4">
            <p className="text-xs text-gray-500 mb-1">Requested source revision</p>
            {forge_info.pr_url ? (
              <a href={forge_info.pr_url} target="_blank" rel="noopener noreferrer" className="text-sm text-indigo-600 hover:text-indigo-800 hover:underline">
                #{forge_info.pr_number}: {forge_info.pr_title}
              </a>
            ) : (
              <p className="text-sm text-gray-700">Recorded source revision</p>
            )}
            {forge_info.repository && (
              <p className="mt-2 text-xs text-gray-500">
                Repository: <span className="font-medium text-gray-700">{forge_info.repository}</span>
              </p>
            )}
            {forge_info.head_branch && (
              <p className="mt-1 text-xs text-gray-500">
                Branch: <code className="text-gray-700">{forge_info.head_branch}</code>
              </p>
            )}
            {forge_info.requested_sha && (
              <p className="mt-1 text-xs text-gray-500">
                Selected commit:{' '}
                {forge_info.repository ? (
                  <a
                    href={`https://github.com/${forge_info.repository}/commit/${forge_info.requested_sha}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-mono text-indigo-600 hover:text-indigo-800 hover:underline"
                  >
                    {forge_info.requested_sha.slice(0, 12)}
                  </a>
                ) : (
                  <code className="text-gray-700">{forge_info.requested_sha.slice(0, 12)}</code>
                )}
              </p>
            )}
          </div>
        )}
        <div className="card p-4">
          <p className="text-xs text-gray-500 mb-1">Forge execution</p>
          {forgeExecution.gitVersions.length > 0 ? (
            <div className="space-y-1">
              {forgeExecution.gitVersions.map((item) => (
                <p key={`${item.version}-${item.artifactPath}`} className="text-xs text-gray-500" title={item.artifactPath}>
                  Version used: <code className="font-medium text-gray-800">{item.version}</code>
                </p>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-400">Version not yet observed</p>
          )}
          {forgeExecution.images.map((item) => (
            <div key={`${item.image}-${item.imageID}`} className="mt-2 space-y-1 text-xs text-gray-500">
              <p className="break-all">Image: <code className="text-gray-700">{item.image || '-'}</code></p>
              <p className="break-all">Image ID: <code className="text-gray-700">{item.imageID || '-'}</code></p>
            </div>
          ))}
          <p className="mt-2 text-xs text-gray-400">
            Evidence: {(data.forge_provenance_state || 'pending').replace(/_/g, ' ')}
          </p>
        </div>
        <div className="card p-4">
          <p className="text-xs text-gray-500 mb-1">MLflow</p>
          {job.mlflow_url ? (
            <a href={job.mlflow_url} target="_blank" rel="noopener noreferrer" className="text-sm text-indigo-600 hover:text-indigo-800 hover:underline">
              View MLflow Run
            </a>
          ) : (
            <p className="text-sm text-gray-300">-</p>
          )}
        </div>
        {isAuthenticated() && (
          <div className="card p-4">
            <p className="text-xs text-gray-500 mb-1">Work items</p>
            {workItems.length > 0 ? (
              <div className="space-y-2">
                {workItems.map((item) => (
                  <div key={`${item.provider}-${item.key}`} className="flex items-center justify-between gap-2">
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-medium text-indigo-600 hover:text-indigo-800 hover:underline"
                    >
                      {item.key}
                    </a>
                    {data.can_edit_work_items && (
                      <button
                        type="button"
                        onClick={() => updateWorkItems.mutate(workItems.filter((candidate) => candidate.key !== item.key || candidate.provider !== item.provider))}
                        disabled={updateWorkItems.isPending}
                        className="text-xs text-gray-400 hover:text-red-600 disabled:opacity-50"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-gray-300">-</p>
            )}
            {data.can_edit_work_items && (
              <div className="mt-3 flex gap-2">
                <input
                  type="text"
                  value={workItemDraft}
                  onChange={(event) => setWorkItemDraft(event.target.value)}
                  placeholder="PROJECT-123 or Jira URL"
                  aria-label="Add Jira work item"
                  className="min-w-0 flex-1 rounded-md border-gray-300 px-2 py-1 text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
                />
                <button
                  type="button"
                  disabled={!workItemDraft.trim() || updateWorkItems.isPending}
                  onClick={() => {
                    const item = workItemFromInput(workItemDraft)
                    if (!item) return
                    updateWorkItems.mutate([
                      ...workItems,
                      item,
                    ], { onSuccess: () => setWorkItemDraft('') })
                  }}
                  className="rounded-md bg-indigo-600 px-2 py-1 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  Add
                </button>
              </div>
            )}
          </div>
        )}
        <div className="card p-4">
          <p className="mb-2 text-xs text-gray-500">Run grouping</p>
          {data.can_edit_run_groups ? (
            <RunGroupSelector
              selectedIds={runGroups.map((group) => group.id)}
              selectedGroups={runGroups}
              onChange={(ids) => updateRunGroups.mutate(ids)}
            />
          ) : runGroups.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {runGroups.map((group) => (
                <span key={group.id} className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700">
                  <span className="text-indigo-400">{group.group_type}</span> {group.display_name}
                  {group.archived && <span className="text-gray-400"> (archived)</span>}
                </span>
              ))}
            </div>
          ) : (
            <p className="text-sm text-gray-300">-</p>
          )}
        </div>
      </div>

      {/* Tab bar */}
      <div className="border-b border-gray-200">
        <nav className="-mb-px flex space-x-8">
          {[
            { id: 'timeline' as const, label: 'Pipeline Timeline', icon: ClockIcon },
            ...(isHistorical ? [] : [{ id: 'pods' as const, label: `Pods (${pods.length})`, icon: CubeIcon }]),
            { id: 'spec' as const, label: 'FournosJob Spec', icon: CodeBracketIcon },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'whitespace-nowrap border-b-2 py-3 px-1 text-sm font-medium flex items-center gap-1.5',
                activeTab === tab.id
                  ? 'border-indigo-500 text-indigo-600'
                  : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700'
              )}
            >
              <tab.icon className="h-4 w-4" />
              {tab.label}
            </button>
          ))}
        </nav>
      </div>

      {/* Pipeline Timeline tab */}
      {activeTab === 'timeline' && (
        stages.length > 0 ? (
          <div className="card p-5">
            <PipelineTimeline stages={stages} />
          </div>
        ) : (
          <div className="card p-8 text-center text-sm text-gray-400">No pipeline stages to show yet.</div>
        )
      )}

      {/* Pods tab — live jobs only, see isHistorical */}
      {activeTab === 'pods' && !isHistorical && (
        pods.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            <div className="card p-4">
              <PodsPanel pods={pods} onSelectPod={setSelectedPod} selectedPod={selectedPod} />
            </div>
            <div className="card p-4 lg:col-span-2">
              {selectedPod ? (
                <LogViewer jobName={name!} podName={selectedPod} />
              ) : (
                <div className="flex items-center justify-center h-48 text-sm text-gray-400">
                  Select a pod to view logs
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="card p-8 text-center text-sm text-gray-400">No pods for this job yet.</div>
        )
      )}

      {/* Spec tab */}
      {activeTab === 'spec' && (
        <div className="card">
          <pre className="p-4 text-xs overflow-x-auto bg-gray-50 font-mono text-gray-600 rounded-xl">
            {JSON.stringify(spec, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}
