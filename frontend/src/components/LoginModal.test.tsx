import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import LoginModal from './LoginModal'
import type { AuthConfig } from '../types'

vi.mock('../services/api', () => ({
  authApi: {
    login: vi.fn(),
    loginWithGoogle: vi.fn(),
  },
}))

const enabled: AuthConfig = {
  google_enabled: true,
  local_login_enabled: true,
}

function renderModal(overrides: Partial<React.ComponentProps<typeof LoginModal>> = {}) {
  return render(
    <LoginModal
      open
      onClose={vi.fn()}
      authConfig={enabled}
      configLoading={false}
      configError={false}
      onRetryConfig={vi.fn()}
      {...overrides}
    />,
  )
}

describe('LoginModal authentication discovery', () => {
  it('shows only a loading state while methods are being discovered', () => {
    const close = vi.fn()
    renderModal({ authConfig: undefined, configLoading: true, onClose: close })

    expect(screen.getByRole('status')).toHaveTextContent('Loading sign-in options')
    expect(screen.queryByRole('button', { name: 'Continue with Google' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Close sign-in dialog' }))
    expect(close).toHaveBeenCalledOnce()
  })

  it('shows every enabled sign-in method', () => {
    renderModal()

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
  })

  it('supports Google-only configuration', () => {
    renderModal({
      authConfig: { google_enabled: true, local_login_enabled: false },
    })

    expect(screen.getByRole('button', { name: 'Continue with Google' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument()
  })

  it('supports local-only configuration without implying Google is loading', () => {
    renderModal({
      authConfig: { google_enabled: false, local_login_enabled: true },
    })

    expect(screen.queryByRole('button', { name: 'Continue with Google' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Username')).toBeInTheDocument()
  })

  it('reports when no sign-in method is enabled', () => {
    renderModal({
      authConfig: { google_enabled: false, local_login_enabled: false },
    })

    expect(screen.getByRole('alert')).toHaveTextContent('No sign-in methods are enabled')
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument()
  })

  it('shows a retryable error without falling back to local login', () => {
    const retry = vi.fn()
    renderModal({
      authConfig: undefined,
      configError: true,
      onRetryConfig: retry,
    })

    expect(screen.getByRole('alert')).toHaveTextContent('Unable to load sign-in options')
    expect(screen.queryByLabelText('Username')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledOnce()
  })
})
