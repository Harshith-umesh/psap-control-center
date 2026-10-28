import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeftIcon, ArrowPathIcon, ClockIcon, PlayIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import { Link } from 'react-router-dom'
import { createPortal } from 'react-dom'
import ReviewRow, { ReviewSection } from './ReviewRow'
import YamlPreview from './YamlPreview'
import { getProjectSubmitAdapter } from './rhaiis/rhaiisSubmitAdapter'
import { normalizeClusterName } from '../utils/cptClusterOptions'
import { useSubmitJob, useSubmitMatrix } from '../hooks/useFournos'
import { buildMatrixJobPreviews, buildSingleJobPreview, mergeMatrixConfigOverrides, toYamlPreview } from '../utils/fournosJobPreview'
import { getMatrixWorkloadOptions } from '../utils/matrixWorkloads'
import type { JobScheduling, ProjectUiSchema, PullRequestSelection, RunGroupReference, SubmitMatrixResponse, UiField, UiMode, UiOption, UiPipeline, UiQuickPreset, UiVisibleIf, WorkItemReference } from '../types'

// ─── Generic, schema-driven submit form ────────────────────────────────
//
// Renders whatever fields a project's ui/submit.yaml declares (see
// docs/ui-schema-spec.md), and builds a submission the same way for any
// project: arg-fields (no `maps_to`) contribute preset keys to `args`,
// override-fields (`maps_to` set) write into `config_overrides`.
//
// This only renders the *project-specific* portion of the submit wizard
// (steps 2 "Project Details" and 3 "Review & Submit" — nothing for step 1).
// Cluster, pipeline, owner, priority, exclusive, and the PR picker are
// "Basics", common to every project, and owned by the parent page — passed
// in here as `basics` — so there is exactly one wizard/step indicator, not
// one nested inside another.
//
// A `kind: matrix` mode (e.g. RHAIIS's CPT pipelines) additionally offers a
// pipeline picker plus model/workload checkboxes, submitting one job per
// selected model via `/fournos/submit-matrix` — this is the exact same
// renderer for every project, no per-project branch.

export interface SubmitBasics {
  cluster: string
  clusterGpuType?: string
  pipeline: string
  owner: string
  priority: string
  exclusive: boolean
  pullSha: string
  pullRequest: PullRequestSelection | null
  useLatestMain: boolean
  /** Human-readable label for the review step, e.g. "#123 — title (author)". */
  prLabel: string
  /** When this job (or recurring template) should run — see ClusterScheduleModal. */
  scheduling: JobScheduling
  workItems: WorkItemReference[]
  runGroupIds: string[]
  runGroups: RunGroupReference[]
}

export interface MatrixClusterConstraints {
  allowedClusterNames: string[]
  accelerator: string
  gpuType: string
}

/** SubmitJobRequest/SubmitMatrixRequest's schedule/scheduled_start_time pair for a given choice. */
function schedulingRequestFields(scheduling: JobScheduling): { schedule: string; scheduled_start_time: string | null } {
  if (scheduling.mode === 'defer') return { schedule: '', scheduled_start_time: scheduling.scheduledStartTimeUtc }
  if (scheduling.mode === 'recurring') return { schedule: scheduling.scheduleUtc, scheduled_start_time: null }
  return { schedule: '', scheduled_start_time: null }
}

function defaultValueFor(field: UiField): unknown {
  if (field.default !== undefined && field.default !== null) return field.default
  if (field.type === 'boolean') return false
  if (field.type === 'multiselect') return []
  return ''
}

function fieldsOf(mode: UiMode): UiField[] {
  return mode.sections.flatMap((s) => s.fields)
}

function matchesCondition(cond: UiVisibleIf, values: Record<string, unknown>): boolean {
  const current = values[cond.field]
  // Pydantic serializes an omitted optional `equals` as null. Treat that as
  // absent so a condition such as `{one_of: [...]}` is evaluated correctly.
  if (cond.equals !== undefined && cond.equals !== null) return current === cond.equals
  if (cond.one_of) return cond.one_of.includes(current)
  return true
}

function isFieldVisible(field: UiField, values: Record<string, unknown>): boolean {
  const cond = field.visible_if
  if (!cond) return true
  return matchesCondition(cond, values)
}

/**
 * This field's options, narrowed by any `restrict_if` rules whose `when`
 * condition currently matches (e.g. dropping `amd` from `accelerator` once
 * `engine == trt-llm`). Applies uniformly regardless of whether the options
 * came from literal `options:`, `options_ref`, or a `presets_ref` pool.
 */
function visibleOptions(field: UiField, values: Record<string, unknown>): UiOption[] {
  if (!field.restrict_if || field.restrict_if.length === 0) return field.options
  const excluded = new Set<string>()
  for (const rule of field.restrict_if) {
    if (matchesCondition(rule.when, values)) {
      for (const v of rule.exclude_values) excluded.add(v)
    }
  }
  if (excluded.size === 0) return field.options
  return field.options.filter((o) => !excluded.has(o.value))
}

/**
 * A `maps_to` may reference other fields' current values via `{fieldKey}`
 * placeholders — e.g. RHAIIS's per-accelerator engine image override,
 * `rhaiis.engines.{engine}.images.{accelerator}`, composes the real Forge
 * override key from two other fields rather than a single fixed one.
 * Fields with no placeholders resolve to themselves unchanged.
 */
function resolveMapsTo(mapsTo: string, values: Record<string, unknown>): string {
  return mapsTo.replace(/\{(\w+)\}/g, (_match, key: string) => {
    const v = values[key]
    return typeof v === 'string' ? v : ''
  })
}

