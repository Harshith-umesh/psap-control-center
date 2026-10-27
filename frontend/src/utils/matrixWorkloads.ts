import type { UiPipeline } from '../types'

export function getMatrixWorkloadOptions(project: string, pipeline?: UiPipeline): string[] {
  if (!pipeline) return []
  const workloads = [...pipeline.workloads]
  for (const model of pipeline.models) {
    for (const workload of model.workloads || []) {
      if (!workloads.includes(workload)) workloads.push(workload)
    }
  }
  if (project.trim().toLowerCase() !== 'rhaiis') return workloads

  const workloadOrder = ['profile1', 'profile4', 'profile5', 'profile7', 'profile6']
  return workloads.sort((left, right) => {
    const leftIndex = workloadOrder.indexOf(left)
    const rightIndex = workloadOrder.indexOf(right)
    return (leftIndex < 0 ? workloadOrder.length : leftIndex)
      - (rightIndex < 0 ? workloadOrder.length : rightIndex)
  })
}
