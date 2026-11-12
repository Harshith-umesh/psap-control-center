import { useMemo, useState } from 'react'
import { PlusIcon, XMarkIcon } from '@heroicons/react/24/outline'
import { useCreateRunGroup, useRunGroups } from '../hooks/useFournos'
import type { RunGroupReference, RunGroupType } from '../types'

const GROUP_TYPES: Array<{ value: RunGroupType; label: string }> = [
  { value: 'experiment', label: 'Experiment' },
  { value: 'workload', label: 'Workload family' },
  { value: 'campaign', label: 'Campaign / cycle' },
  { value: 'cohort', label: 'Cohort' },
]

export default function RunGroupSelector({
  selectedIds,
  onChange,
  selectedGroups = [],
  allowCreate = true,
}: {
  selectedIds: string[]
  onChange: (ids: string[]) => void
  selectedGroups?: RunGroupReference[]
  allowCreate?: boolean
}) {
  const groupsQuery = useRunGroups()
  const createGroup = useCreateRunGroup()
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [groupType, setGroupType] = useState<RunGroupType>('experiment')
  const [key, setKey] = useState('')
  const [displayName, setDisplayName] = useState('')

  const allGroups = useMemo(() => {
    const byId = new Map<string, RunGroupReference>()
    for (const group of [...selectedGroups, ...(groupsQuery.data ?? [])]) {
      byId.set(group.id, group)
    }
    return Array.from(byId.values())
  }, [groupsQuery.data, selectedGroups])

  const selected = selectedIds
    .map((id) => allGroups.find((group) => group.id === id))
    .filter((group): group is RunGroupReference => Boolean(group))
  const available = (groupsQuery.data ?? []).filter((group) => {
    if (selectedIds.includes(group.id)) return false
    const needle = search.trim().toLowerCase()
    return !needle || `${group.group_type} ${group.key} ${group.display_name}`.toLowerCase().includes(needle)
  })

  const create = async () => {
    try {
      const created = await createGroup.mutateAsync({
        group_type: groupType,
        key,
        display_name: displayName,
      })
      onChange([...selectedIds, created.id])
      setKey('')
      setDisplayName('')
      setShowCreate(false)
    } catch {
      // Mutation-level toast supplies the actionable server validation error.
    }
  }

  return (
    <div className="space-y-2">
      {selected.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selected.map((group) => (
            <span key={group.id} className="inline-flex items-center gap-1 rounded-full bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700">
              <span className="text-indigo-400">{group.group_type}</span>
              {group.display_name}
              {group.archived && <span className="text-gray-400">(archived)</span>}
              <button
                type="button"
                aria-label={`Remove ${group.display_name}`}
                onClick={() => onChange(selectedIds.filter((id) => id !== group.id))}
                className="text-indigo-400 hover:text-red-600"
              >
                <XMarkIcon className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search reusable run groups"
          className="min-w-0 flex-1 rounded-md border-gray-300 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
        />
        {allowCreate && (
          <button
            type="button"
            onClick={() => setShowCreate((value) => !value)}
            className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50"
          >
            <PlusIcon className="h-4 w-4" /> New
          </button>
        )}
      </div>

      {search.trim() && available.length > 0 && (
        <div className="max-h-40 overflow-auto rounded-md border border-gray-200 bg-white shadow-sm">
          {available.map((group) => (
            <button
              key={group.id}
              type="button"
              onClick={() => { onChange([...selectedIds, group.id]); setSearch('') }}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-gray-50"
            >
              <span>{group.display_name}</span>
              <span className="text-xs text-gray-400">{group.group_type} · {group.key}</span>
            </button>
          ))}
        </div>
      )}
      {search.trim() && !groupsQuery.isLoading && available.length === 0 && (
        <p className="text-xs text-gray-400">No selectable groups match.</p>
      )}

      {showCreate && (
        <div className="grid gap-2 rounded-md border border-gray-200 bg-gray-50 p-3 sm:grid-cols-3">
          <select
            value={groupType}
            onChange={(event) => setGroupType(event.target.value as RunGroupType)}
            aria-label="Run group type"
            className="rounded-md border-gray-300 text-sm"
          >
            {GROUP_TYPES.map((type) => <option key={type.value} value={type.value}>{type.label}</option>)}
          </select>
          <input
            value={key}
            onChange={(event) => setKey(event.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, ''))}
            placeholder="stable-key"
            aria-label="Run group key"
            className="rounded-md border-gray-300 text-sm"
          />
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="Display name"
            aria-label="Run group display name"
            className="rounded-md border-gray-300 text-sm"
          />
          <div className="flex justify-end gap-2 sm:col-span-3">
            <button type="button" onClick={() => setShowCreate(false)} className="px-3 py-1.5 text-xs text-gray-600">Cancel</button>
            <button
              type="button"
              disabled={!key || !displayName.trim() || createGroup.isPending}
              onClick={() => void create()}
              className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            >
              Create and select
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
