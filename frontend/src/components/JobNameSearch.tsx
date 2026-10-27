import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { MagnifyingGlassIcon } from '@heroicons/react/24/outline'
import { fournosApi } from '../services/api'

export default function JobNameSearch() {
  const navigate = useNavigate()
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [open, setOpen] = useState(false)

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 250)
    return () => window.clearTimeout(timer)
  }, [query])

  const { data, isFetching, error } = useQuery({
    queryKey: ['fournos-job-name-search', debounced],
    queryFn: () => fournosApi.searchJobNames(debounced),
    enabled: open && debounced.length > 0,
    staleTime: 5_000,
    retry: false,
  })
  const items = data?.items || []

  const openJob = (name: string) => {
    setOpen(false)
    setQuery(name)
    navigate(`/testing/jobs/${encodeURIComponent(name)}`)
  }

  return (
    <div className="relative w-full max-w-sm" onBlur={(event) => {
      if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
        window.setTimeout(() => setOpen(false), 100)
      }
    }}>
      <MagnifyingGlassIcon className="pointer-events-none absolute left-2.5 top-2 h-4 w-4 text-gray-400" />
      <input
        type="search"
        role="combobox"
        aria-label="Search job names using a regular expression"
        aria-autocomplete="list"
        aria-expanded={open && debounced.length > 0}
        aria-controls="job-name-search-options"
        value={query}
        maxLength={128}
        autoComplete="off"
        spellCheck={false}
        onFocus={() => setOpen(true)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true) }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && items[0]) {
            event.preventDefault()
            openJob(items[0].name)
          }
          if (event.key === 'Escape') setOpen(false)
        }}
        placeholder="Search job names (regex, e.g. jql)"
        className="w-full rounded-md border-gray-300 py-1.5 pl-8 pr-3 text-sm shadow-sm focus:border-indigo-500 focus:ring-indigo-500"
      />
      {open && debounced && (
        <div id="job-name-search-options" role="listbox" className="absolute z-30 mt-1 max-h-72 w-full overflow-y-auto rounded-md border border-gray-200 bg-white py-1 shadow-lg">
          {isFetching ? (
            <p className="px-3 py-2 text-xs text-gray-400">Searching job names…</p>
          ) : error ? (
            <p className="px-3 py-2 text-xs text-red-600">{(error as Error).message || 'Invalid regular expression'}</p>
          ) : items.length ? items.map((item) => (
            <button
              key={`${item.source}:${item.name}`}
              type="button"
              role="option"
              onClick={() => openJob(item.name)}
              className="flex w-full items-center justify-between gap-3 px-3 py-2 text-left hover:bg-indigo-50"
            >
              <span className="min-w-0 truncate font-mono text-xs text-gray-800">{item.name}</span>
              <span className="shrink-0 text-[10px] text-gray-400">{item.status} · {item.source}</span>
            </button>
          )) : (
            <p className="px-3 py-2 text-xs text-gray-400">No matching jobs.</p>
          )}
        </div>
      )}
    </div>
  )
}
