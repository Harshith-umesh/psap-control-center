import { dump } from 'js-yaml'
import type { PullRequestSelection } from '../types'

// Mirrors the FournosJob CR construction in backend/app/api/fournos.py
// (submit_job / submit_matrix) field-for-field, so what's previewed here on
// the Review & Submit step matches exactly what gets POSTed to the cluster.
// Defaults (namespace, apiVersion) match app/core/config.py's Settings
// defaults — if those are ever overridden via env vars in a real
// deployment, this preview is illustrative rather than exact.

const NAMESPACE = 'fournos-jobs'
const API_VERSION = 'fournos.dev/v1'

/** Mirrors fournos_k8s_client.sanitize_job_name(prefix). */
function sanitizeJobName(prefix: string): string {
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const ts =
    `${now.getUTCFullYear()}${pad(now.getUTCMonth() + 1)}${pad(now.getUTCDate())}-` +
    `${pad(now.getUTCHours())}${pad(now.getUTCMinutes())}${pad(now.getUTCSeconds())}`
  let name = `${prefix}-${ts}`.toLowerCase()
  name = name.replace(/[^a-z0-9-]/g, '-')
  name = name.replace(/-+/g, '-').replace(/^-|-$/g, '')
  return name.slice(0, 63)
}

interface SharedInput {
  project: string
  cluster: string
  pipeline: string
  owner: string
  priority: string
  exclusive: boolean
  pullSha: string
  pullRequest?: PullRequestSelection | null
  args: string[]
  configOverrides: Record<string, string>
  gpuType?: string
  gpuCount?: number
  /** Cron expression, UTC — mutually exclusive with scheduledStartTime. */
  schedule?: string
  /** ISO 8601 UTC — mutually exclusive with schedule. */
  scheduledStartTime?: string | null
}

function sourceEnvironment(input: Pick<SharedInput, 'pullSha' | 'pullRequest'>): Record<string, string> | null {
  if (input.pullRequest) {
    const [owner, name] = input.pullRequest.repository.split('/', 2)
    return {
      REPO_OWNER: owner,
      REPO_NAME: name,
      PULL_NUMBER: String(input.pullRequest.number),
      PULL_HEAD_REF: input.pullRequest.head_branch,
      PULL_PULL_SHA: input.pullRequest.requested_sha,
      CONTROL_CENTER_REQUESTED_SHA: input.pullRequest.requested_sha,
      CONTROL_CENTER_PR_URL: input.pullRequest.url,
    }
  }
  return input.pullSha.trim() ? { PULL_PULL_SHA: input.pullSha.trim() } : null
}

function applyScheduling(spec: Record<string, unknown>, input: Pick<SharedInput, 'schedule' | 'scheduledStartTime'>): void {
  if (input.schedule) spec.schedule = input.schedule
  else if (input.scheduledStartTime) spec.scheduledStartTime = input.scheduledStartTime
}

export function buildSingleJobPreview(input: SharedInput): Record<string, unknown> {
  const displayName = input.args.length > 0
    ? `${input.project} ${input.args.join(' ')}`.trim()
    : input.project

  const spec: Record<string, unknown> = {
    cluster: input.cluster,
    displayName,
    owner: input.owner || 'fournos-dashboard',
    pipeline: input.pipeline,
    exclusive: input.exclusive,
    priority: input.priority,
    executionEngine: {
      forge: {
        project: input.project,
        args: input.args,
        configOverrides: input.configOverrides,
      },
    },
  }
  if (input.gpuType?.trim()) {
    spec.hardware = { gpuType: input.gpuType.trim(), gpuCount: input.gpuCount || 1 }
  }
  const env = sourceEnvironment(input)
  if (env) spec.env = env
  applyScheduling(spec, input)

  return {
    apiVersion: API_VERSION,
    kind: 'FournosJob',
    metadata: {
      // The real name is only generated at submit time; shown here
      // illustratively with a preview timestamp.
      name: sanitizeJobName(`forge-${input.project}`),
      namespace: NAMESPACE,
    },
    spec,
  }
}

export function buildMatrixJobPreviews(
  input: SharedInput & {
    models: { key: string; overrides: Record<string, string>; gpuCount?: number | null }[]
    workloads: string[]
  }
): Record<string, unknown>[] {
  return input.models.map((model) => {
    const args = [...input.args, model.key, ...input.workloads]
    const overrides = { ...input.configOverrides, ...model.overrides }
    const displayName = `${input.project}-${model.key}-${input.cluster}`

    const spec: Record<string, unknown> = {
      cluster: input.cluster,
      displayName,
      owner: input.owner,
      pipeline: input.pipeline,
      exclusive: input.exclusive,
      priority: input.priority,
      executionEngine: {
        forge: { project: input.project, args, configOverrides: overrides },
      },
    }
    if (input.gpuType?.trim() || model.gpuCount) {
      spec.hardware = { gpuType: input.gpuType?.trim() || 'unknown', gpuCount: model.gpuCount || 1 }
    }
    const env = sourceEnvironment(input)
    if (env) spec.env = env
    applyScheduling(spec, input)

    return {
      apiVersion: API_VERSION,
      kind: 'FournosJob',
      metadata: {
        name: sanitizeJobName(`${input.project}-${model.key}`),
        namespace: NAMESPACE,
      },
      spec,
    }
  })
}

// Mirrors _VERSION_KEYS in backend/app/api/fournos.py.
const VERSION_KEYS: Record<string, string> = { mcp_gateway: 'infrastructure.mcp_gateway_version' }

export function withVersionOverride(
  project: string,
  version: string,
  overrides: Record<string, string>
): Record<string, string> {
  if (!version.trim()) return overrides
  const key = VERSION_KEYS[project] || 'infrastructure.version'
  return { ...overrides, [key]: version.trim() }
}

export function toYamlPreview(docs: Record<string, unknown> | Record<string, unknown>[]): string {
  const arr = Array.isArray(docs) ? docs : [docs]
  return arr.map((d) => dump(d, { indent: 2, lineWidth: 100, noRefs: true })).join('---\n')
}
