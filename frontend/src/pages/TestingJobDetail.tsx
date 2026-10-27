import { useState, useEffect, useMemo, useRef, Fragment } from 'react'
import { useParams, Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowLeftIcon,
  PlayIcon,
  StopIcon,
  ArrowPathIcon,
  DocumentTextIcon,
  ChevronRightIcon,
  ServerIcon,
  ExclamationTriangleIcon,
  PencilSquareIcon,
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
  useEditSubmitJob,
  useInferenceLogsAvailability,
  useInferenceServerLogs,
} from '../hooks/useFournos'
import type { PipelineStage, FournosPod } from '../types'
import { workItemFromInput } from '../utils/workItems'
import { buildFournosJobYaml } from '../utils/fournosJobPreview'
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

function formatDurationSeconds(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total}s`
  if (total < 3600) return `${Math.floor(total / 60)}m ${total % 60}s`
  return `${Math.floor(total / 3600)}h ${Math.floor((total % 3600) / 60)}m`
}

function formatConfigValue(value: unknown): string {
  if (typeof value === 'string') return value
  const serialized = JSON.stringify(value)
  return serialized === undefined ? String(value) : serialized
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
  const [complete, setComplete] = useState(false)
  const [issueIndex, setIssueIndex] = useState<number | null>(null)
  const [autoScroll, setAutoScroll] = useState(true)
  const autoScrollRef = useRef(true)

  useEffect(() => {
    setLines([])
    setConnected(false)
    setComplete(false)
    setIssueIndex(null)
    const encodedJob = encodeURIComponent(jobName)
    const encodedPod = encodeURIComponent(podName)
    const evtSource = new EventSource(`/api/v1/fournos/jobs/${encodedJob}/logs/${encodedPod}`)
    evtSource.onopen = () => setConnected(true)
    evtSource.addEventListener('context', (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as { lines: string[]; issue_index: number }
      setLines(payload.lines)
      setIssueIndex(payload.issue_index)
      setComplete(false)
      if (autoScrollRef.current) {
        window.requestAnimationFrame(() => {
          document.getElementById('latest-log-issue')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        })
      }
    })
    evtSource.addEventListener('complete', () => {
      setConnected(false)
      setComplete(true)
    })
    evtSource.onerror = () => {
      setConnected(false)
      evtSource.close()
    }
    return () => evtSource.close()
  }, [jobName, podName])

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-900">
          <DocumentTextIcon className="h-4 w-4" />
          Latest log issue <span className="text-gray-300">—</span> <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono text-xs text-gray-600">{podName}</span>
        </h3>
        <div className="flex items-center gap-3 text-xs">
          <span className={clsx('flex items-center gap-1 font-medium', connected ? 'text-green-600' : complete ? 'text-gray-500' : 'text-gray-400')}>
            <span className={clsx('h-1.5 w-1.5 rounded-full', connected ? 'bg-green-500 animate-pulse' : complete ? 'bg-gray-400' : 'bg-gray-300')} />
            {connected ? 'Live' : complete ? 'Complete' : 'Disconnected'}
          </span>
          <label className="flex items-center gap-1.5 text-gray-500">
            <input type="checkbox" checked={autoScroll} onChange={(e) => { autoScrollRef.current = e.target.checked; setAutoScroll(e.target.checked) }} className="h-3 w-3 rounded border-gray-300 text-indigo-600" />
            Auto-scroll
          </label>
          <a
            href={`/api/v1/fournos/jobs/${encodeURIComponent(jobName)}/logs/${encodeURIComponent(podName)}/download`}
            download
            className="rounded-md border border-gray-300 bg-white px-2.5 py-1.5 font-medium text-gray-700 hover:bg-gray-50"
          >
            Download full logs
          </a>
        </div>
      </div>
      <div className="h-96 overflow-y-auto rounded-lg bg-gray-950 p-4 font-mono text-xs leading-relaxed text-gray-300 shadow-inner">
        {lines.length === 0 && connected && <span className="text-gray-500">Waiting for the latest error or warning…</span>}
        {lines.length === 0 && complete && <span className="text-gray-500">No errors or warnings found in this pod log.</span>}
        {lines.map((line, i) => (
          <div id={i === issueIndex ? 'latest-log-issue' : undefined} key={i} className={clsx('whitespace-pre-wrap hover:bg-gray-800/50', i === issueIndex && 'border-l-2 border-red-400 bg-red-950/40 pl-2 text-red-200')}>
            {line}
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-500">Showing up to 300 lines around the latest error or warning. Download the full log for complete output.</p>
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
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const cancelJob = useCancelJob()
  const rerunJob = useRerunJob()
  const editSubmitJob = useEditSubmitJob(name || '')
  const updateRunGroups = useUpdateJobRunGroups(name)
  const updateWorkItems = useUpdateJobWorkItems(name)
  const [workItemDraft, setWorkItemDraft] = useState('')
  const [selectedPod, setSelectedPod] = useState('')
  const [autoSelected, setAutoSelected] = useState(false)
  const [editSubmitOpen, setEditSubmitOpen] = useState(false)
  const [editSpecText, setEditSpecText] = useState('')
  const [editSpecError, setEditSpecError] = useState('')

  useEffect(() => {
    if (searchParams.get('edit') !== '1' || !data) return
    const editableSpec = { ...data.job.spec }
    delete editableSpec.schedule
    delete editableSpec.scheduledStartTime
    delete editableSpec.shutdown
    setEditSpecText(JSON.stringify(editableSpec, null, 2))
    setEditSpecError('')
    setEditSubmitOpen(true)
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.delete('edit')
      return next
    }, { replace: true })
  }, [data, searchParams, setSearchParams])

  const pods = useMemo(() => data?.pods ?? [], [data?.pods])
  const phase = (data?.job.status as Record<string, unknown> | undefined)?.phase as string | undefined
  // Once a job is archived to history its pods are long gone from the cluster.
  const isHistorical = data?.job.source === 'history'
  const inferenceAvailability = useInferenceLogsAvailability(
    name,
    Boolean(data && !isHistorical && data.forge_info.project === 'rhaiis'),
  )
  const [inferenceLogsOpen, setInferenceLogsOpen] = useState(false)
  const inferenceLogs = useInferenceServerLogs(
    name,
    inferenceLogsOpen && inferenceAvailability.data?.available === true,
  )

  useEffect(() => {
    if (autoSelected || selectedPod || !pods.length) return
    if (phase !== 'Failed') return
    const failedPod = pods.find((p) => p.exit_code != null && p.exit_code !== 0) || pods.find((p) => p.term_reason && p.term_reason !== 'Completed')
    if (failedPod) {
      setSelectedPod(failedPod.name)
      setAutoSelected(true)
    }
  }, [pods, phase, autoSelected, selectedPod])

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
  const durationLabel = typeof job.duration_seconds === 'number'
    ? formatDurationSeconds(job.duration_seconds)
    : meta.creationTimestamp
      ? formatDuration(meta.creationTimestamp as string, (status.completionTime as string) || null)
      : ''
  const jobYaml = buildFournosJobYaml(job)
  const hardware = spec.hardware && typeof spec.hardware === 'object'
    ? spec.hardware as Record<string, unknown>
    : {}
  const secretRefs = Array.isArray(spec.secretRefs) ? spec.secretRefs : []

  const PHASE_BADGE: Record<string, string> = {
    Running: 'bg-blue-50 text-blue-700 ring-blue-200',
    Succeeded: 'bg-green-50 text-green-700 ring-green-200',
    Failed: 'bg-red-50 text-red-700 ring-red-200',
    Stopped: 'bg-gray-100 text-gray-600 ring-gray-200',
    Pending: 'bg-gray-100 text-gray-600 ring-gray-200',
    Resolving: 'bg-purple-50 text-purple-700 ring-purple-200',
    Admitted: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  }

  return (
    <div className="space-y-5">
      <Link to="/testing" className="inline-flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700">
        <ArrowLeftIcon className="h-4 w-4" /> All Jobs
      </Link>

      <div className="rounded-lg border border-gray-200 bg-white px-5 py-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="break-all text-xl font-semibold text-gray-900">{name}</h1>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className={clsx('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset', PHASE_BADGE[displayPhase] || 'bg-gray-100 text-gray-600 ring-gray-200')}>
                {isRunning && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}
                {displayPhase}
              </span>
              {job.source === 'history' && <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-600">Archived</span>}
              {durationLabel && <span className="text-xs text-gray-500">{durationLabel}</span>}
              {forge_info.project && <span className="rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700">{forge_info.project}</span>}
            </div>
            {forge_info.args?.length > 0 && <p className="mt-2 break-all text-sm text-gray-500">{forge_info.args.join(' ')}</p>}
          </div>
        </div>

        {isAdmin() && (
          <div className="mt-4 flex flex-wrap gap-2 border-t border-gray-100 pt-3" aria-label="Job actions">
            {isRunning && (
              <button onClick={() => { if (confirm(`Cancel "${name}"?`)) cancelJob.mutate(name!) }} className="inline-flex items-center gap-1.5 rounded-md border border-red-200 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50">
                <StopIcon className="h-4 w-4" /> Cancel Job
              </button>
            )}
            <button onClick={() => rerunJob.mutate(name!)} disabled={rerunJob.isPending} className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-60">
              <PlayIcon className="h-4 w-4" /> Run Again
            </button>
            <button
              type="button"
              onClick={() => {
                const editableSpec = { ...spec }
                delete editableSpec.schedule
                delete editableSpec.scheduledStartTime
                delete editableSpec.shutdown
                setEditSpecText(JSON.stringify(editableSpec, null, 2))
                setEditSpecError('')
                setEditSubmitOpen((open) => !open)
              }}
              className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              <PencilSquareIcon className="h-4 w-4" /> Edit &amp; Submit
            </button>
          </div>
        )}
      </div>

      {job.source === 'history' && ['Succeeded', 'Failed', 'Stopped'].includes(displayPhase) && (
        <div className={clsx(
          'rounded-lg border px-4 py-3 text-sm',
          displayPhase === 'Succeeded' ? 'border-green-200 bg-green-50 text-green-800' : displayPhase === 'Failed' ? 'border-red-200 bg-red-50 text-red-800' : 'border-gray-200 bg-gray-50 text-gray-700'
        )}>
          <span className="font-semibold">Execution {displayPhase.toLowerCase()}</span>
          {durationLabel && <span> after {durationLabel}</span>}
          {displayPhase === 'Failed' && typeof status.message === 'string' && status.message && <p className="mt-1 text-sm font-normal">{status.message}</p>}
        </div>
      )}

      {(job.mlflow_url || job.ci_artifacts_url) && (
        <div className="flex flex-wrap gap-2" aria-label="Job results">
          {job.mlflow_url && <a href={job.mlflow_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50"><span aria-hidden="true">📊</span> MLflow Results</a>}
          {job.ci_artifacts_url && <a href={job.ci_artifacts_url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-2 rounded-md border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-indigo-600 hover:bg-indigo-50"><span aria-hidden="true">📁</span> CI Artifacts</a>}
        </div>
      )}

      {editSubmitOpen && isAdmin() && (
        <form
          className="rounded-xl border border-indigo-200 bg-white p-4 shadow-sm sm:p-5"
          onSubmit={async (event) => {
            event.preventDefault()
            setEditSpecError('')
            let editedSpec: unknown
            try {
              editedSpec = JSON.parse(editSpecText)
            } catch {
              setEditSpecError('Enter a valid JSON object before submitting.')
              return
            }
            if (!editedSpec || typeof editedSpec !== 'object' || Array.isArray(editedSpec)) {
              setEditSpecError('The FournosJob spec must be a JSON object.')
              return
            }
            try {
              const result = await editSubmitJob.mutateAsync(editedSpec as Record<string, unknown>)
              setEditSubmitOpen(false)
              if (result.redirect) navigate(result.redirect)
            } catch (submitError) {
              setEditSpecError(submitError instanceof Error ? submitError.message : 'Could not submit the edited job.')
            }
          }}
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">Edit and submit as a new job</h2>
              <p className="mt-1 max-w-3xl text-xs leading-5 text-gray-500">
                Edit the JSON FournosJob spec below, then submit it as a separate run. The current job is unchanged; owner is set to your signed-in identity and the default TTL is 12h. Scheduling fields are omitted so this creates a one-off run.
              </p>
            </div>
            <button type="button" onClick={() => setEditSubmitOpen(false)} className="text-sm text-gray-500 hover:text-gray-700">Close</button>
          </div>
          <textarea
            value={editSpecText}
            onChange={(event) => setEditSpecText(event.target.value)}
            spellCheck={false}
            aria-label="Edited FournosJob spec JSON"
            className="mt-3 min-h-96 w-full rounded-md border-gray-300 font-mono text-xs shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
          />
          {editSpecError && <p role="alert" className="mt-2 text-sm text-red-600">{editSpecError}</p>}
          <div className="mt-3 flex justify-end">
            <button type="submit" disabled={editSubmitJob.isPending} className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50">
              {editSubmitJob.isPending ? 'Submitting…' : 'Submit edited job'}
            </button>
          </div>
        </form>
      )}

      {inferenceAvailability.data?.available && (
        <section className="overflow-hidden rounded-xl border-2 border-indigo-200 bg-white shadow-sm">
          <button
            type="button"
            aria-expanded={inferenceLogsOpen}
            onClick={() => setInferenceLogsOpen((open) => !open)}
            className="flex w-full flex-wrap items-center justify-between gap-3 bg-indigo-50 px-4 py-3 text-left hover:bg-indigo-100 sm:px-5"
          >
            <span className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-600 text-white">
                <ServerIcon className="h-5 w-5" />
              </span>
              <span>
                <span className="block text-sm font-semibold text-indigo-950">{inferenceLogsOpen ? 'Hide' : 'View'} inference server logs</span>
                <span className="mt-0.5 block text-xs text-indigo-700">Live RHAIIS predictor output · latest 300 lines · refreshes while open</span>
              </span>
            </span>
            <span className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white">{inferenceLogsOpen ? 'Close logs' : 'Open logs'}</span>
          </button>
          {inferenceLogsOpen && (
            <div className="border-t border-indigo-100 p-4">
              {inferenceLogs.isLoading ? (
                <p className="text-sm text-gray-500">Loading inference-server logs…</p>
              ) : inferenceLogs.error ? (
                <p role="alert" className="text-sm text-amber-700">Inference-server logs are temporarily unavailable.</p>
              ) : inferenceLogs.data ? (
                <>
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                    {inferenceLogs.data.pod && <code>{inferenceLogs.data.pod}</code>}
                    <span>{inferenceLogs.data.container}</span>
                    <span>{inferenceLogs.data.phase}</span>
                  </div>
                  {inferenceLogs.data.message ? (
                    <p className="text-sm text-gray-500">{inferenceLogs.data.message}</p>
                  ) : (
                    <pre className="max-h-[32rem] overflow-auto rounded-lg bg-gray-950 p-3 font-mono text-xs leading-5 text-gray-100">{inferenceLogs.data.logs || 'No inference-server log output yet.'}</pre>
                  )}
                </>
              ) : null}
            </div>
          )}
        </section>
      )}

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

      <section className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        {stages.length > 0 ? (
          <PipelineTimeline stages={stages} />
        ) : (
          <div>
            <h2 className="text-sm font-semibold text-gray-900">Pipeline Timeline</h2>
            <p className="mt-2 text-sm text-gray-400">No pipeline stages to show yet.</p>
          </div>
        )}
      </section>

      {/* Keep the configuration and YAML together, as on the FourNos dashboard. */}
      <section className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-4 py-3 text-sm font-semibold text-gray-900">Test Configuration</div>
        <div className="p-4">
          <dl className="grid grid-cols-1 gap-x-8 gap-y-3 text-sm sm:grid-cols-[minmax(9rem,12rem)_minmax(0,1fr)]">
            <dt className="text-gray-500">Display Name</dt><dd className="break-all text-gray-800">{String(spec.displayName || '-')}</dd>
            <dt className="text-gray-500">Owner</dt><dd className="text-gray-800">{String(spec.owner || (isAuthenticated() ? '-' : 'Sign in to view'))}</dd>
            <dt className="text-gray-500">Pipeline</dt><dd className="text-gray-800"><code>{String(spec.pipeline || '-')}</code></dd>
            <dt className="text-gray-500">Cluster</dt><dd className="text-gray-800"><code>{String(spec.cluster || '-')}</code></dd>
            <dt className="text-gray-500">Exclusive</dt><dd className="text-gray-800">{spec.exclusive ? 'Yes' : 'No'}</dd>
            <dt className="text-gray-500">Project</dt><dd className="text-gray-800"><span className="rounded bg-indigo-50 px-2 py-0.5 font-medium text-indigo-700">{forge_info.project || '-'}</span></dd>
            <dt className="text-gray-500">Args (Presets)</dt><dd className="break-all text-gray-800"><code>{forge_info.args.join(', ') || 'none'}</code></dd>
            {Object.keys(hardware).length > 0 && <><dt className="text-gray-500">Hardware</dt><dd className="break-all text-gray-800"><code>{formatConfigValue(hardware)}</code></dd></>}
            {spec.priority != null && <><dt className="text-gray-500">Priority</dt><dd className="text-gray-800">{String(spec.priority)}</dd></>}
            {spec.ttl != null && <><dt className="text-gray-500">TTL</dt><dd className="text-gray-800">{String(spec.ttl)}</dd></>}
            {Object.keys(forge_info.config_overrides || {}).length > 0 && (
              <>
                <dt className="text-gray-500">Config Overrides</dt>
                <dd className="min-w-0 space-y-1 text-gray-800">
                  {Object.entries(forge_info.config_overrides).map(([key, value]) => (
                    <div key={key} className="break-all"><code>{key}: {formatConfigValue(value)}</code></div>
                  ))}
                </dd>
              </>
            )}
            {forge_info.pr_url && <><dt className="text-gray-500">Pull Request</dt><dd className="text-gray-800"><a href={forge_info.pr_url} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:underline">#{forge_info.pr_number}: {forge_info.pr_title}</a></dd></>}
            {secretRefs.length > 0 && <><dt className="text-gray-500">Secrets</dt><dd className="break-all text-gray-800">{secretRefs.map(String).join(', ')}</dd></>}
            {meta.creationTimestamp && <><dt className="text-gray-500">Created</dt><dd className="text-gray-800">{new Date(meta.creationTimestamp as string).toLocaleString()}</dd></>}
          </dl>

          <details className="mt-4 overflow-hidden rounded-lg border border-gray-200">
            <summary className="cursor-pointer bg-gray-50 px-4 py-2.5 text-sm font-semibold text-indigo-700 hover:bg-gray-100">View FournosJob YAML</summary>
            <pre className="m-0 max-h-[36rem] overflow-auto border-t border-gray-200 bg-gray-950 p-4 font-mono text-xs leading-relaxed text-gray-100">{jobYaml}</pre>
          </details>
        </div>
      </section>

      {/* Additional Forge provenance and dashboard associations */}
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

      {!isHistorical && (
        <section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
          <h2 className="mb-3 text-sm font-semibold text-gray-900">Pod Logs</h2>
          {pods.length > 0 ? (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <div className="rounded-lg border border-gray-100 p-3">
                <PodsPanel pods={pods} onSelectPod={setSelectedPod} selectedPod={selectedPod} />
              </div>
              <div className="min-h-48 rounded-lg border border-gray-100 p-3 lg:col-span-2">
                {selectedPod ? (
                  <LogViewer jobName={name!} podName={selectedPod} />
                ) : (
                  <div className="flex h-48 items-center justify-center text-sm text-gray-400">Select a pod to view logs.</div>
                )}
              </div>
            </div>
          ) : (
            <p className="py-4 text-sm text-gray-400">No pods for this job yet.</p>
          )}
        </section>
      )}

    </div>
  )
}
