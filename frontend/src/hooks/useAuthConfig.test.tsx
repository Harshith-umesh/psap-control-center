import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { authApi } from '../services/api'
import { authConfigQueryOptions, useAuthConfig } from './useAuthConfig'

vi.mock('../services/api', () => ({
  authApi: {
    config: vi.fn(),
  },
}))

describe('useAuthConfig', () => {
  beforeEach(() => vi.clearAllMocks())

  it('shares the startup request and cached result across consumers', async () => {
    vi.mocked(authApi.config).mockResolvedValue({
      google_enabled: true,
      local_login_enabled: false,
    })
    const client = new QueryClient()
    await client.prefetchQuery(authConfigQueryOptions)
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )

    const first = renderHook(() => useAuthConfig(), { wrapper })
    const second = renderHook(() => useAuthConfig(), { wrapper })

    await waitFor(() => expect(first.result.current.isSuccess).toBe(true))
    expect(second.result.current.data?.google_enabled).toBe(true)
    expect(authApi.config).toHaveBeenCalledOnce()
  })

  it('does not silently retry a failure and supports an explicit retry', async () => {
    vi.mocked(authApi.config)
      .mockRejectedValueOnce(new Error('unavailable'))
      .mockResolvedValueOnce({
        google_enabled: true,
        local_login_enabled: true,
      })
    const client = new QueryClient()
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const hook = renderHook(() => useAuthConfig(), { wrapper })

    await waitFor(() => expect(hook.result.current.isError).toBe(true))
    expect(authApi.config).toHaveBeenCalledOnce()

    await act(async () => { await hook.result.current.refetch() })
    await waitFor(() => expect(hook.result.current.isSuccess).toBe(true))
    expect(hook.result.current.data?.google_enabled).toBe(true)
    expect(authApi.config).toHaveBeenCalledTimes(2)
  })
})
