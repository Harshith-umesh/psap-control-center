import type { Dispatch, ReactNode, SetStateAction } from 'react'
import ReviewRow, { ReviewSection } from '../ReviewRow'
import type { UiField, UiMode, UiSection } from '../../types'

export interface ProjectSubmitBasics {
  cluster: string
  clusterGpuType?: string
}

export interface ProjectDerivedState {
  gpuType: string
  selectedModelTp: number
  tpSize: number
  gpuCount: number
}

interface FieldChangeContext {
  field: UiField
  value: unknown
  values: Record<string, unknown>
  modelField?: UiField
  engineField?: UiField
  autoEngineVersion: string
}

interface FieldChangeResult {
  updates: Record<string, unknown>
  autoEngineVersion?: string
}

interface SubmissionContext {
  values: Record<string, unknown>
  modelField?: UiField
  workloadField?: UiField
  derived: ProjectDerivedState
  overrides: Record<string, string>
}

interface RenderContext {
  field: UiField
  values: Record<string, unknown>
  derived: ProjectDerivedState
  setFieldValue: (key: string, value: unknown) => void
  setValues: Dispatch<SetStateAction<Record<string, unknown>>>
}

export interface ProjectSubmitAdapter {
  requiresBuildSource: boolean
  buildSourceLabel: string
  getInitialValues(mode: UiMode): Record<string, unknown>
  getDerivedState(
    basics: ProjectSubmitBasics,
    activeFields: UiField[],
    values: Record<string, unknown>,
  ): ProjectDerivedState
  handleFieldChange(context: FieldChangeContext): FieldChangeResult
  appendBasicsArgs(args: string[], basics: ProjectSubmitBasics): void
  skipField(field: UiField): boolean
  augmentSubmission(context: SubmissionContext): void
  validate(
    activeMode: UiMode,
    values: Record<string, unknown>,
    derived: ProjectDerivedState,
  ): boolean
  isCustomWorkloadSelected(values: Record<string, unknown>): boolean
  sectionLabel(section: UiSection): string
  fieldPresentation(
    field: UiField,
    values: Record<string, unknown>,
    activeMode: UiMode,
  ): { hidden: boolean; label: string; disabled: boolean }
  renderFieldExtras(context: RenderContext): ReactNode
  renderSectionExtras(
    section: UiSection,
    context: Omit<RenderContext, 'field'>,
  ): ReactNode
  renderAdditionalConfig(context: Omit<RenderContext, 'field'>): ReactNode
  renderReviewBasics(derived: ProjectDerivedState): ReactNode
  renderAdditionalReview(
    values: Record<string, unknown>,
    derived: ProjectDerivedState,
  ): ReactNode
}

const CLUSTER_GPU_TYPES: Record<string, string> = {
  hera: 'h200',
  zeus: 'h200',
  'old-zeus': 'h200',
  b200: 'b200',
  mi355x: 'amd',
}

function stringifyValue(value: unknown): string {
  if (Array.isArray(value)) return JSON.stringify(value)
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return value == null ? '' : String(value)
}

