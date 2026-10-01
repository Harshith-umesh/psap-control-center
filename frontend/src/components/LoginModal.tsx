import { Fragment, useState } from 'react'
import { Dialog, Transition } from '@headlessui/react'
import { ArrowPathIcon, LockClosedIcon, XMarkIcon } from '@heroicons/react/24/outline'
import toast from 'react-hot-toast'
import { authApi } from '../services/api'
import { setSession } from '../stores/authStore'
import type { AuthConfig } from '../types'

interface LoginModalProps {
  open: boolean
  onClose: () => void
  authConfig?: AuthConfig
  configLoading: boolean
  configError: boolean
  onRetryConfig: () => void
}

export default function LoginModal({
  open,
  onClose,
  authConfig,
  configLoading,
  configError,
  onRetryConfig,
}: LoginModalProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username || !password) return

    setLoading(true)
    try {
      const session = await authApi.login(username, password)
      setSession(session)
      toast.success('Logged in successfully')
      setUsername('')
      setPassword('')
      onClose()
    } catch {
      toast.error('Invalid credentials')
    } finally {
      setLoading(false)
    }
  }

  return (
    <Transition.Root show={open} as={Fragment}>
      <Dialog as="div" className="relative z-50" onClose={onClose}>
        <Transition.Child
          as={Fragment}
          enter="ease-out duration-300"
          enterFrom="opacity-0"
          enterTo="opacity-100"
          leave="ease-in duration-200"
          leaveFrom="opacity-100"
          leaveTo="opacity-0"
        >
          <div className="fixed inset-0 bg-gray-500/75 transition-opacity" />
        </Transition.Child>

        <div className="fixed inset-0 z-10 overflow-y-auto">
          <div className="flex min-h-full items-center justify-center p-4">
            <Transition.Child
              as={Fragment}
              enter="ease-out duration-300"
              enterFrom="opacity-0 translate-y-4 sm:scale-95"
              enterTo="opacity-100 translate-y-0 sm:scale-100"
              leave="ease-in duration-200"
              leaveFrom="opacity-100 translate-y-0 sm:scale-100"
              leaveTo="opacity-0 translate-y-4 sm:scale-95"
            >
              <Dialog.Panel className="relative w-full max-w-sm rounded-2xl bg-white p-6 shadow-xl">
                <button
                  type="button"
                  onClick={onClose}
                  aria-label="Close sign-in dialog"
                  className="absolute right-4 top-4 rounded-md p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
                >
                  <XMarkIcon className="h-5 w-5" aria-hidden="true" />
                </button>
                <div className="flex flex-col items-center mb-6">
                  <div className="h-12 w-12 rounded-full bg-primary-100 flex items-center justify-center mb-3">
                    <LockClosedIcon className="h-6 w-6 text-primary-600" />
                  </div>
                  <Dialog.Title className="text-lg font-semibold text-gray-900">
                    Sign In
                  </Dialog.Title>
                  <p className="mt-1 text-sm text-gray-500">
                    Authenticate to make changes
                  </p>
                </div>

                {configLoading && (
                  <div role="status" className="flex items-center justify-center gap-2 rounded-lg bg-gray-50 px-4 py-6 text-sm text-gray-600">
                    <ArrowPathIcon className="h-5 w-5 animate-spin" aria-hidden="true" />
                    Loading sign-in options…
                  </div>
                )}

                {!configLoading && configError && (
                  <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
                    <p className="font-medium">Unable to load sign-in options.</p>
                    <p className="mt-1 text-red-600">Check the connection and try again.</p>
                    <button
                      type="button"
                      onClick={onRetryConfig}
                      className="mt-3 rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-100"
                    >
                      Retry
                    </button>
                  </div>
                )}

                {!configLoading && !configError && authConfig && !authConfig.google_enabled && !authConfig.local_login_enabled && (
                  <div role="alert" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                    No sign-in methods are enabled. Contact an administrator.
                  </div>
                )}

                {!configLoading && !configError && authConfig?.google_enabled && (
                  <>
                    <button
                      type="button"
                      onClick={() => authApi.loginWithGoogle()}
                      className="w-full inline-flex items-center justify-center gap-3 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-semibold text-gray-700 shadow-sm hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2"
                    >
                      <svg className="h-5 w-5" viewBox="0 0 24 24" aria-hidden="true">
                        <path fill="#4285F4" d="M21.6 12.23c0-.71-.06-1.4-.18-2.07H12v3.91h5.38a4.6 4.6 0 0 1-2 3.02v2.54h3.24c1.9-1.75 2.98-4.33 2.98-7.4Z" />
                        <path fill="#34A853" d="M12 22c2.7 0 4.97-.9 6.63-2.42l-3.24-2.54c-.9.6-2.05.96-3.39.96-2.6 0-4.8-1.76-5.59-4.12H3.07v2.62A10 10 0 0 0 12 22Z" />
                        <path fill="#FBBC05" d="M6.41 13.88A6.02 6.02 0 0 1 6.1 12c0-.65.11-1.29.31-1.88V7.5H3.07A10 10 0 0 0 2 12c0 1.61.38 3.14 1.07 4.5l3.34-2.62Z" />
                        <path fill="#EA4335" d="M12 6c1.47 0 2.79.5 3.82 1.5l2.87-2.87A9.62 9.62 0 0 0 12 2a10 10 0 0 0-8.93 5.5l3.34 2.62C7.2 7.76 9.4 6 12 6Z" />
                      </svg>
                      Continue with Google
                    </button>
                    {authConfig.local_login_enabled && (
                      <div className="my-5 flex items-center gap-3">
                        <div className="h-px flex-1 bg-gray-200" />
                        <span className="text-xs uppercase tracking-wide text-gray-400">or use local access</span>
                        <div className="h-px flex-1 bg-gray-200" />
                      </div>
                    )}
                  </>
                )}

                {!configLoading && !configError && authConfig?.local_login_enabled && <form onSubmit={handleSubmit} className="space-y-4">
                  <div>
                    <label htmlFor="username" className="block text-sm font-medium text-gray-700">
                      Username
                    </label>
                    <input
                      id="username"
                      type="text"
                      autoComplete="username"
                      required
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                    />
                  </div>
                  <div>
                    <label htmlFor="password" className="block text-sm font-medium text-gray-700">
                      Password
                    </label>
                    <input
                      id="password"
                      type="password"
                      autoComplete="current-password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="mt-1 block w-full rounded-lg border border-gray-300 px-3 py-2 text-sm shadow-sm focus:border-primary-500 focus:outline-none focus:ring-1 focus:ring-primary-500"
                    />
                  </div>
                  <button
                    type="submit"
                    disabled={loading}
                    className="w-full rounded-lg bg-primary-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-primary-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:ring-offset-2 disabled:opacity-50 transition-colors"
                  >
                    {loading ? 'Signing in...' : 'Sign In'}
                  </button>
                </form>}
              </Dialog.Panel>
            </Transition.Child>
          </div>
        </div>
      </Dialog>
    </Transition.Root>
  )
}
