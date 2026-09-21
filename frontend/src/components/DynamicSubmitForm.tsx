import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeftIcon, ArrowPathIcon, ClockIcon, PlayIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import ReviewRow, { ReviewSection } from './ReviewRow'
import YamlPreview from './YamlPreview'
import { useSubmitJob, useSubmitMatrix } from '../hooks/useFournos'
import { buildMatrixJobPreviews, buildSingleJobPreview, toYamlPreview } from '../utils/fournosJobPreview'
import type { JobScheduling, ProjectUiSchema, UiField, UiMode, UiOption, UiPipeline, UiQuickPreset, UiVisibleIf } from '../types'

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
  useLatestMain: boolean
  /** Human-readable label for the review step, e.g. "#123 — title (author)". */
  prLabel: string
  /** When this job (or recurring template) should run — see ClusterScheduleModal. */
  scheduling: JobScheduling
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
  if (cond.equals !== undefined) return current === cond.equals
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

const RHAIIS_CLUSTER_GPU_TYPES: Record<string, string> = {
  hera: 'h200',
  zeus: 'h200',
  'old-zeus': 'h200',
  b200: 'b200',
  mi355x: 'amd',
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

function modelTpSize(field: UiField | undefined, value: unknown): number | null {
  if (!field || typeof value !== 'string') return null
  const option = field.options.find((item) => item.value === value)
  const extra = option?.extra || {}
  const vllmArgs = extra.vllm_args as Record<string, unknown> | undefined
  const sglangArgs = extra.sglang_args as Record<string, unknown> | undefined
  const raw = vllmArgs?.['tensor-parallel-size'] ?? sglangArgs?.['tp-size'] ?? extra.tensor_parallel ?? extra.tp_size ?? extra.tp
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function parseOverrideLines(raw: string): Record<string, string> {
  const overrides: Record<string, string> = {}
  raw.split('\n').forEach((line) => {
    const index = line.indexOf(':')
    if (index > 0) {
      overrides[line.slice(0, index).trim()] = line.slice(index + 1).trim()
    }
  })
  return overrides
}

export default function DynamicSubmitForm({
  project,
  schema,
  basics,
  step,
  onBack,
  onNext,
  onSubmitted,
  onOpenScheduleModal,
}: {
  project: string
  schema: ProjectUiSchema
  basics: SubmitBasics
  /** Which shared wizard step is active; this component only renders for 2 and 3. */
  step: number
  onBack: () => void
  onNext: () => void
  onSubmitted?: (name: string) => void
  /** Opens the shared ClusterScheduleModal (owned by the parent page) — the
   * "Defer / Set Recurring…" decision lives on this review step, once
   * every other field is finalized, not earlier in the wizard. */
  onOpenScheduleModal: () => void
}) {
  const submitJob = useSubmitJob()
  const submitMatrix = useSubmitMatrix()

  const allModes = schema.modes

  const [activeModeId, setActiveModeId] = useState<string>(
    () => (allModes.find((m) => m.default) || allModes[0])?.id || ''
  )
  const activeMode = useMemo(
    () => allModes.find((m) => m.id === activeModeId) || allModes[0],
    [allModes, activeModeId]
  )
  const isMatrix = activeMode?.kind === 'matrix'
  const isRhaiis = project === 'rhaiis'

  const [values, setValues] = useState<Record<string, unknown>>({})
  const [quickPresetKey, setQuickPresetKey] = useState('')
  const activeFields = useMemo(() => (activeMode ? fieldsOf(activeMode) : []), [activeMode])
  const modelField = activeFields.find((field) => field.key === 'model')
  const workloadField = activeFields.find((field) => field.key === 'workload')
  const engineField = activeFields.find((field) => field.key === 'engine')
  const clusterName = basics.cluster.trim().toLowerCase()
  const gpuType = basics.clusterGpuType || (isRhaiis ? RHAIIS_CLUSTER_GPU_TYPES[clusterName] || '' : '')
  const selectedModelTp = modelTpSize(modelField, values.model) || 1
  const tpSize = isRhaiis ? Math.max(Number(values.tp_size) || selectedModelTp, 1) : 1
  const gpuCount = isRhaiis ? Math.max(Number(values.gpu_count) || 1, tpSize) : 1
  const autoEngineVersionRef = useRef('')

  // Matrix-mode-only selection state
  const [pipelineKey, setPipelineKey] = useState('')
  const [selectedModels, setSelectedModels] = useState<string[]>([])
  const [selectedWorkloads, setSelectedWorkloads] = useState<string[]>([])
  const selectedPipeline: UiPipeline | undefined = useMemo(
    () => activeMode?.pipelines.find((p) => p.key === pipelineKey),
    [activeMode, pipelineKey]
  )

  useEffect(() => {
    if (!activeMode) return
    const initial: Record<string, unknown> = {}
    for (const field of fieldsOf(activeMode)) {
      initial[field.key] = defaultValueFor(field)
      if (isRhaiis && field.key === 'warmup') initial[field.key] = true
      if (isRhaiis && field.key === 'slack') initial[field.key] = true
    }
    if (isRhaiis) {
      initial.advanced_overrides = ''
      initial.custom_model_tp = 1
      initial.custom_workload_data = 'prompt_tokens=1000,output_tokens=1000'
      initial.custom_workload_concurrencies = '1'
      initial.custom_workload_max_seconds = 450
      initial.custom_workload_samples = ''
    }
    setValues(initial)
    setQuickPresetKey('')
    setPipelineKey(activeMode.pipelines[0]?.key || '')
  }, [activeMode, isRhaiis, project])

  useEffect(() => {
    if (!selectedPipeline) {
      setSelectedModels([])
      setSelectedWorkloads([])
      return
    }
    setSelectedModels(selectedPipeline.models.map((m) => m.key))
    setSelectedWorkloads(selectedPipeline.workloads)
  }, [selectedPipeline])

  const setFieldValue = (key: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [key]: value }))
  }

  const handleFieldChange = (field: UiField, value: unknown) => {
    setFieldValue(field.key, value)
    if (!isRhaiis) return

    if (field.key === 'model') {
      const nextTp = value === '__custom_model__' ? 1 : modelTpSize(modelField, value) || 1
      setValues((prev) => ({
        ...prev,
        model: value,
        tp_size: nextTp,
        gpu_count: nextTp,
      }))
    } else if (field.key === 'tp_size') {
      const nextTp = Math.max(Number(value) || 1, 1)
      setValues((prev) => ({
        ...prev,
        tp_size: nextTp,
        gpu_count: Math.max(Number(prev.gpu_count) || 1, nextTp),
      }))
    } else if (field.key === 'engine' || field.key === 'accelerator') {
      const nextEngine = field.key === 'engine' ? value : values.engine
      const nextAccelerator = field.key === 'accelerator' ? value : values.accelerator
      const engineOption = engineField?.options.find((option) => option.value === nextEngine)
      const images = engineOption?.extra?.images as Record<string, unknown> | undefined
      const defaultImage = typeof images?.[String(nextAccelerator)] === 'string'
        ? String(images[String(nextAccelerator)])
        : ''
      if (defaultImage && (!values.engine_version || values.engine_version === autoEngineVersionRef.current)) {
        autoEngineVersionRef.current = defaultImage
        setFieldValue('engine_version', defaultImage)
      }
    } else if (field.key === 'engine_version') {
      autoEngineVersionRef.current = ''
    } else if (field.key === 'agent_analysis' && value === true) {
      setFieldValue('compare_versions', true)
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

    // The shared Basics cluster is also the RHAIIS cluster/profile argument.
    // Keeping it here avoids a duplicate Project Details selector and keeps
    // the submitted job aligned with the legacy FourNos dashboard.
    if (isRhaiis && basics.cluster.trim()) args.push(basics.cluster.trim())

    for (const field of fieldsOf(activeMode)) {
      if (!isFieldVisible(field, values)) continue
      const value = values[field.key]

      // RHAIIS has a plural workload override and a few custom controls
      // whose keys are not part of Forge's declarative schema yet. Handle
      // those below so the ordinary schema renderer remains generic.
      if (isRhaiis && (field.key === 'model' || field.key === 'workload')) continue

      if (field.maps_to) {
        if (value === undefined || value === '' || value === null) continue
        const key = resolveMapsTo(field.maps_to, values)
        if (!key) continue
        if (key === 'rhaiis.compare_versions.enabled') continue
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

    if (isRhaiis) {
      const selectedModel = values.model
      if (selectedModel === '__custom_model__') {
        overrides['tests.rhaiis.model_key'] = 'custom'
        const customName = String(values.custom_model_name || '').trim()
        const customId = String(values.custom_model_id || '').trim()
        if (customName) overrides['models.custom.name'] = customName
        if (customId) overrides['models.custom.hf_model_id'] = customId
        overrides['rhaiis.engines.vllm.args.tensor-parallel-size'] = stringifyValue(tpSize)
      } else if (selectedModel) {
        overrides['tests.rhaiis.model_key'] = stringifyValue(rawOptionValue(modelField!, selectedModel))
        if (Number(values.tp_size) && Number(values.tp_size) !== selectedModelTp) {
          overrides['rhaiis.engines.vllm.args.tensor-parallel-size'] = stringifyValue(tpSize)
        }
      }

      const selectedWorkloads = Array.isArray(values.workload) ? values.workload as string[] : []
      if (selectedWorkloads.length > 0) {
        const workloadKeys = selectedWorkloads.map((item) =>
          item === '__custom_workload__' ? 'custom' : rawOptionValue(workloadField!, item)
        )
        overrides['tests.rhaiis.workload_keys'] = stringifyValue(workloadKeys)
        if (selectedWorkloads.includes('__custom_workload__')) {
          const customData = String(values.custom_workload_data || '').trim()
          const customConcurrencies = String(values.custom_workload_concurrencies || '').trim()
          if (customData) overrides['workloads.custom.data'] = customData
          if (customConcurrencies) {
            overrides['workloads.custom.concurrencies'] = stringifyValue(
              customConcurrencies.split(',').map((item) => Number(item.trim()) || 0)
            )
          }
          if (values.custom_workload_max_seconds !== '' && values.custom_workload_max_seconds != null) {
            overrides['workloads.custom.max_seconds'] = stringifyValue(Number(values.custom_workload_max_seconds) || 450)
          }
          if (values.custom_workload_samples !== '' && values.custom_workload_samples != null) {
            overrides['workloads.custom.samples'] = stringifyValue(Number(values.custom_workload_samples))
          }
        }
      }

      const slackMember = String(values.slack_member_id || '').trim()
      if (slackMember) overrides['tests.rhaiis.slack_user'] = slackMember
      const compareVersion = String(values.compare_version || '').trim()
      if (compareVersion) overrides['tests.rhaiis.compare_version'] = compareVersion

      const engineVersion = String(values.engine_version || '').trim()
      const engine = String(values.engine || '').trim()
      const accelerator = String(values.accelerator || '').trim()
      if (engineVersion && engine && accelerator) {
        overrides[`rhaiis.engines.${engine}.images.${accelerator}`] = engineVersion
      }

      if (values.prefix_caching !== undefined && engine) {
        delete overrides['rhaiis.engines.vllm.args.enable-prefix-caching']
        delete overrides['rhaiis.engines.vllm.args.no-enable-prefix-caching']
        delete overrides['rhaiis.engines.sglang.args.disable-radix-cache']
        delete overrides['rhaiis.engines.trtllm.trtllm_config.kv_cache_config.enable_block_reuse']
        const prefixCaching = values.prefix_caching === true
        if (engine === 'sglang') {
          overrides['rhaiis.engines.sglang.args.disable-radix-cache'] = stringifyValue(!prefixCaching)
        } else if (engine === 'trtllm') {
          overrides['rhaiis.engines.trtllm.trtllm_config.kv_cache_config.enable_block_reuse'] = stringifyValue(prefixCaching)
        } else {
          overrides[prefixCaching
            ? 'rhaiis.engines.vllm.args.enable-prefix-caching'
            : 'rhaiis.engines.vllm.args.no-enable-prefix-caching'] = 'true'
        }
      }

      Object.assign(overrides, parseOverrideLines(String(values.advanced_overrides || '')))
    }

    return { args, overrides }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!activeMode) return

    if (isMatrix) {
      if (!selectedPipeline || selectedModels.length === 0 || selectedWorkloads.length === 0) return
      const { args, overrides } = collectSharedArgsAndOverrides()
      const modelByKey = new Map(selectedPipeline.models.map((m) => [m.key, m]))
      try {
        const result = await submitMatrix.mutateAsync({
          project,
          cluster: basics.cluster,
          pipeline: basics.pipeline,
          args,
          config_overrides: { ...overrides, ...stringifyOverrides(selectedPipeline.overrides) },
          models: selectedModels.map((key) => {
            const m = modelByKey.get(key)
            return {
              key,
              overrides: m?.overrides || {},
              gpu_count: m?.tp ?? gpuCount,
            }
          }),
          workloads: selectedWorkloads,
          owner: basics.owner,
          priority: basics.priority,
          exclusive: basics.exclusive,
          pull_sha: basics.pullSha,
          use_latest_main: basics.useLatestMain,
          gpu_type: gpuType,
          ...schedulingRequestFields(basics.scheduling),
        })
        onSubmitted?.(result.jobs?.[0]?.job_name || '')
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
        pull_sha: basics.pullSha,
        use_latest_main: basics.useLatestMain,
        priority: basics.priority,
        gpu_type: gpuType,
        gpu_count: gpuCount,
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

  if (step !== 2 && step !== 3) return null

  const buildSourceValid = project !== 'rhaiis' || basics.useLatestMain || !!basics.pullSha.trim()
  const requiredFieldsValid = activeFields
    .filter((field) => field.required && isFieldVisible(field, values))
    .every((field) => {
      const value = values[field.key]
      return field.type === 'multiselect'
        ? Array.isArray(value) && value.length > 0
        : value !== undefined && value !== null && value !== ''
    })
  const customModelValid = !isRhaiis || values.model !== '__custom_model__' || !!String(values.custom_model_id || '').trim()
  const customWorkloadSelected = isRhaiis && Array.isArray(values.workload) && values.workload.includes('__custom_workload__')
  const customWorkloadValid = !customWorkloadSelected || (
    !!String(values.custom_workload_data || '').trim() &&
    !!String(values.custom_workload_concurrencies || '').trim()
  )
  const sizingValid = !isRhaiis || gpuCount >= tpSize
  const slackValid = !isRhaiis || activeMode.id !== 'single' || values.slack !== true || !!String(values.slack_member_id || '').trim()
  const formValid = requiredFieldsValid && customModelValid && customWorkloadValid && sizingValid && slackValid
  const canSubmit = isMatrix
    ? !submitMatrix.isPending && !!basics.cluster.trim() && !!basics.owner.trim() && buildSourceValid && formValid && !!selectedPipeline && selectedModels.length > 0 && selectedWorkloads.length > 0
    : !submitJob.isPending && !!basics.cluster.trim() && !!basics.owner.trim() && buildSourceValid && formValid

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {step === 2 && (
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

          {activeMode.sections.map((section) => (
            <div key={section.id} className="rounded-lg border border-gray-200 p-4 space-y-4">
              {section.label && (
                <h3 className="text-sm font-semibold text-gray-900">
                  {isRhaiis && section.id === 'model' ? 'Workload' : section.label}
                </h3>
              )}
              <div className="grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2">
                {section.fields.map((field) => {
                  if (field.type === 'hidden' || !isFieldVisible(field, values)) return null
                  if (isRhaiis && field.key === 'tp_size' && (!values.model || values.model === '__custom_model__')) return null
                  const fieldLabel = isRhaiis && field.key === 'slack'
                    ? 'Slack Notifications (always on)'
                    : field.label || field.key
                  const isSlackAlwaysOn = isRhaiis && field.key === 'slack' && activeMode.id === 'single'
                  return (
                    <div key={field.key} className={clsx(field.type === 'textarea' ? 'sm:col-span-2' : '', field.key === 'model' || field.key === 'workload' ? 'sm:col-span-2' : '')}>
                      <label className="block text-sm font-medium text-gray-700">
                        {fieldLabel}
                        {field.required && <span className="text-red-500 ml-0.5">*</span>}
                      </label>
                      <FieldControl
                        field={field}
                        value={values[field.key]}
                        onChange={(v) => handleFieldChange(field, v)}
                        options={visibleOptions(field, values)}
                        disabled={isSlackAlwaysOn}
                      />
                      {field.help && <p className="mt-1 text-xs text-gray-400">{field.help}</p>}

                      {isRhaiis && field.key === 'model' && values.model === '__custom_model__' && (
                        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
                          <label className="text-xs text-gray-600">
                            Model Name
                            <input
                              type="text"
                              value={String(values.custom_model_name || '')}
                              onChange={(e) => setFieldValue('custom_model_name', e.target.value)}
                              className="input mt-1"
                              placeholder="e.g. Llama-3.3-70B-Instruct-FP8"
                            />
                          </label>
                          <label className="text-xs text-gray-600 sm:col-span-2">
                            HuggingFace Model ID <span className="text-red-500">*</span>
                            <input
                              type="text"
                              value={String(values.custom_model_id || '')}
                              onChange={(e) => setFieldValue('custom_model_id', e.target.value)}
                              className="input mt-1"
                              placeholder="org/model"
                            />
                          </label>
                          <label className="text-xs text-gray-600">
                            TP Size
                            <input
                              type="number"
                              min={1}
                              value={Number(values.custom_model_tp) || 1}
                              onChange={(e) => {
                                const next = e.target.value === '' ? '' : Number(e.target.value)
                                setValues((prev) => ({
                                  ...prev,
                                  custom_model_tp: next,
                                  tp_size: next,
                                  gpu_count: Math.max(Number(prev.gpu_count) || 1, Number(next) || 1),
                                }))
                              }}
                              className="input mt-1"
                            />
                          </label>
                        </div>
                      )}

                      {isRhaiis && field.key === 'workload' && customWorkloadSelected && (
                        <div className="mt-3 space-y-3 rounded-md border border-gray-200 bg-gray-50 p-3">
                          <label className="block text-xs text-gray-600">
                            Data <span className="text-red-500">*</span>
                            <input
                              type="text"
                              value={String(values.custom_workload_data || '')}
                              onChange={(e) => setFieldValue('custom_workload_data', e.target.value)}
                              className="input mt-1"
                              placeholder="prompt_tokens=1000,output_tokens=1000"
                            />
                          </label>
                          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                            <label className="text-xs text-gray-600">
                              Concurrencies <span className="text-red-500">*</span>
                              <input
                                type="text"
                                value={String(values.custom_workload_concurrencies || '')}
                                onChange={(e) => setFieldValue('custom_workload_concurrencies', e.target.value)}
                                className="input mt-1"
                                placeholder="1,50,100"
                              />
                            </label>
                            <label className="text-xs text-gray-600">
                              Max Seconds
                              <input
                                type="number"
                                min={1}
                                value={Number(values.custom_workload_max_seconds) || 450}
                                onChange={(e) => setFieldValue('custom_workload_max_seconds', e.target.value === '' ? '' : Number(e.target.value))}
                                className="input mt-1"
                              />
                            </label>
                            <label className="text-xs text-gray-600">
                              Samples (optional)
                              <input
                                type="number"
                                min={1}
                                value={values.custom_workload_samples === '' ? '' : Number(values.custom_workload_samples) || ''}
                                onChange={(e) => setFieldValue('custom_workload_samples', e.target.value === '' ? '' : Number(e.target.value))}
                                className="input mt-1"
                              />
                            </label>
                          </div>
                        </div>
                      )}
                    </div>
                  )
                })}
                {isRhaiis && section.id === 'infra' && (
                  <div>
                    <label className="block text-sm font-medium text-gray-700">GPU Type</label>
                    <input
                      type="text"
                      value={gpuType || 'Select a registered cluster or profile'}
                      readOnly
                      className="input mt-1 bg-gray-50 text-gray-500"
                    />
                    <p className="mt-1 text-xs text-gray-400">Derived from the selected Control Center cluster, with the RHAIIS profile as fallback.</p>
                  </div>
                )}
              </div>
            </div>
          ))}

          {isRhaiis && (
            <div className="rounded-lg border border-gray-200 p-4">
              <label className="block text-sm font-medium text-gray-700">Advanced Config Overrides</label>
              <textarea
                value={String(values.advanced_overrides || '')}
                onChange={(e) => setFieldValue('advanced_overrides', e.target.value)}
                rows={4}
                className="input mt-1 font-mono"
                placeholder="key: value (one per line)\ne.g. experiment.concurrency: [32]"
              />
              <p className="mt-1 text-xs text-gray-400">
                Optional Forge overrides. Use one <code>key: value</code> per line; these are applied last.
              </p>
            </div>
          )}

          {isMatrix && (
            <div className="space-y-4 rounded-lg border border-gray-200 p-4">
              <div>
                <label className="block text-sm font-medium text-gray-700">Pipeline</label>
                {activeMode.pipelines.length > 0 ? (
                  <select
                    value={pipelineKey}
                    onChange={(e) => setPipelineKey(e.target.value)}
                    className="input mt-1"
                  >
                    {activeMode.pipelines.map((p) => (
                      <option key={p.key} value={p.key}>
                        {p.label || p.key}
                      </option>
                    ))}
                  </select>
                ) : (
                  <p className="mt-1 text-xs text-gray-400">
                    No pipelines are published for {schema.title || project} yet.
                  </p>
                )}
              </div>

              {selectedPipeline && (
                <>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Models</label>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedPipeline.models.map((m) => {
                        const checked = selectedModels.includes(m.key)
                        return (
                          <label
                            key={m.key}
                            title={formatOverridesTooltip(m.overrides)}
                            className={clsx(
                              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border cursor-pointer select-none',
                              checked
                                ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                                : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
                            )}
                          >
                            <input
                              type="checkbox"
                              className="h-3 w-3 rounded border-gray-300 text-indigo-600"
                              checked={checked}
                              onChange={() =>
                                setSelectedModels((prev) =>
                                  checked ? prev.filter((k) => k !== m.key) : [...prev, m.key]
                                )
                              }
                            />
                            {m.label || m.key}
                            {m.tp ? <span className="text-gray-400">tp{m.tp}</span> : null}
                          </label>
                        )
                      })}
                      {selectedPipeline.models.length === 0 && (
                        <p className="text-xs text-gray-400">This pipeline declares no models.</p>
                      )}
                    </div>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Workloads</label>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedPipeline.workloads.map((w) => {
                        const checked = selectedWorkloads.includes(w)
                        return (
                          <label
                            key={w}
                            className={clsx(
                              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border cursor-pointer select-none',
                              checked
                                ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                                : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
                            )}
                          >
                            <input
                              type="checkbox"
                              className="h-3 w-3 rounded border-gray-300 text-indigo-600"
                              checked={checked}
                              onChange={() =>
                                setSelectedWorkloads((prev) =>
                                  checked ? prev.filter((x) => x !== w) : [...prev, w]
                                )
                              }
                            />
                            {w}
                          </label>
                        )
                      })}
                      {selectedPipeline.workloads.length === 0 && (
                        <p className="text-xs text-gray-400">This pipeline declares no workloads.</p>
                      )}
                    </div>
                  </div>

                  <p className="text-xs text-gray-400">
                    Submits {selectedModels.length} job(s), one per model, each covering{' '}
                    {selectedWorkloads.length} workload(s).
                  </p>
                </>
              )}
            </div>
          )}

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
        </>
      )}

      {step === 3 && (() => {
        const { args, overrides } = collectSharedArgsAndOverrides()
        const shared = {
          project,
          cluster: basics.cluster,
          pipeline: basics.pipeline,
          owner: basics.owner,
          priority: basics.priority,
          exclusive: basics.exclusive,
          pullSha: basics.useLatestMain ? 'main' : basics.pullSha,
          gpuType,
          gpuCount,
          args,
          configOverrides: overrides,
          schedule: basics.scheduling.mode === 'recurring' ? basics.scheduling.scheduleUtc : '',
          scheduledStartTime: basics.scheduling.mode === 'defer' ? basics.scheduling.scheduledStartTimeUtc : null,
        }
        const yamlText = isMatrix
          ? selectedPipeline
            ? toYamlPreview(
                buildMatrixJobPreviews({
                  ...shared,
                  models: selectedModels.map((key) => {
                    const m = selectedPipeline.models.find((mm) => mm.key === key)
                    return {
                      key,
                      overrides: stringifyOverrides(m?.overrides || {}),
                      gpuCount: m?.tp ?? gpuCount,
                    }
                  }),
                  workloads: selectedWorkloads,
                })
              )
            : ''
          : toYamlPreview(buildSingleJobPreview(shared))

        return (
          <div className="lg:grid lg:grid-cols-[minmax(0,1fr)_380px] lg:gap-6 lg:items-start">
            <div className="space-y-4 min-w-0">
              <ReviewSection title="Basics">
                <ReviewRow label="Project" value={project} />
                <ReviewRow label="Cluster" value={basics.cluster} missing={!basics.cluster.trim()} />
                <ReviewRow label="Pipeline" value={basics.pipeline} />
                {isRhaiis && <ReviewRow label="GPU type / count" value={`${gpuType || 'auto'} / ${gpuCount}`} />}
                {basics.owner && <ReviewRow label="Owner" value={basics.owner} />}
                <ReviewRow label="Priority" value={basics.priority} />
                {basics.exclusive && <ReviewRow label="Exclusive" value="Yes" />}
                {project === 'rhaiis' && basics.useLatestMain && (
                  <ReviewRow label="Build source" value="Latest main (un-pinned)" />
                )}
                {basics.pullSha && (
                  <ReviewRow label={project === 'rhaiis' ? 'Build source' : 'Pull Request'} value={basics.prLabel} mono={!basics.prLabel || basics.prLabel === basics.pullSha} />
                )}
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

              {isRhaiis && (values.model === '__custom_model__' || customWorkloadSelected || String(values.advanced_overrides || '').trim()) && (
                <ReviewSection title="Additional RHAIIS Settings">
                  {values.model === '__custom_model__' && (
                    <>
                      <ReviewRow label="Custom model name" value={String(values.custom_model_name || '') || '-'} />
                      <ReviewRow label="HuggingFace model ID" value={String(values.custom_model_id || '')} missing={!String(values.custom_model_id || '').trim()} />
                      <ReviewRow label="TP size" value={String(tpSize)} />
                    </>
                  )}
                  {customWorkloadSelected && (
                    <>
                      <ReviewRow label="Custom workload data" value={String(values.custom_workload_data || '')} missing={!String(values.custom_workload_data || '').trim()} />
                      <ReviewRow label="Custom concurrencies" value={String(values.custom_workload_concurrencies || '')} missing={!String(values.custom_workload_concurrencies || '').trim()} />
                      <ReviewRow label="Custom max seconds" value={String(values.custom_workload_max_seconds || '')} />
                      {values.custom_workload_samples !== '' && <ReviewRow label="Custom samples" value={String(values.custom_workload_samples)} />}
                    </>
                  )}
                  {String(values.advanced_overrides || '').trim() && (
                    <ReviewRow label="Advanced overrides" value={String(values.advanced_overrides)} mono />
                  )}
                </ReviewSection>
              )}

              {isMatrix && (
                <ReviewSection title="Matrix Selection">
                  <ReviewRow
                    label="Pipeline"
                    value={selectedPipeline?.label || selectedPipeline?.key}
                    missing={!selectedPipeline}
                  />
                  <ReviewRow
                    label="Models"
                    value={selectedModels
                      .map((k) => selectedPipeline?.models.find((m) => m.key === k)?.label || k)
                      .join(', ')}
                    missing={selectedModels.length === 0}
                  />
                  <ReviewRow
                    label="Workloads"
                    value={selectedWorkloads.join(', ')}
                    missing={selectedWorkloads.length === 0}
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
}: {
  field: UiField
  value: unknown
  onChange: (value: unknown) => void
  /** Defaults to `field.options` — pass a `visibleOptions(field, values)` result to apply `restrict_if`. */
  options?: UiOption[]
  disabled?: boolean
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
          {opts.map((opt) => (
            <button
              key={opt.value}
              type="button"
              title={formatOverridesTooltip(opt.overrides)}
              disabled={disabled}
              onClick={() => onChange(value === opt.value ? '' : opt.value)}
              className={clsx(
                'px-3 py-1.5 rounded-md text-xs font-medium border transition-colors',
                value === opt.value
                  ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                  : 'border-gray-300 bg-white text-gray-600 hover:border-gray-400'
              )}
            >
              {opt.label || opt.value}
            </button>
          ))}
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
