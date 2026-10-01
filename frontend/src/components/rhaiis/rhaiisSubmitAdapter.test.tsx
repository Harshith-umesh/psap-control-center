import { describe, expect, it } from 'vitest'
import { mergeMatrixConfigOverrides } from '../../utils/fournosJobPreview'
import {
  canonicalFournosGpuType,
  getProjectSubmitAdapter,
} from './rhaiisSubmitAdapter'

describe('RHAIIS submission adapter', () => {
  const adapter = getProjectSubmitAdapter('rhaiis')!

  it('normalizes registered-cluster GPU product names to Fournos short names', () => {
    expect(canonicalFournosGpuType('NVIDIA-H200')).toBe('h200')
    expect(canonicalFournosGpuType('AMD Instinct MI355X')).toBe('mi355x')
    expect(canonicalFournosGpuType('amd')).toBe('')
    expect(canonicalFournosGpuType('not a known accelerator')).toBe('')
    expect(
      adapter.getDerivedState(
        { cluster: 'mi355x', clusterGpuType: 'AMD Instinct MI355X' },
        [],
        {},
      ).gpuType,
    ).toBe('mi355x')
  })

  it.each([
    ['vllm', 'rhaiis.engines.vllm.args.tensor-parallel-size'],
    ['sglang', 'rhaiis.engines.sglang.args.tp-size'],
    ['trtllm', 'rhaiis.engines.trtllm.args.tp_size'],
  ])('writes the TP override for %s', (engine, expectedKey) => {
    const overrides: Record<string, string> = {}
    adapter.augmentSubmission({
      values: {
        engine,
        model: '__custom_model__',
        custom_model_id: 'org/model',
        workload: [],
      },
      derived: { gpuType: 'h200', selectedModelTp: 1, tpSize: 4, gpuCount: 4 },
      overrides,
    })

    expect(overrides[expectedKey]).toBe('4')
  })

  it('maps custom workload concurrency input to Forge rates', () => {
    const overrides: Record<string, string> = {}
    adapter.augmentSubmission({
      values: {
        engine: 'vllm',
        workload: ['__custom_workload__'],
        custom_workload_data: 'prompt_tokens=1000,output_tokens=1000',
        custom_workload_concurrencies: '1,32,64',
      },
      derived: { gpuType: 'h200', selectedModelTp: 1, tpSize: 1, gpuCount: 1 },
      overrides,
    })

    expect(overrides['workloads.custom.rates']).toBe('[1,32,64]')
    expect(overrides).not.toHaveProperty('workloads.custom.concurrencies')
  })

  it('requires a canonical GPU type before submission', () => {
    expect(
      adapter.validate(
        {} as never,
        { workload: [] },
        { gpuType: '', selectedModelTp: 1, tpSize: 1, gpuCount: 1 },
      ),
    ).toBe(false)
  })
})

describe('matrix override precedence', () => {
  it('applies explicit form and advanced overrides after pipeline defaults', () => {
    expect(
      mergeMatrixConfigOverrides(
        { shared: 'pipeline', pipelineOnly: true },
        { shared: 'user', userOnly: 'value' },
      ),
    ).toEqual({
      shared: 'user',
      pipelineOnly: 'true',
      userOnly: 'value',
    })
  })
})
