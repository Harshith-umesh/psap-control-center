import { describe, expect, it } from 'vitest'
import { workItemFromInput } from './workItems'

describe('workItemFromInput', () => {
  it('normalizes a Jira key', () => {
    expect(workItemFromInput(' psap-58 ')).toEqual({
      provider: 'jira',
      key: 'PSAP-58',
      url: '',
    })
  })

  it('preserves a URL for server-side validation', () => {
    expect(workItemFromInput('https://jira.example.com/browse/PSAP-58')).toEqual({
      provider: 'jira',
      key: '',
      url: 'https://jira.example.com/browse/PSAP-58',
    })
  })

  it('omits blank input', () => {
    expect(workItemFromInput('  ')).toBeNull()
  })
})
