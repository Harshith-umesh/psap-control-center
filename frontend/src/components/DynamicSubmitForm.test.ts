import { describe, expect, it } from 'vitest'
import { getMatrixWorkloadOptions } from '../utils/matrixWorkloads'
import type { UiPipeline } from '../types'

const pipeline: UiPipeline = {
  key: 'cpt-rhaiis-example',
  label: 'Example CPT',
  models: [
    { key: 'model-a', label: 'Model A', overrides: {}, workloads: ['profile1', 'ci-quick'] },
    { key: 'model-b', label: 'Model B', overrides: {}, workloads: ['profile5'] },
  ],
  workloads: ['profile4', 'profile1'],
  overrides: {},
}

describe('getMatrixWorkloadOptions', () => {
  it('includes model-specific RHAIIS workloads in the dashboard profile order', () => {
    expect(getMatrixWorkloadOptions('rhaiis', pipeline)).toEqual([
      'profile1',
      'profile4',
      'profile5',
      'ci-quick',
    ])
  })

  it('keeps declared ordering for other projects while appending model-specific workloads', () => {
    expect(getMatrixWorkloadOptions('other-project', pipeline)).toEqual([
      'profile4',
      'profile1',
      'ci-quick',
      'profile5',
    ])
  })

  it('returns no workload choices before a pipeline is selected', () => {
    expect(getMatrixWorkloadOptions('rhaiis')).toEqual([])
  })
})