function stringifyValue(value: unknown): string {
  if (Array.isArray(value)) return JSON.stringify(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return value == null ? '' : String(value)
}

function stringifyOverrides(overrides: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(overrides)) out[k] = stringifyValue(v)
  return out
}

function formatOverridesTooltip(overrides?: Record<string, unknown>): string {
  if (!overrides || Object.keys(overrides).length === 0) return ''
  return Object.entries(overrides)
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n')
}

function optionLabel(field: UiField, raw: string): string {
  return field.options.find((o) => o.value === raw)?.label || raw
}

/** Human-readable rendering of a field's current value for the review step. */
function formatFieldValueForReview(field: UiField, value: unknown): string | null {
  if (field.type === 'boolean') return value ? 'Yes' : 'No'
  if (field.type === 'multiselect') {
    const arr = Array.isArray(value) ? (value as string[]) : []
    if (arr.length === 0) return null
    return arr.map((v) => optionLabel(field, v)).join(', ')
  }
  if (field.type === 'select' || field.type === 'radio') {
    if (!value) return null
    return optionLabel(field, value as string)
  }
  if (value === undefined || value === null || value === '') return null
  return String(value)
}

function rawOptionValue(field: UiField, value: unknown): unknown {
  const option = field.options.find((item) => item.value === value)
  if (option && field.maps_to && Object.prototype.hasOwnProperty.call(option.overrides, field.maps_to)) {
    return option.overrides[field.maps_to]
  }
  return value
}

