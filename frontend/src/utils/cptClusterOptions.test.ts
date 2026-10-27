import { describe, expect, it } from 'vitest'
import type { HearthCluster } from '../types'
import {
  filterCptClusterOptions,
  getHearthClusterOptions,
  isCptClusterOptionSelected,
} from './cptClusterOptions'

function hearthCluster(name: string, shortName: string, model: string): HearthCluster {
  return {
    name,
    kubeconfig_secret: `${name}-kubeconfig`,
    locked: false,
    conditions: [],
    hardware: {
      gpus: [{ vendor: 'NVIDIA', model, short_name: shortName, count: 8 }],
      total_gpus: 8,
      consecutive_failures: 0,
    },
  }
}

const options = getHearthClusterOptions([
  hearthCluster('hera', 'h200', 'NVIDIA H200'),
  hearthCluster('b200', 'b200', 'NVIDIA B200'),
  hearthCluster('mi355x', 'amd', 'AMD Instinct MI355X'),
])

describe('CPT cluster choices from Hearth', () => {
  it('preserves canonical cluster IDs and includes discovered GPU metadata', () => {
    expect(options.map((option) => option.name)).toEqual(['hera', 'b200', 'mi355x'])
    expect(options.find((option) => option.name === 'mi355x')?.gpu_type).toContain('MI355X')
  })

  it('matches the pipeline allowlist against canonical Hearth names, not display labels or GPU aliases', () => {
    const filtered = filterCptClusterOptions(options, ['HERA', 'b200'], 'unrelated-gpu-label')
    expect(filtered.map((option) => option.name)).toEqual(['hera', 'b200'])
  })

  it('uses discovered GPU metadata when the pipeline has no explicit cluster allowlist', () => {
    const filtered = filterCptClusterOptions(options, [], 'mi355x')
    expect(filtered.map((option) => option.name)).toEqual(['mi355x'])
  })

  it('validates a selected canonical cluster name without case sensitivity', () => {
    expect(isCptClusterOptionSelected('Mi355X', options)).toBe(true)
    expect(isCptClusterOptionSelected('B200 - 8xB200', options)).toBe(false)
  })
})
