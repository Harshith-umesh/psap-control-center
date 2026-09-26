import { queryOptions, useQuery } from '@tanstack/react-query'
import { authApi } from '../services/api'
import type { AuthConfig } from '../types'

export const authConfigQueryOptions = queryOptions<AuthConfig>({
  queryKey: ['auth-config'],
  queryFn: () => authApi.config(),
  staleTime: Infinity,
  gcTime: Infinity,
  retry: false,
  refetchOnWindowFocus: false,
})

export function useAuthConfig() {
  return useQuery(authConfigQueryOptions)
}
