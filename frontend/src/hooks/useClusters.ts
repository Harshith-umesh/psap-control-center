import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { clusterApi } from '../services/api'
import type { Cluster, RefreshDisplayMode, RefreshDisplayPreference } from '../types'
import toast from 'react-hot-toast'
import { createLogger } from '../utils/logger'

const logger = createLogger('Clusters')

export function useClusters(activeOnly = false, enabled = true) {
  return useQuery({
    queryKey: ['clusters', { activeOnly }],
    queryFn: () => clusterApi.list(activeOnly),
    enabled,
  })
}

export function useClusterRefreshDisplayPreference(enabled = true) {
  return useQuery<RefreshDisplayPreference>({
    queryKey: ['cluster-refresh-display-preference'],
    queryFn: clusterApi.getRefreshDisplayPreference,
    enabled,
    staleTime: Infinity,
    retry: 1,
  })
}

export function useSaveClusterRefreshDisplayPreference() {
  const queryClient = useQueryClient()

  return useMutation<
    RefreshDisplayPreference,
    Error,
    RefreshDisplayMode,
    { previous?: RefreshDisplayPreference }
  >({
    mutationFn: clusterApi.saveRefreshDisplayPreference,
    onMutate: async (mode) => {
      await queryClient.cancelQueries({
        queryKey: ['cluster-refresh-display-preference'],
      })
      const previous = queryClient.getQueryData<RefreshDisplayPreference>(
        ['cluster-refresh-display-preference'],
      )
      queryClient.setQueryData<RefreshDisplayPreference>(
        ['cluster-refresh-display-preference'],
        { ...previous, mode },
      )
      return { previous }
    },
    onError: (error, _mode, context) => {
      queryClient.setQueryData<RefreshDisplayPreference>(
        ['cluster-refresh-display-preference'],
        context?.previous ?? { mode: 'countdown' },
      )
      toast.error(`Failed to save refresh display preference: ${error.message}`)
    },
    onSuccess: (data) => {
      queryClient.setQueryData(['cluster-refresh-display-preference'], data)
    },
  })
}

export function useCluster(id: string) {
  return useQuery({
    queryKey: ['cluster', id],
    queryFn: () => clusterApi.get(id),
    enabled: !!id,
  })
}

export function useClusterStatus(id: string) {
  return useQuery({
    queryKey: ['clusterStatus', id],
    queryFn: () => clusterApi.getStatus(id),
    enabled: !!id,
    refetchInterval: 60000,
  })
}

export function useCreateCluster() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: clusterApi.create,
    onSuccess: (data) => {
      logger.info('Cluster created:', data.name)
      queryClient.invalidateQueries({ queryKey: ['clusters'] })
      toast.success('Cluster created successfully')
    },
    onError: (error: Error) => {
      logger.error('Failed to create cluster:', error)
      toast.error(`Failed to create cluster: ${error.message}`)
    },
  })
}

export function useUpdateCluster() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: Partial<Cluster> }) =>
      clusterApi.update(id, data),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['clusters'] })
      queryClient.invalidateQueries({ queryKey: ['cluster', data.id] })
      toast.success('Cluster updated successfully')
    },
    onError: (error: Error) => {
      toast.error(`Failed to update cluster: ${error.message}`)
    },
  })
}

export function useDeleteCluster() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: clusterApi.delete,
    onSuccess: (_data, clusterId) => {
      logger.info('Cluster deleted:', clusterId)
      queryClient.invalidateQueries({ queryKey: ['clusters'] })
      toast.success('Cluster removed from Control Center')
    },
    onError: (error: Error) => {
      logger.error('Failed to delete cluster:', error)
      toast.error(`Failed to remove cluster: ${error.message}`)
    },
  })
}

export function useRefreshClusterStatus() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: clusterApi.refreshStatus,
    onSuccess: (_data, id) => {
      queryClient.invalidateQueries({ queryKey: ['clusterStatus', id] })
      queryClient.invalidateQueries({ queryKey: ['clusters'] })
      toast.success('Cluster status refreshed')
    },
    onError: (error: Error) => {
      toast.error(`Failed to refresh status: ${error.message}`)
    },
  })
}

export function useUploadKubeconfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      clusterApi.uploadKubeconfig(id, file),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['clusters'] })
      queryClient.invalidateQueries({ queryKey: ['cluster', data.id] })
      toast.success('Kubeconfig uploaded successfully')
    },
    onError: (error: Error) => {
      toast.error(`Failed to upload kubeconfig: ${error.message}`)
    },
  })
}

export function useClusterTopology(id: string) {
  return useQuery({
    queryKey: ['clusterTopology', id],
    queryFn: () => clusterApi.getTopology(id),
    enabled: !!id,
    staleTime: 60000,
  })
}

export function useOcpDetails(id: string) {
  return useQuery({
    queryKey: ['ocpDetails', id],
    queryFn: () => clusterApi.getOcpDetails(id),
    enabled: !!id,
    staleTime: 60000,
  })
}

export function useClusterOperators(id: string) {
  return useQuery({
    queryKey: ['clusterOperators', id],
    queryFn: () => clusterApi.getOperators(id),
    enabled: !!id,
    staleTime: 60000,
  })
}

export function useClusterWorkloads(id: string, namespace?: string) {
  return useQuery({
    queryKey: ['clusterWorkloads', id, namespace],
    queryFn: () => clusterApi.getWorkloads(id, namespace),
    enabled: !!id,
    staleTime: 30000,
  })
}