function rawOptionValue(field: UiField | undefined, value: unknown): unknown {
  const option = field?.options.find((item) => item.value === value)
  if (option && field?.maps_to && Object.prototype.hasOwnProperty.call(option.overrides, field.maps_to)) {
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
  const raw = vllmArgs?.['tensor-parallel-size']
    ?? sglangArgs?.['tp-size']
    ?? extra.tensor_parallel
    ?? extra.tp_size
    ?? extra.tp
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function parseOverrideLines(raw: string): Record<string, string> {
  const overrides: Record<string, string> = {}
  raw.split('\n').forEach((line) => {
    const index = line.indexOf(':')
    if (index > 0) overrides[line.slice(0, index).trim()] = line.slice(index + 1).trim()
  })
  return overrides
}

const rhaiisSubmitAdapter: ProjectSubmitAdapter = {
  requiresBuildSource: true,
  buildSourceLabel: 'Build source',

  getInitialValues(mode) {
    const initial: Record<string, unknown> = {}
    for (const field of mode.sections.flatMap((section) => section.fields)) {
      if (field.key === 'warmup' || field.key === 'slack') initial[field.key] = true
    }
    return {
      ...initial,
      advanced_overrides: '',
      custom_model_tp: 1,
      custom_workload_data: 'prompt_tokens=1000,output_tokens=1000',
      custom_workload_concurrencies: '1',
      custom_workload_max_seconds: 450,
      custom_workload_samples: '',
    }
  },

  getDerivedState(basics, activeFields, values) {
    const modelField = activeFields.find((field) => field.key === 'model')
    const selectedModelTp = modelTpSize(modelField, values.model) || 1
    const tpSize = Math.max(Number(values.tp_size) || selectedModelTp, 1)
    return {
      gpuType: basics.clusterGpuType || CLUSTER_GPU_TYPES[basics.cluster.trim().toLowerCase()] || '',
      selectedModelTp,
      tpSize,
      gpuCount: Math.max(Number(values.gpu_count) || 1, tpSize),
    }
  },

  handleFieldChange({ field, value, values, modelField, engineField, autoEngineVersion }): FieldChangeResult {
    if (field.key === 'model') {
      const nextTp = value === '__custom_model__' ? 1 : modelTpSize(modelField, value) || 1
      return { updates: { model: value, tp_size: nextTp, gpu_count: nextTp } }
    }
    if (field.key === 'tp_size') {
      const nextTp = Math.max(Number(value) || 1, 1)
      return {
        updates: {
          tp_size: nextTp,
          gpu_count: Math.max(Number(values.gpu_count) || 1, nextTp),
        },
      }
    }
    if (field.key === 'engine' || field.key === 'accelerator') {
      const nextEngine = field.key === 'engine' ? value : values.engine
      const nextAccelerator = field.key === 'accelerator' ? value : values.accelerator
      const engineOption = engineField?.options.find((option) => option.value === nextEngine)
      const images = engineOption?.extra?.images as Record<string, unknown> | undefined
      const defaultImage = typeof images?.[String(nextAccelerator)] === 'string'
        ? String(images[String(nextAccelerator)])
        : ''
      if (defaultImage && (!values.engine_version || values.engine_version === autoEngineVersion)) {
        return { updates: { engine_version: defaultImage }, autoEngineVersion: defaultImage }
      }
    }
    if (field.key === 'engine_version') return { updates: {}, autoEngineVersion: '' }
    if (field.key === 'agent_analysis' && value === true) {
      return { updates: { compare_versions: true } }
    }
    return { updates: {} }
  },

  appendBasicsArgs(args, basics) {
    if (basics.cluster.trim()) args.push(basics.cluster.trim())
  },

  skipField(field) {
    return field.key === 'model' || field.key === 'workload'
  },

  augmentSubmission({ values, modelField, workloadField, derived, overrides }) {
    const selectedModel = values.model
    if (selectedModel === '__custom_model__') {
      overrides['tests.rhaiis.model_key'] = 'custom'
      const customName = String(values.custom_model_name || '').trim()
      const customId = String(values.custom_model_id || '').trim()
      if (customName) overrides['models.custom.name'] = customName
      if (customId) overrides['models.custom.hf_model_id'] = customId
      overrides['rhaiis.engines.vllm.args.tensor-parallel-size'] = stringifyValue(derived.tpSize)
    } else if (selectedModel) {
      overrides['tests.rhaiis.model_key'] = stringifyValue(rawOptionValue(modelField, selectedModel))
      if (Number(values.tp_size) && Number(values.tp_size) !== derived.selectedModelTp) {
        overrides['rhaiis.engines.vllm.args.tensor-parallel-size'] = stringifyValue(derived.tpSize)
      }
    }

    const selectedWorkloads = Array.isArray(values.workload) ? values.workload as string[] : []
    if (selectedWorkloads.length > 0) {
      const workloadKeys = selectedWorkloads.map((item) =>
        item === '__custom_workload__' ? 'custom' : rawOptionValue(workloadField, item)
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
    delete overrides['tests.rhaiis.slack_member_id']

    const compareVersion = String(values.compare_version || '').trim()
    if (compareVersion) overrides['tests.rhaiis.compare_version'] = compareVersion
    delete overrides['rhaiis.compare_versions.enabled']

    const engine = String(values.engine || '').trim()
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
  },

  validate(activeMode, values, derived) {
    const customModelValid = values.model !== '__custom_model__'
      || !!String(values.custom_model_id || '').trim()
    const customWorkloadSelected = this.isCustomWorkloadSelected(values)
    const customWorkloadValid = !customWorkloadSelected || (
      !!String(values.custom_workload_data || '').trim()
      && !!String(values.custom_workload_concurrencies || '').trim()
    )
    const sizingValid = derived.gpuCount >= derived.tpSize
    const slackValid = activeMode.id !== 'single'
      || values.slack !== true
      || !!String(values.slack_member_id || '').trim()
    return customModelValid && customWorkloadValid && sizingValid && slackValid
  },

  isCustomWorkloadSelected(values) {
    return Array.isArray(values.workload) && values.workload.includes('__custom_workload__')
  },

  sectionLabel(section) {
    return section.id === 'model' ? 'Workload' : section.label
  },

  fieldPresentation(field, values, activeMode) {
    return {
      hidden: field.key === 'tp_size' && (!values.model || values.model === '__custom_model__'),
      label: field.key === 'slack' ? 'Slack Notifications (always on)' : field.label || field.key,
      disabled: field.key === 'slack' && activeMode.id === 'single',
    }
  },

  renderFieldExtras({ field, values, setFieldValue, setValues }) {
    if (field.key === 'model' && values.model === '__custom_model__') {
      return (
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
      )
    }

    if (field.key === 'workload' && this.isCustomWorkloadSelected(values)) {
      return (
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
      )
    }
    return null
  },

  renderSectionExtras(section, { derived }) {
    if (section.id !== 'infra') return null
    return (
      <div>
        <label className="block text-sm font-medium text-gray-700">GPU Type</label>
        <input
          type="text"
          value={derived.gpuType || 'Select a registered cluster or profile'}
          readOnly
          className="input mt-1 bg-gray-50 text-gray-500"
        />
        <p className="mt-1 text-xs text-gray-400">Derived from the selected Control Center cluster, with the RHAIIS profile as fallback.</p>
      </div>
    )
  },

  renderAdditionalConfig({ values, setFieldValue }) {
    return (
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
    )
  },

  renderReviewBasics(derived) {
    return <ReviewRow label="GPU type / count" value={`${derived.gpuType || 'auto'} / ${derived.gpuCount}`} />
  },

  renderAdditionalReview(values, derived) {
    const customWorkloadSelected = this.isCustomWorkloadSelected(values)
    const hasAdditionalSettings = values.model === '__custom_model__'
      || customWorkloadSelected
      || String(values.advanced_overrides || '').trim()
    if (!hasAdditionalSettings) return null
    return (
      <ReviewSection title="Additional RHAIIS Settings">
        {values.model === '__custom_model__' && (
          <>
            <ReviewRow label="Custom model name" value={String(values.custom_model_name || '') || '-'} />
            <ReviewRow label="HuggingFace model ID" value={String(values.custom_model_id || '')} missing={!String(values.custom_model_id || '').trim()} />
            <ReviewRow label="TP size" value={String(derived.tpSize)} />
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
    )
  },
}

export function getProjectSubmitAdapter(project: string): ProjectSubmitAdapter | null {
  return project === 'rhaiis' ? rhaiisSubmitAdapter : null
}
