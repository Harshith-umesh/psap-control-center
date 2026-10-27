import type { HearthCluster } from '../types'

export interface ClusterPickerOption {
  id: string
  /** Canonical cluster identifier used in FournosJob.spec.cluster. */
  name: string
  provider?: string
  gpu_type?: string
  gpu_count?: string
}

export function normalizeClusterName(name: string): string {
  return name.trim().toLowerCase()
}

/** Adapt Hearth's FournosCluster resources into picker options. */
export function getHearthClusterOptions(clusters: HearthCluster[]): ClusterPickerOption[] {
  return clusters.map((cluster) => {
    const gpuLabels = cluster.hardware?.gpus.flatMap((gpu) =>
      [gpu.short_name, gpu.model, gpu.vendor]
        .map((value) => value.trim())
        .filter(Boolean),
    ) ?? []
    const gpuType = [...new Set(gpuLabels)].join(' / ')
    const totalGpus = cluster.hardware?.total_gpus

    return {
      id: cluster.name,
      name: cluster.name,
      provider: 'Hearth',
      ...(gpuType ? { gpu_type: gpuType } : {}),
      ...(totalGpus ? { gpu_count: String(totalGpus) } : {}),
    }
  })
}

/**
 * Forge's explicit __clusters list is authoritative. Use it to match Hearth's
 * canonical metadata.name, never the Control Center's human-readable cluster
 * label. Fall back to the pipeline GPU type only when Forge gives no cluster list.
 */
export function filterCptClusterOptions(
  options: ClusterPickerOption[],
  allowedClusterNames: string[],
  gpuType: string,
): ClusterPickerOption[] {
  const allowedNames = new Set(allowedClusterNames.map(normalizeClusterName).filter(Boolean))
  if (allowedNames.size > 0) {
    return options.filter((option) => allowedNames.has(normalizeClusterName(option.name)))
  }

  const expectedGpuType = normalizeClusterName(gpuType)
  if (!expectedGpuType) return options
  return options.filter((option) => normalizeClusterName(option.gpu_type || '').includes(expectedGpuType))
}

export function isCptClusterOptionSelected(value: string, options: ClusterPickerOption[]): boolean {
  const normalizedValue = normalizeClusterName(value)
  return !!normalizedValue && options.some((option) => normalizeClusterName(option.name) === normalizedValue)
}
