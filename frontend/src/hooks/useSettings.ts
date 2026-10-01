import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { settingsApi } from '../services/api'
import toast from 'react-hot-toast'

export function useManagedUsers() {
  return useQuery({
    queryKey: ['settings', 'users'],
    queryFn: settingsApi.getUsers,
    retry: false,
  })
}

export function useUpdateUserRole() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: 'admin' | 'user' }) =>
      settingsApi.updateUserRole(userId, role),
    onSuccess: (updatedUser) => {
      queryClient.setQueryData(
        ['settings', 'users'],
        (users: import('../services/api').ManagedUser[] | undefined) =>
          users?.map((user) => user.id === updatedUser.id ? updatedUser : user),
      )
      toast.success(`${updatedUser.full_name || updatedUser.email} is now ${updatedUser.role === 'admin' ? 'an administrator' : 'a general user'}`)
    },
    onError: (error: Error) => {
      toast.error(`Failed to update user role: ${error.message}`)
    },
  })
}

export function useSlackSettings() {
  return useQuery({
    queryKey: ['settings', 'slack'],
    queryFn: settingsApi.getSlack,
    retry: false,
  })
}

export function useUpdateSlackSettings() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (webhook_url: string | null) => settingsApi.updateSlack(webhook_url),
    onSuccess: (data) => {
      queryClient.setQueryData(['settings', 'slack'], data)
      toast.success(data.enabled ? 'Slack webhook saved' : 'Slack webhook removed')
    },
    onError: (error: Error) => {
      toast.error(`Failed to update Slack settings: ${error.message}`)
    },
  })
}

export function useTestSlack() {
  return useMutation({
    mutationFn: settingsApi.testSlack,
    onSuccess: () => {
      toast.success('Test message sent to Slack')
    },
    onError: (error: Error) => {
      toast.error(`Slack test failed: ${error.message}`)
    },
  })
}
