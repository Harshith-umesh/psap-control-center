import { useEffect, useMemo, useRef, useState } from 'react'
import { useClusters } from '../hooks/useClusters'
import type { ClusterPickerOption } from '../utils/cptClusterOptions'

// Searchable dropdown for picking a cluster registered in the Control
// Center's own Clusters page (GET /clusters) — separate from anything
// Forge-side. Still a free-text field underneath (you can submit against a
// cluster name that isn't registered yet), but as you type it filters and
// shows matching registered clusters to pick from — same look and
// interaction as the "Pull Request" search box next to it.

export default function ClusterCombobox({
  value,
  onChange,
  required = false,
  inputClassName = 'input mt-1',
  allowedClusterNames,
  allowedGpuTypes,
  clusterOptions,
  clusterOptionsLoading = false,
  clusterOptionsUnavailable = false,
  emptyStateMessage = 'No registered clusters are available.',
}: {
  value: string
  onChange: (value: string) => void
  required?: boolean
  /** Match whatever input styling the surrounding form uses. */
  inputClassName?: string
  /** Optional cluster-name allowlist used by constrained CPT pipelines. */
  allowedClusterNames?: string[]
  /** Optional GPU type allowlist used by accelerator-constrained CPT pipelines. */
  allowedGpuTypes?: string[]
  /** Optional canonical choices supplied by another registry, e.g. Hearth for CPT jobs. */
  clusterOptions?: ClusterPickerOption[]
  clusterOptionsLoading?: boolean
  clusterOptionsUnavailable?: boolean
  emptyStateMessage?: string
}) {
  const { data: clustersData } = useClusters(false, clusterOptions === undefined)
  const clusters = useMemo<ClusterPickerOption[]>(
    () => clusterOptions ?? clustersData?.clusters ?? [],
    [clusterOptions, clustersData],
  )
  const compatibleClusters = useMemo(() => {
    const names = allowedClusterNames?.length
      ? new Set(allowedClusterNames.map((name) => name.trim().toLowerCase()))
      : null
    const gpuTypes = allowedGpuTypes?.map((type) => type.toLowerCase()) || []
    return clusters.filter((cluster) => {
      if (names && !names.has(cluster.name.trim().toLowerCase())) return false
      // An explicit pipeline cluster allowlist is authoritative. GPU metadata
      // may be incomplete or vendor-only in Hearth, so don't reject a listed ID.
      if (names) return true
      if (!gpuTypes.length) return true
      const clusterGpuType = String(cluster.gpu_type || '').toLowerCase()
      return gpuTypes.some((gpuType) => clusterGpuType.includes(gpuType))
    })
  }, [clusters, allowedClusterNames, allowedGpuTypes])
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const filtered = useMemo(() => {
    const q = value.trim().toLowerCase()
    if (!q) return compatibleClusters
    return compatibleClusters.filter((c) => c.name.toLowerCase().includes(q))
  }, [compatibleClusters, value])
  const isConstrained = !!allowedClusterNames?.length || !!allowedGpuTypes?.length || clusterOptions !== undefined

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onClickOutside)
    return () => document.removeEventListener('mousedown', onClickOutside)
  }, [])

  return (
    <div className="relative" ref={containerRef}>
      <input
        type="text"
        value={value}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
        }}
        onFocus={() => setOpen(true)}
        className={inputClassName}
        placeholder="Search or type a cluster name..."
        autoComplete="off"
        required={required}
      />
      {open && (clusters.length > 0 || isConstrained || clusterOptionsLoading || clusterOptionsUnavailable) && (
        <div className="absolute z-20 mt-1 w-full max-h-60 overflow-y-auto rounded-md bg-white shadow-lg border border-gray-200">
          {clusterOptionsLoading ? (
            <div className="w-full text-left px-3 py-2 text-sm text-gray-500 border-b border-gray-100">
              Loading clusters from Hearth…
            </div>
          ) : clusterOptionsUnavailable ? (
            <div className="w-full text-left px-3 py-2 text-sm text-amber-700 border-b border-gray-100">
              Hearth is unavailable. Check the Hearth connection and try again.
            </div>
          ) : compatibleClusters.length === 0 ? (
            <div className="w-full text-left px-3 py-2 text-sm text-gray-500 border-b border-gray-100">
              {emptyStateMessage}
            </div>
          ) : filtered.length === 0 ? (
            <div className="w-full text-left px-3 py-2 text-sm text-gray-500 border-b border-gray-100">
              No registered cluster matches "{value}"{isConstrained
                ? ' — choose a listed cluster compatible with this pipeline.'
                : ' — you can still submit against this name.'}
            </div>
          ) : (
            filtered.map((c) => (
              <button
                key={c.id}
                type="button"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onChange(c.name)
                  setOpen(false)
                }}
                className="w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 border-b border-gray-50"
              >
                <span className="font-medium text-gray-900">{c.name}</span>
                {(c.provider || c.gpu_type) && (
                  <span className="text-gray-400 ml-1">
                    ({[c.provider, c.gpu_type].filter(Boolean).join(' · ')})
                  </span>
                )}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
