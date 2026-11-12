import type { WorkItemReference } from '../types'

export function workItemFromInput(raw: string): WorkItemReference | null {
  const value = raw.trim()
  if (!value) return null
  const isUrl = /^https?:\/\//i.test(value)
  return {
    provider: 'jira',
    key: isUrl ? '' : value.toUpperCase(),
    url: isUrl ? value : '',
  }
}
