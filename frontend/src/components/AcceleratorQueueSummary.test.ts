import { describe, expect, it } from 'vitest'
import { groupClusterActivity } from '../utils/clusterActivity'
import type { FournosJobSummary } from '../types'

function job(name: string, status: string, scheduled_start_time: string | null = null): FournosJobSummary {
  return { name, status, scheduled_start_time } as FournosJobSummary
}

describe('groupClusterActivity', () => {
  const now = Date.parse('2026-10-26T18:00:00Z')

  it('separates queued and running jobs from terminal jobs', () => {
    const groups = groupClusterActivity([
      job('pending', 'Pending'),
      job('admitted', 'Admitted'),
      job('resolving', 'Resolving'),
      job('running', 'Running'),
      job('finished', 'Succeeded'),
    ], now)

    expect(groups.queued.map((item) => item.name)).toEqual(['pending', 'admitted', 'resolving'])
    expect(groups.running.map((item) => item.name)).toEqual(['running'])
    expect(groups.scheduled).toEqual([])
    expect(groups.other).toEqual([])
  })

  it('classifies future-start jobs as scheduled', () => {
    const groups = groupClusterActivity([
      job('later', 'Pending', '2026-10-26T19:00:00Z'),
      job('already-started', 'Pending', '2026-10-26T17:00:00Z'),
    ], now)

    expect(groups.scheduled.map((item) => item.name)).toEqual(['later'])
    expect(groups.queued.map((item) => item.name)).toEqual(['already-started'])
  })
})
