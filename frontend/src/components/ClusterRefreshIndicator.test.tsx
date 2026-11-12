import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ClusterRefreshIndicator from './ClusterRefreshIndicator'

const schedule = {
  last_refresh: '2026-10-02T14:15:16Z',
  next_refresh: '2026-10-02T14:20:00Z',
  in_progress: false,
}

describe('ClusterRefreshIndicator', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-02T14:16:30Z'))
  })

  afterEach(() => vi.useRealTimers())

  it('shows and updates the live countdown in countdown mode', () => {
    render(
      <ClusterRefreshIndicator
        schedule={schedule}
        mode="countdown"
        onModeChange={vi.fn()}
        canSavePreference
      />,
    )

    expect(screen.getByText('3:30')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1000))
    expect(screen.getByText('3:29')).toBeInTheDocument()
  })

  it('keeps last-update mode static without starting a timer', () => {
    const intervalSpy = vi.spyOn(window, 'setInterval')
    render(
      <ClusterRefreshIndicator
        schedule={schedule}
        mode="last_update"
        onModeChange={vi.fn()}
        canSavePreference
      />,
    )

    expect(screen.getByText(/Last update:/)).toBeInTheDocument()
    expect(screen.queryByText(/Refresh in/)).not.toBeInTheDocument()
    expect(intervalSpy).not.toHaveBeenCalled()
    intervalSpy.mockRestore()
  })

  it('offers an accessible preference toggle', () => {
    const onModeChange = vi.fn()
    render(
      <ClusterRefreshIndicator
        schedule={schedule}
        mode="countdown"
        onModeChange={onModeChange}
        canSavePreference
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Show last update time' }))
    expect(onModeChange).toHaveBeenCalledWith('last_update')
  })
})
