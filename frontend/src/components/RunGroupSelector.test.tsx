import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import RunGroupSelector from './RunGroupSelector'
import type { RunGroupReference } from '../types'

const mocks = vi.hoisted(() => ({ create: vi.fn() }))

const experiment: RunGroupReference = {
  id: '11111111-1111-4111-8111-111111111111',
  group_type: 'experiment',
  key: 'latency-study',
  display_name: 'Latency study',
  description: '',
  archived: false,
}

vi.mock('../hooks/useFournos', () => ({
  useRunGroups: () => ({ data: [experiment], isLoading: false }),
  useCreateRunGroup: () => ({
    mutateAsync: mocks.create,
    isPending: false,
  }),
}))

describe('RunGroupSelector', () => {
  it('searches and selects a reusable generic group', () => {
    const onChange = vi.fn()
    render(<RunGroupSelector selectedIds={[]} onChange={onChange} />)

    fireEvent.change(screen.getByPlaceholderText('Search reusable run groups'), {
      target: { value: 'latency' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Latency study/ }))

    expect(onChange).toHaveBeenCalledWith([experiment.id])
  })

  it('creates and selects a product-neutral group', async () => {
    const onChange = vi.fn()
    const created = {
      ...experiment,
      id: '22222222-2222-4222-8222-222222222222',
      group_type: 'campaign' as const,
      key: 'september-cpt',
      display_name: 'September CPT',
    }
    mocks.create.mockResolvedValueOnce(created)
    render(<RunGroupSelector selectedIds={[]} onChange={onChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'New' }))
    fireEvent.change(screen.getByLabelText('Run group type'), {
      target: { value: 'campaign' },
    })
    fireEvent.change(screen.getByLabelText('Run group key'), {
      target: { value: 'september-cpt' },
    })
    fireEvent.change(screen.getByLabelText('Run group display name'), {
      target: { value: 'September CPT' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create and select' }))

    await waitFor(() => expect(mocks.create).toHaveBeenCalledWith({
      group_type: 'campaign',
      key: 'september-cpt',
      display_name: 'September CPT',
    }))
    expect(onChange).toHaveBeenCalledWith([created.id])
  })
})
