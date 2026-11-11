import type { FournosJobSummary } from '../types'

const QUEUED_STATUSES = new Set(['pending', 'resolving', 'admitted', 'queued', 'submitting', 'created'])
const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'stopped', 'cancelled', 'canceled', 'completed'])

export interface ClusterActivityGroups {
  queued: FournosJobSummary[]
  running: FournosJobSummary[]
  scheduled: FournosJobSummary[]
  other: FournosJobSummary[]
}

export function groupClusterActivity(jobs: FournosJobSummary[], now = Date.now()): ClusterActivityGroups {
  const groups: ClusterActivityGroups = { queued: [], running: [], scheduled: [], other: [] }

  for (const job of jobs) {
    const status = job.status.trim().toLowerCase()
    if (TERMINAL_STATUSES.has(status)) continue

    const scheduledAt = job.scheduled_start_time ? Date.parse(job.scheduled_start_time) : Number.NaN
    if (Number.isFinite(scheduledAt) && scheduledAt > now) {
      groups.scheduled.push(job)
    } else if (status === 'running') {
      groups.running.push(job)
    } else if (QUEUED_STATUSES.has(status)) {
      groups.queued.push(job)
    } else {
      groups.other.push(job)
    }
  }

  return groups
}
