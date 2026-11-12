import { useEffect, useState } from 'react'
import { format } from 'date-fns'
import { ArrowPathIcon, ClockIcon } from '@heroicons/react/24/outline'
import clsx from 'clsx'
import type { RefreshDisplayMode } from '../types'

export interface ClusterRefreshSchedule {
  last_refresh: string | null
  next_refresh: string | null
  in_progress: boolean
}

interface ClusterRefreshIndicatorProps {
  schedule: ClusterRefreshSchedule
  mode: RefreshDisplayMode
  onModeChange: (mode: RefreshDisplayMode) => void
  canSavePreference: boolean
  saving?: boolean
}

function remainingTime(nextRefresh: string): string {
  const seconds = Math.max(
    0,
    Math.floor((new Date(nextRefresh).getTime() - Date.now()) / 1000),
  )
  const minutes = Math.floor(seconds / 60)
  return `${minutes}:${(seconds % 60).toString().padStart(2, '0')}`
}

export default function ClusterRefreshIndicator({
  schedule,
  mode,
  onModeChange,
  canSavePreference,
  saving = false,
}: ClusterRefreshIndicatorProps) {
  const [countdown, setCountdown] = useState('')

  useEffect(() => {
    if (mode !== 'countdown' || !schedule.next_refresh) {
      setCountdown('')
      return
    }

    const tick = () => setCountdown(remainingTime(schedule.next_refresh!))
    tick()
    const interval = window.setInterval(tick, 1000)
    return () => window.clearInterval(interval)
  }, [mode, schedule.next_refresh])

  return (
    <span className="flex items-center gap-2 text-xs text-gray-400 border-l border-gray-200 pl-4">
      <ClockIcon className="h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />

      {mode === 'last_update' ? (
        <span aria-live="polite">
          Last update:{' '}
          <span className="font-medium text-gray-600">
            {schedule.last_refresh
              ? format(new Date(schedule.last_refresh), 'MMM d, HH:mm:ss')
              : 'not available'}
          </span>
        </span>
      ) : schedule.in_progress ? (
        <span className="flex items-center gap-1 text-primary-600 font-medium">
          <ArrowPathIcon className="h-3 w-3 animate-spin" aria-hidden="true" />
          Refreshing...
        </span>
      ) : (
        <span aria-live="off">
          Refresh in{' '}
          <span className="font-mono font-medium text-gray-600">
            {countdown || '—'}
          </span>
        </span>
      )}

      {canSavePreference && (
        <span
          role="group"
          aria-label="Refresh time display"
          className="inline-flex rounded-md border border-gray-200 bg-white p-0.5"
        >
          {(['countdown', 'last_update'] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={mode === option}
              aria-label={
                option === 'countdown'
                  ? 'Show refresh countdown'
                  : 'Show last update time'
              }
              disabled={saving}
              onClick={() => onModeChange(option)}
              className={clsx(
                'rounded px-2 py-0.5 text-[11px] font-medium transition-colors',
                mode === option
                  ? 'bg-gray-100 text-gray-700'
                  : 'text-gray-400 hover:text-gray-600',
                saving && 'cursor-wait opacity-60',
              )}
            >
              {option === 'countdown' ? 'Countdown' : 'Last update'}
            </button>
          ))}
        </span>
      )}
    </span>
  )
}
