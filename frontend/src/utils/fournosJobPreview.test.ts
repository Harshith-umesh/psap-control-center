import { describe, expect, it } from 'vitest'
import { buildMatrixJobPreviews, buildSingleJobPreview } from './fournosJobPreview'

const baseInput = {
  project: 'rhaiis',
  cluster: 'hera',
  pipeline: 'forge-full',
  owner: 'tester',
  priority: 'nightly',
  exclusive: false,
  pullSha: '',
  args: ['nvidia', 'vllm'],
  configOverrides: {},
}

describe('FournosJob priority preview', () => {
  it('writes the selected WorkloadPriorityClass name to spec.priority', () => {
    const preview = buildSingleJobPreview(baseInput)
    expect((preview.spec as Record<string, unknown>).priority).toBe('nightly')
  })

  it('omits spec.priority for cluster default on single and matrix jobs', () => {
    const single = buildSingleJobPreview({ ...baseInput, priority: '' })
    const matrix = buildMatrixJobPreviews({
      ...baseInput,
      priority: '',
      models: [{ key: 'model-a', overrides: {} }],
      workloads: ['profile1'],
    })

    expect(single.spec).not.toHaveProperty('priority')
    expect(matrix[0].spec).not.toHaveProperty('priority')
  })
})