export default function DynamicSubmitForm({
  project,
  schema,
  basics,
  step,
  onBack,
  onNext,
  onSubmitted,
  onMatrixConstraintsChange,
  onRhaiisCptModeChange,
  matrixClusterValid,
  onOpenScheduleModal,
  singlePage = false,
  matrixPipelinePortalTarget,
}: {
  project: string
  schema: ProjectUiSchema
  basics: SubmitBasics
  /** Which shared wizard step is active; this component only renders for 2 and 3. */
  step: number
  onBack: () => void
  onNext: () => void
  onSubmitted?: (name: string) => void
  onMatrixConstraintsChange?: (constraints: MatrixClusterConstraints | null) => void
  onRhaiisCptModeChange?: (isCptMode: boolean) => void
  matrixClusterValid?: boolean
  /** Opens the shared ClusterScheduleModal (owned by the parent page) — the
   * "Defer / Set Recurring…" decision lives on this review step, once
   * every other field is finalized, not earlier in the wizard. */
  onOpenScheduleModal: () => void
  /** Renders project fields and YAML/submit controls together, without a review step. */
  singlePage?: boolean
  /** Optional parent-page slot for placing RHAIIS mode/pipeline selection before shared cluster settings. */
  matrixPipelinePortalTarget?: HTMLDivElement | null
}) {
  const submitJob = useSubmitJob()
  const submitMatrix = useSubmitMatrix()
  const [matrixSubmissionResult, setMatrixSubmissionResult] = useState<SubmitMatrixResponse | null>(null)

  const allModes = schema.modes

  const [activeModeId, setActiveModeId] = useState<string>(
    () => (allModes.find((m) => m.default) || allModes[0])?.id || ''
  )
  const activeMode = useMemo(
    () => allModes.find((m) => m.id === activeModeId) || allModes[0],
    [allModes, activeModeId]
  )
  const isMatrix = activeMode?.kind === 'matrix'
  const projectAdapter = useMemo(() => getProjectSubmitAdapter(project), [project])

  const [values, setValues] = useState<Record<string, unknown>>({})
  const [quickPresetKey, setQuickPresetKey] = useState('')
  const activeFields = useMemo(() => (activeMode ? fieldsOf(activeMode) : []), [activeMode])
  const modelField = activeFields.find((field) => field.key === 'model')
  const workloadField = activeFields.find((field) => field.key === 'workload')
  const engineField = activeFields.find((field) => field.key === 'engine')
  const derived = projectAdapter?.getDerivedState(basics, activeFields, values) || {
    gpuType: basics.clusterGpuType || '',
    selectedModelTp: 1,
    tpSize: 1,
    gpuCount: 1,
  }
  const autoEngineVersionRef = useRef('')

  // Matrix-mode-only selection state
  const [pipelineKey, setPipelineKey] = useState('')
  const [selectedModels, setSelectedModels] = useState<string[]>([])
  const [selectedWorkloads, setSelectedWorkloads] = useState<string[]>([])
  const selectedPipeline: UiPipeline | undefined = useMemo(
    () => activeMode?.pipelines.find((p) => p.key === pipelineKey),
    [activeMode, pipelineKey]
  )
  const selectedPipelineClusterSupported = !!selectedPipeline?.clusters?.some(
    (allowedCluster) => normalizeClusterName(allowedCluster) === normalizeClusterName(basics.cluster)
  )
  const matrixWorkloads = useMemo(
    () => getMatrixWorkloadOptions(project, selectedPipeline),
    [project, selectedPipeline]
  )
  const isRhaiisCpt = project.trim().toLowerCase() === 'rhaiis' && isMatrix && activeMode?.id === 'cpt'
  const matrixRows = useMemo(() => {
    if (!selectedPipeline) return []
    return selectedPipeline.models.map((model) => {
      const availableWorkloads = model.workloads?.length
        ? model.workloads
        : selectedPipeline.workloads
      const selected = selectedModels.includes(model.key)
      const workloads = selected
        ? selectedWorkloads.filter((workload) => availableWorkloads.includes(workload))
        : []
      return { model, availableWorkloads, selected, workloads }
    })
  }, [selectedPipeline, selectedModels, selectedWorkloads])
  const activeMatrixRows = matrixRows.filter((row) => row.selected && row.workloads.length > 0)
  const matrixJobCount = activeMatrixRows.length
  const matrixWorkloadExecutionCount = activeMatrixRows.reduce(
    (total, row) => total + row.workloads.length,
    0
  )

  useEffect(() => {
    if (!activeMode) return
    const initial: Record<string, unknown> = {}
    for (const field of fieldsOf(activeMode)) {
      initial[field.key] = defaultValueFor(field)
    }
    Object.assign(initial, projectAdapter?.getInitialValues(activeMode) || {})
    setValues(initial)
    setQuickPresetKey('')
    setPipelineKey('')
  }, [activeMode, projectAdapter])

  useEffect(() => {
    if (!selectedPipeline) {
      setSelectedModels([])
      setSelectedWorkloads([])
      return
    }
    setSelectedModels(selectedPipeline.models.map((m) => m.key))
    setSelectedWorkloads(matrixWorkloads)
    setValues((prev) => ({
      ...prev,
      ...(selectedPipeline.engine ? { engine: selectedPipeline.engine } : {}),
      ...(selectedPipeline.accelerator ? { accelerator: selectedPipeline.accelerator } : {}),
    }))
  }, [selectedPipeline, matrixWorkloads])

  useEffect(() => {
    if (!isRhaiisCpt || !selectedPipeline) {
      onMatrixConstraintsChange?.(null)
      return
    }
    onMatrixConstraintsChange?.({
      allowedClusterNames: selectedPipeline.clusters || [],
      accelerator: String(selectedPipeline.accelerator || values.accelerator || ''),
      gpuType: String(selectedPipeline.gpu_type || ''),
    })
  }, [isRhaiisCpt, selectedPipeline, values.accelerator, onMatrixConstraintsChange])

  useEffect(() => {
    onRhaiisCptModeChange?.(isRhaiisCpt)
  }, [isRhaiisCpt, onRhaiisCptModeChange])

  const setFieldValue = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  const handleFieldChange = (field: UiField, value: unknown) => {
    setFieldValue(field.key, value)
    if (projectAdapter) {
      const result = projectAdapter.handleFieldChange({
        field,
        value,
        values,
        modelField,
        engineField,
        autoEngineVersion: autoEngineVersionRef.current,
      })
      if (result.autoEngineVersion !== undefined) {
        autoEngineVersionRef.current = result.autoEngineVersion
      }
      if (Object.keys(result.updates).length > 0) {
        setValues((prev) => ({ ...prev, ...result.updates }))
      }
    }
  }

  // If a `restrict_if` rule now excludes a field's currently-selected
  // value (e.g. the user just switched `engine` to `trt-llm` while
  // `accelerator` was set to `amd`), clear it so the form never submits a
  // stale, now-invalid combination — the user re-picks from what's left.
  useEffect(() => {
    if (!activeMode) return
    for (const field of fieldsOf(activeMode)) {
      if (!field.restrict_if || field.restrict_if.length === 0) continue
      const allowed = visibleOptions(field, values)
      const allowedValues = new Set(allowed.map((o) => o.value))
      const current = values[field.key]
      if (field.type === 'multiselect') {
        if (!Array.isArray(current)) continue
        const filtered = (current as string[]).filter((v) => allowedValues.has(v))
        if (filtered.length !== current.length) setFieldValue(field.key, filtered)
      } else if (typeof current === 'string' && current && !allowedValues.has(current)) {
        setFieldValue(field.key, '')
      }
    }
  }, [activeMode, values])

  const applyQuickPreset = (qp: UiQuickPreset) => {
    const wasActive = quickPresetKey === qp.key
    setQuickPresetKey(wasActive ? '' : qp.key)
    if (wasActive || !activeMode) return
    const fields = fieldsOf(activeMode)
    setValues((prev) => {
      const next = { ...prev }
      for (const [fieldKey, fillValue] of Object.entries(qp.fills)) {
        const field = fields.find((f) => f.key === fieldKey)
        if (!field) continue
        if (field.type === 'multiselect') {
          const arr = Array.isArray(next[fieldKey]) ? [...(next[fieldKey] as string[])] : []
          const incoming = Array.isArray(fillValue) ? fillValue : [fillValue]
          for (const item of incoming) {
            if (typeof item === 'string' && !arr.includes(item)) arr.push(item)
          }
          next[fieldKey] = arr
        } else {
          next[fieldKey] = fillValue
        }
      }
      return next
    })
  }

  // Shared-field args/overrides: every mode's own declared fields (arg
  // fields -> args, override fields -> config_overrides) plus any selected
  // quick preset — identical logic whether the mode is `form` or `matrix`.
  const collectSharedArgsAndOverrides = (): { args: string[]; overrides: Record<string, string> } => {
    const args: string[] = []
    const overrides: Record<string, string> = {}
    if (!activeMode) return { args, overrides }

    if (quickPresetKey) {
      args.push(quickPresetKey)
      const qp = activeMode.quick_presets.find((p) => p.key === quickPresetKey)
      if (qp) {
        for (const [k, v] of Object.entries(qp.overrides)) overrides[k] = stringifyValue(v)
      }
    }

    projectAdapter?.appendBasicsArgs(args, basics)

    for (const field of fieldsOf(activeMode)) {
      if (!isFieldVisible(field, values)) continue
      const value = values[field.key]

      if (projectAdapter?.skipField(field)) continue

      if (field.maps_to) {
        if (value === undefined || value === '' || value === null) continue
        const key = resolveMapsTo(field.maps_to, values)
        if (!key) continue
        overrides[key] = stringifyValue(
          field.type === 'multiselect' && Array.isArray(value)
            ? value.map((item) => rawOptionValue(field, item))
            : rawOptionValue(field, value)
        )
      } else if (field.type === 'select' || field.type === 'radio') {
        if (typeof value === 'string' && value) args.push(value)
      } else if (field.type === 'multiselect' && Array.isArray(value)) {
        for (const v of value) if (typeof v === 'string' && v) args.push(v)
      }
    }

    projectAdapter?.augmentSubmission({
      values,
      modelField,
      workloadField,
      derived,
      overrides,
    })

    return { args, overrides }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!activeMode) return

    if (isMatrix) {
      if (
        !selectedPipeline ||
        matrixJobCount === 0 ||
        matrixWorkloadExecutionCount === 0 ||
        ((selectedPipeline.clusters?.length || 0) > 0 && !selectedPipelineClusterSupported)
      ) return
      const { args, overrides } = collectSharedArgsAndOverrides()
      try {
        setMatrixSubmissionResult(null)
        const result = await submitMatrix.mutateAsync({
          project,
          cluster: basics.cluster,
          pipeline: basics.pipeline,
          args,
          config_overrides: mergeMatrixConfigOverrides(selectedPipeline.overrides, overrides),
          matrix_pipeline_key: activeMode.id === 'cpt' ? selectedPipeline.key : '',
          version_label: String(values.version || '').trim(),
          accelerator: String(values.accelerator || '').trim(),
          engine: String(values.engine || '').trim(),
          engine_version: String(values.engine_version || '').trim(),
          configured_model_count: selectedPipeline.models.length,
          models: activeMatrixRows.map(({ model: m }) => {
            return {
              key: m.key,
              label: m.label || m.key,
              overrides: m?.overrides || {},
              gpu_count: m?.tp ?? derived.gpuCount,
              workloads: m?.workloads?.length ? m.workloads : selectedPipeline.workloads,
            }
          }),
          workloads: selectedWorkloads,
          owner: basics.owner,
          priority: basics.priority,
          exclusive: basics.exclusive,
          pull_request: basics.pullRequest,
          pull_sha: basics.pullSha,
          work_items: basics.workItems,
          run_group_ids: basics.runGroupIds,
          use_latest_main: basics.useLatestMain,
          gpu_type: derived.gpuType,
          ...schedulingRequestFields(basics.scheduling),
        })
        setMatrixSubmissionResult(result)
      } catch {
        // error surfaced below via submitMatrix.error
      }
      return
    }

    const { args, overrides: configOverrides } = collectSharedArgsAndOverrides()
    try {
      const result = await submitJob.mutateAsync({
        project,
        cluster: basics.cluster,
        pipeline: basics.pipeline,
        preset: '',
        args,
        version: '',
        owner: basics.owner,
        exclusive: basics.exclusive,
        config_overrides: configOverrides,
        pull_request: basics.pullRequest,
        pull_sha: basics.pullSha,
        work_items: basics.workItems,
        run_group_ids: basics.runGroupIds,
        use_latest_main: basics.useLatestMain,
        priority: basics.priority,
        gpu_type: derived.gpuType,
        gpu_count: derived.gpuCount,
        ...schedulingRequestFields(basics.scheduling),
      })
      onSubmitted?.(result.job_name)
    } catch {
      // error surfaced below via submitJob.error
    }
  }

  if (!activeMode) {
    return step >= 2 ? (
      <p className="text-sm text-gray-500">{schema.title || project} has no submit form defined.</p>
    ) : null
  }

  if (!singlePage && step !== 2 && step !== 3) return null

  const buildSourceValid = !projectAdapter?.requiresBuildSource || basics.useLatestMain || !!basics.pullSha.trim()
  const requiredFieldsValid = activeFields
    .filter((field) => field.required && isFieldVisible(field, values))
    .every((field) => {
      const value = values[field.key]
      return field.type === 'multiselect'
        ? Array.isArray(value) && value.length > 0
        : value !== undefined && value !== null && value !== ''
    })
  const projectFieldsValid = projectAdapter?.validate(activeMode, values, derived) ?? true
  const formValid = requiredFieldsValid && projectFieldsValid
  const canSubmit = isMatrix
    ? !submitMatrix.isPending && !!basics.cluster.trim() && !!basics.owner.trim() && buildSourceValid && formValid && !!selectedPipeline && matrixJobCount > 0 && matrixWorkloadExecutionCount > 0 && (!(selectedPipeline.clusters?.length) || selectedPipelineClusterSupported) && (!isRhaiisCpt || matrixClusterValid !== false)
    : !submitJob.isPending && !!basics.cluster.trim() && !!basics.owner.trim() && buildSourceValid && formValid

  const matrixSubmissionFeedback = matrixSubmissionResult ? (() => {
    const succeeded = matrixSubmissionResult.jobs.filter((job) => job.status === 'created' || job.status === 'succeeded').length
    const failed = matrixSubmissionResult.jobs.length - succeeded
    return (
      <section
        aria-live="polite"
        className={clsx(
          'rounded-lg border p-4',
          failed ? 'border-amber-300 bg-amber-50' : 'border-green-200 bg-green-50'
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className={clsx('text-sm font-semibold', failed ? 'text-amber-900' : 'text-green-900')}>
            {isRhaiisCpt ? 'CPT submission results' : 'Matrix submission results'}
          </h3>
          <span className={clsx('text-xs font-medium', failed ? 'text-amber-800' : 'text-green-800')}>
            {succeeded}/{matrixSubmissionResult.total} job(s) submitted{failed ? ' · ' + failed + ' failed' : ''}
          </span>
        </div>
        <ul className="mt-3 space-y-2">
          {matrixSubmissionResult.jobs.map((job, index) => {
            const created = job.status === 'created' || job.status === 'succeeded'
            return (
              <li key={job.model + '-' + (job.job_name || index)} className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className={created ? 'font-semibold text-green-700' : 'font-semibold text-red-700'}>
                  {created ? '✓' : '✗'} {job.model}
                </span>
                {job.job_name && (
                  <Link to={'/testing/jobs/' + encodeURIComponent(job.job_name)} className="font-mono text-xs text-indigo-700 hover:underline">
                    {job.job_name}
                  </Link>
                )}
                {!created && job.error && <span className="break-all text-xs text-red-700">{job.error}</span>}
              </li>
            )
          })}
        </ul>
        {matrixSubmissionResult.cpt_run_id && (
          <Link
            to={'/testing?tab=cpt&group=' + encodeURIComponent(matrixSubmissionResult.cpt_run_id)}
            className="mt-3 inline-flex text-sm font-semibold text-indigo-700 hover:underline"
          >
            Track this CPT run
          </Link>
        )}
      </section>
    )
  })() : null

  const buildYamlPreview = () => {
    const { args, overrides } = collectSharedArgsAndOverrides()
    const shared = {
      project,
      cluster: basics.cluster,
      pipeline: basics.pipeline,
      owner: basics.owner,
      priority: basics.priority,
      exclusive: basics.exclusive,
      pullSha: basics.useLatestMain ? 'main' : basics.pullSha,
      pullRequest: basics.pullRequest,
      gpuType: derived.gpuType,
      gpuCount: derived.gpuCount,
      args,
      configOverrides: overrides,
      schedule: basics.scheduling.mode === 'recurring' ? basics.scheduling.scheduleUtc : '',
      scheduledStartTime: basics.scheduling.mode === 'defer' ? basics.scheduling.scheduledStartTimeUtc : null,
    }
    if (!isMatrix) return toYamlPreview(buildSingleJobPreview(shared))
    if (!selectedPipeline) return ''
    return toYamlPreview(
      buildMatrixJobPreviews({
        ...shared,
        models: activeMatrixRows.map(({ model, workloads }) => {
          const modelOverrides = stringifyOverrides(model.overrides || {})
          return {
            key: model.key,
            overrides: projectAdapter?.applyMatrixWorkloads
              ? projectAdapter.applyMatrixWorkloads(modelOverrides, workloads)
              : modelOverrides,
            gpuCount: model?.tp ?? derived.gpuCount,
            workloads,
          }
        }),
        workloads: selectedWorkloads,
      }),
    )
  }

  const pipelineSetup = (
    <>
      {allModes.length > 1 && (
        <div className="flex gap-2">
          {allModes.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setActiveModeId(m.id)}
              className={clsx(
                'px-3 py-1.5 rounded-md text-sm font-medium border',
                m.id === activeModeId
                  ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                  : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
              )}
            >
              {m.label || m.id}
            </button>
          ))}
        </div>
      )}

      {isMatrix && (
        <div className="rounded-lg border border-gray-200 p-4 space-y-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">CPT Pipeline</h3>
            <p className="mt-1 text-xs text-gray-500">
              Choose a published pipeline first. Its model/workload matrix and supported cluster choices will be loaded below.
            </p>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700">Pipeline</label>
            {activeMode.pipelines.length > 0 ? (
              <select
                value={pipelineKey}
                onChange={(e) => setPipelineKey(e.target.value)}
                className="input mt-1"
                required
              >
                <option value="">-- Select a CPT pipeline --</option>
                {activeMode.pipelines.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label && p.label !== p.key ? `${p.label} (${p.key})` : p.key}
                  </option>
                ))}
              </select>
            ) : (
              <p className="mt-1 text-sm text-amber-700">
                No CPT pipelines were resolved from Forge’s cpt.d catalog. Refresh the project form from the Basics panel and try again.
              </p>
            )}
            {selectedPipeline?.clusters?.length ? (
              <p className="mt-1 text-xs text-gray-500">
                Available on: {selectedPipeline.clusters.join(', ')}
                {!selectedPipelineClusterSupported && (
                  <span className="ml-1 font-medium text-amber-700">
                    Select one of these clusters below to submit this pipeline.
                  </span>
                )}
              </p>
            ) : null}
            {isRhaiisCpt && matrixClusterValid === false && (
              <p className="mt-2 text-xs font-medium text-amber-700">
                Choose a registered cluster supported by this CPT pipeline and accelerator below.
              </p>
            )}
          </div>
        </div>
      )}
    </>
  )
  const usePipelineSetupPortal = singlePage && step === 2 && !!matrixPipelinePortalTarget

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {step === 2 && (
        <>
          {usePipelineSetupPortal && matrixPipelinePortalTarget
            ? createPortal(pipelineSetup, matrixPipelinePortalTarget)
            : pipelineSetup}

          {activeMode.quick_presets.length > 0 && (
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-4">
              <label className="block text-sm font-medium text-gray-700 mb-1.5">Quick Presets</label>
              <div className="flex flex-wrap gap-1.5">
                {activeMode.quick_presets.map((qp) => (
                  <button
                    key={qp.key}
                    type="button"
                    title={formatOverridesTooltip(qp.overrides)}
                    onClick={() => applyQuickPreset(qp)}
                    className={clsx(
                      'px-3 py-1.5 rounded-md text-xs font-medium border transition-colors',
                      quickPresetKey === qp.key
                        ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                        : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
                    )}
                  >
                    {qp.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {isMatrix && selectedPipeline && (
            <div className="space-y-3 rounded-lg border border-gray-200 bg-white p-4">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">Matrix Preview</h3>
                <p className="mt-1 text-xs text-gray-500">
                  Select models by row and workload profiles by column. A checkmark means that model runs that selected profile.
                </p>
              </div>
              {selectedPipeline.models.length > 0 && matrixWorkloads.length > 0 ? (
                <div className="overflow-x-auto rounded-md border border-gray-200">
                  <table className="w-full min-w-[680px] border-collapse text-xs">
                    <thead className="bg-gray-50 text-gray-600">
                      <tr>
                        <th scope="col" className="min-w-64 border-b border-gray-200 px-3 py-2 text-left font-semibold">
                          Model
                        </th>
                        {matrixWorkloads.map((workload) => {
                          const checked = selectedWorkloads.includes(workload)
                          return (
                            <th key={workload} scope="col" className="min-w-24 border-b border-l border-gray-200 px-2 py-2 text-center font-semibold">
                              <label className="inline-flex cursor-pointer items-center justify-center gap-1 whitespace-nowrap">
                                <input
                                  type="checkbox"
                                  aria-label={`Select workload ${workload}`}
                                  checked={checked}
                                  onChange={() =>
                                    setSelectedWorkloads((prev) =>
                                      checked ? prev.filter((item) => item !== workload) : [...prev, workload]
                                    )
                                  }
                                />
                                {workload}
                              </label>
                            </th>
                          )
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {matrixRows.map(({ model, availableWorkloads, selected }) => (
                        <tr key={model.key} className="odd:bg-white even:bg-gray-50/50">
                          <th scope="row" className="border-b border-gray-200 px-3 py-2 text-left font-normal text-gray-800">
                            <label className="inline-flex cursor-pointer items-center gap-2">
                              <input
                                type="checkbox"
                                aria-label={`Select model ${model.key}`}
                                checked={selected}
                                onChange={() =>
                                  setSelectedModels((prev) =>
                                    selected
                                      ? prev.filter((key) => key !== model.key)
                                      : [...prev, model.key]
                                  )
                                }
                              />
                              <span title={formatOverridesTooltip(model.overrides)}>{model.key}</span>
                              {model.tp && model.tp > 1 && (
                                <span className="whitespace-nowrap text-gray-400">({model.tp} GPU)</span>
                              )}
                              {Object.keys(model.overrides || {}).length > 0 && (
                                <span className="text-indigo-600" title="This model has additional overrides">*</span>
                              )}
                            </label>
                          </th>
                          {matrixWorkloads.map((workload) => {
                            const active = selected
                              && selectedWorkloads.includes(workload)
                              && availableWorkloads.includes(workload)
                            return (
                              <td key={workload} className="border-b border-l border-gray-200 px-2 py-2 text-center">
                                {active
                                  ? <span aria-label="Included" className="font-semibold text-green-700">✓</span>
                                  : <span aria-label="Not included" className="text-gray-400">—</span>}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-amber-700">This pipeline does not define both models and workloads.</p>
              )}
              <div role="status" className="rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-semibold text-blue-700">
                {matrixJobCount} model(s), {matrixWorkloadExecutionCount} workload execution(s) = {matrixJobCount} FournosJob(s)
              </div>
              {projectAdapter?.renderMatrixExtras?.(selectedWorkloads)}
            </div>
          )}

          {activeMode.sections.map((section) => (
            <div key={section.id} className="rounded-lg border border-gray-200 p-4 space-y-4">
              {section.label && (
                <h3 className="text-sm font-semibold text-gray-900">
                  {projectAdapter?.sectionLabel(section) || section.label}
                </h3>
              )}
              <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                {section.fields.map((field) => {
                  const presentation = projectAdapter?.fieldPresentation(field, values, activeMode) || {
                    hidden: false,
                    label: field.label || field.key,
                    disabled: false,
                  }
                  if (field.type === 'hidden' || presentation.hidden || !isFieldVisible(field, values)) return null
                  const options = visibleOptions(field, values)
                  const fieldOptions = !isMatrix && projectAdapter?.orderFieldOptions
                    ? projectAdapter.orderFieldOptions(field, options)
                    : options
                  return (
                    <div key={field.key} className={clsx(field.type === 'textarea' ? 'sm:col-span-2' : '', field.key === 'model' || field.key === 'workload' ? 'sm:col-span-2' : '')}>
                      <label
                        className="block text-sm font-medium text-gray-700"
                        title={field.help || undefined}
                      >
                        {presentation.label}
                        {field.required && <span className="text-red-500 ml-0.5">*</span>}
                      </label>
                      <FieldControl
                        field={field}
                        value={values[field.key]}
                        onChange={(v) => handleFieldChange(field, v)}
                        options={fieldOptions}
                        disabled={presentation.disabled}
                        lockedOption={
                          isMatrix && field.key === 'engine'
                            ? selectedPipeline?.engine || undefined
                            : isMatrix && field.key === 'accelerator'
                              ? selectedPipeline?.accelerator || undefined
                              : undefined
                        }
                      />
                      {field.help && <p className="mt-1 text-xs text-gray-400">{field.help}</p>}
                      {projectAdapter?.renderFieldExtras({
                        field,
                        values,
                        derived,
                        setFieldValue,
                        setValues,
                      })}
                    </div>
                  )
                })}
                {projectAdapter?.renderSectionExtras(section, {
                  values,
                  derived,
                  setFieldValue,
                  setValues,
                })}
              </div>
            </div>
          ))}

          {!isMatrix && projectAdapter?.renderAdditionalConfig({
            values,
            derived,
            setFieldValue,
            setValues,
          })}

          {singlePage ? (
            <>
              {(submitJob.error || submitMatrix.error) && (
                <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
                  {(submitJob.error || submitMatrix.error)?.message}
                </div>
              )}
              <YamlPreview yaml={buildYamlPreview()} title="FournosJob YAML Preview" defaultOpen={false} />
              {!canSubmit && (
                <p className="text-xs text-orange-600">Complete the required fields above before submitting.</p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-200 pt-4">
                <button
                  type="button"
                  onClick={onBack}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  <ArrowLeftIcon className="h-4 w-4" /> Back
                </button>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={onOpenScheduleModal}
                    disabled={!basics.cluster.trim()}
                    className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                  >
                    <ClockIcon className="h-4 w-4" /> Defer / Set Recurring…
                  </button>
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {submitJob.isPending || submitMatrix.isPending ? (
                      <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    ) : (
                      <PlayIcon className="h-4 w-4" />
                    )}
                    {isMatrix ? 'Submit CPT Pipeline' : 'Submit Job'}
                  </button>
                </div>
              </div>
              {matrixSubmissionFeedback}
            </>
          ) : (
            <div className="flex justify-between">
              <button
                type="button"
                onClick={onBack}
                className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                <ArrowLeftIcon className="h-4 w-4" /> Back
              </button>
              <button
                type="button"
                onClick={onNext}
                disabled={!formValid}
                title={!formValid ? 'Complete the required project fields before continuing' : undefined}
                className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
              >
                Next: Review
              </button>
            </div>
          )}
        </>
      )}

      {step === 3 && (() => {
        const yamlText = buildYamlPreview()

        return (
          <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:gap-6 lg:items-start">
            <div className="space-y-4 min-w-0">
              <ReviewSection title="Basics">
                <ReviewRow label="Project" value={project} />
                <ReviewRow label="Cluster" value={basics.cluster} missing={!basics.cluster.trim()} />
                <ReviewRow label="Pipeline" value={basics.pipeline} />
                {projectAdapter?.renderReviewBasics(derived)}
                {basics.owner && <ReviewRow label="Requested by" value={basics.owner} />}
                <ReviewRow label="Priority" value={basics.priority || 'Cluster default (no priority class)'} />
                {basics.exclusive && <ReviewRow label="Exclusive" value="Yes" />}
                {projectAdapter?.requiresBuildSource && basics.useLatestMain && (
                  <ReviewRow label={projectAdapter.buildSourceLabel} value="Latest main (un-pinned)" />
                )}
                {basics.pullSha && (
                  <ReviewRow label={projectAdapter?.buildSourceLabel || 'Pull Request'} value={basics.prLabel} mono={!basics.prLabel || basics.prLabel === basics.pullSha} />
                )}
                {basics.workItems.map((item) => (
                  <ReviewRow key={`${item.provider}-${item.key || item.url}`} label="Work item" value={item.key || item.url} />
                ))}
                {basics.runGroupIds.map((id) => {
                  const group = basics.runGroups.find((candidate) => candidate.id === id)
                  return group ? <ReviewRow key={id} label={group.group_type} value={group.display_name} /> : null
                })}
                <ReviewRow
                  label="Schedule"
                  value={
                    basics.scheduling.mode === 'now'
                      ? 'Run now'
                      : basics.scheduling.mode === 'defer'
                        ? `Deferred — ${basics.scheduling.label}`
                        : `Recurring — ${basics.scheduling.label}`
                  }
                />
              </ReviewSection>

              {(submitJob.error || submitMatrix.error) && (
                <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700">
                  {(submitJob.error || submitMatrix.error)?.message}
                </div>
              )}
              {matrixSubmissionFeedback}

              {(allModes.length > 1 || quickPresetKey) && (
                <ReviewSection>
                  {allModes.length > 1 && <ReviewRow label="Mode" value={activeMode.label || activeMode.id} />}
                  {quickPresetKey && (
                    <ReviewRow
                      label="Quick Preset"
                      value={activeMode.quick_presets.find((p) => p.key === quickPresetKey)?.label || quickPresetKey}
                    />
                  )}
                </ReviewSection>
              )}

              {activeMode.sections.map((section) => {
                const rows = section.fields
                  .filter((f) => f.type !== 'hidden' && isFieldVisible(f, values))
                  .map((field) => ({ field, display: formatFieldValueForReview(field, values[field.key]) }))
                  .filter(({ field, display }) => field.required || display !== null)
                if (rows.length === 0) return null
                return (
                  <ReviewSection key={section.id} title={section.label}>
                    {rows.map(({ field, display }) => (
                      <ReviewRow
                        key={field.key}
                        label={field.label || field.key}
                        value={display}
                        missing={display === null}
                      />
                    ))}
                  </ReviewSection>
                )
              })}

              {projectAdapter?.renderAdditionalReview(values, derived)}

              {isMatrix && (
                <ReviewSection title="Matrix Selection">
                  <ReviewRow
                    label="Pipeline"
                    value={selectedPipeline?.label || selectedPipeline?.key}
                    missing={!selectedPipeline}
                  />
                  <ReviewRow
                    label="Models"
                    value={activeMatrixRows
                      .map(({ model }) => model.label || model.key)
                      .join(', ')}
                    missing={matrixJobCount === 0}
                  />
                  <ReviewRow
                    label="Workloads"
                    value={selectedWorkloads.join(', ')}
                    missing={selectedWorkloads.length === 0}
                  />
                  <ReviewRow
                    label="Workload executions"
                    value={`${matrixWorkloadExecutionCount} across ${matrixJobCount} job(s)`}
                    missing={matrixWorkloadExecutionCount === 0}
                  />
                </ReviewSection>
              )}

              {!canSubmit && (
                <p className="text-xs text-orange-600">
                  Some required fields above are missing — fill them in on the previous step before submitting.
                </p>
              )}

              <div className="flex justify-between">
                <button
                  type="button"
                  onClick={onBack}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  <ArrowLeftIcon className="h-4 w-4" /> Back
                </button>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={onOpenScheduleModal}
                    disabled={!basics.cluster.trim()}
                    className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                    title={!basics.cluster.trim() ? 'Pick a cluster first' : undefined}
                  >
                    <ClockIcon className="h-4 w-4" />
                    Defer / Set Recurring…
                  </button>
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="inline-flex items-center gap-2 rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {submitJob.isPending || submitMatrix.isPending ? (
                      <ArrowPathIcon className="h-4 w-4 animate-spin" />
                    ) : (
                      <PlayIcon className="h-4 w-4" />
                    )}
                    {isMatrix ? `Submit ${selectedModels.length || ''} Job(s)` : 'Submit Job'}
                  </button>
                </div>
              </div>
            </div>

            <div className="mt-4 lg:mt-0 lg:sticky lg:top-4">
              <YamlPreview yaml={yamlText} />
            </div>
          </div>
        )
      })()}
    </form>
  )
}

// ─── Field controls ─────────────────────────────────────────────────────

function FieldControl({
  field,
  value,
  onChange,
  options,
  disabled = false,
  lockedOption,
}: {
  field: UiField
  value: unknown
  onChange: (value: unknown) => void
  /** Defaults to `field.options` — pass a `visibleOptions(field, values)` result to apply `restrict_if`. */
  options?: UiOption[]
  disabled?: boolean
  /** Keep a pipeline-pinned option selected while showing other choices as unavailable. */
  lockedOption?: string
}) {
  const opts = options ?? field.options
  switch (field.type) {
    case 'boolean':
      return (
        <div className="mt-2">
          <label className="flex items-center gap-2 text-sm text-gray-700">
            <input
              type="checkbox"
              checked={!!value}
              onChange={(e) => onChange(e.target.checked)}
              disabled={disabled}
              className="rounded border-gray-300 text-indigo-600"
            />
            {field.placeholder || 'Enabled'}
          </label>
        </div>
      )

    case 'number':
      return (
        <input
          type="number"
          value={value === undefined || value === null ? '' : (value as number | string)}
          min={field.min ?? undefined}
          max={field.max ?? undefined}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
          disabled={disabled}
          className="input mt-1"
          placeholder={field.placeholder}
        />
      )

    case 'textarea':
      return (
        <textarea
          value={(value as string) || ''}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          rows={3}
          className="input mt-1 font-mono"
          placeholder={field.placeholder}
        />
      )

    case 'select':
      return (
        <select
          value={(value as string) || ''}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="input mt-1"
        >
          <option value="">{field.required ? 'Select...' : 'None'}</option>
          {opts.map((opt) => (
            <option key={opt.value} value={opt.value} title={formatOverridesTooltip(opt.overrides)}>
              {opt.label || opt.value}
            </option>
          ))}
        </select>
      )

    case 'radio':
      return (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {opts.map((opt) => {
            const optionDisabled = disabled || (lockedOption !== undefined && opt.value !== lockedOption)
            return (
              <button
                key={opt.value}
                type="button"
                title={formatOverridesTooltip(opt.overrides)}
                disabled={optionDisabled}
                onClick={() => onChange(
                  lockedOption !== undefined
                    ? opt.value
                    : value === opt.value ? '' : opt.value
                )}
                className={clsx(
                  'px-3 py-1.5 rounded-md text-xs font-medium border transition-colors',
                  value === opt.value
                    ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                    : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400',
                  optionDisabled && 'cursor-not-allowed opacity-40 hover:border-gray-300'
                )}
              >
                {opt.label || opt.value}
              </button>
            )
          })}
          {opts.length === 0 && <p className="text-xs text-gray-400">None available.</p>}
        </div>
      )

    case 'multiselect': {
      const selected = Array.isArray(value) ? (value as string[]) : []
      const toggle = (v: string) =>
        onChange(selected.includes(v) ? selected.filter((s) => s !== v) : [...selected, v])
      return (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {opts.map((opt) => (
            <label
              key={opt.value}
              title={formatOverridesTooltip(opt.overrides)}
              className={clsx(
                'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border cursor-pointer select-none',
                selected.includes(opt.value)
                  ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                  : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
              )}
            >
              <input
                type="checkbox"
                className="h-3 w-3 rounded border-gray-300 text-indigo-600"
                checked={selected.includes(opt.value)}
                onChange={() => toggle(opt.value)}
                disabled={disabled}
              />
              {opt.label || opt.value}
            </label>
          ))}
          {opts.length === 0 && <p className="text-xs text-gray-400">None available.</p>}
        </div>
      )
    }

    case 'text':
    default:
      return (
        <input
          type="text"
          value={(value as string) || ''}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="input mt-1"
          placeholder={field.placeholder}
        />
      )
  }
}
