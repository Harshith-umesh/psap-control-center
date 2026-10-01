import { useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate, Link } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Clusters from './pages/Clusters'
import ClusterDetail from './pages/ClusterDetail'
import Reservations from './pages/Reservations'
import Calendar from './pages/Calendar'
import Testing from './pages/Testing'
import TestingJobDetail from './pages/TestingJobDetail'
import ScheduleRuns from './pages/ScheduleRuns'
import Results from './pages/Results'
import Settings from './pages/Settings'
import CostExplorer from './pages/CostExplorer'
import { isAdmin, setSession } from './stores/authStore'
import { authApi } from './services/api'
import toast from 'react-hot-toast'

const OAUTH_QUERY_KEYS = [
  'code',
  'state',
  'scope',
  'authuser',
  'hd',
  'prompt',
  'iss',
  'error',
  'error_description',
]

function consumeOAuthCallback() {
  const params = new URLSearchParams(window.location.search)
  const callback = {
    code: params.get('code'),
    state: params.get('state'),
    error: params.get('error'),
  }

  if (callback.code || callback.state || callback.error) {
    for (const key of OAUTH_QUERY_KEYS) params.delete(key)
    const query = params.toString()
    window.history.replaceState(
      {},
      '',
      `${window.location.pathname}${query ? `?${query}` : ''}${window.location.hash}`,
    )
  }

  return callback
}

// Consume OAuth parameters before React renders so child data requests cannot
// copy the authorization code or state into their Referer header.
const oauthCallback = consumeOAuthCallback()

function NotFound() {
  return (
    <div className="text-center py-12">
      <h1 className="text-4xl font-bold text-gray-900">404</h1>
      <p className="mt-2 text-lg text-gray-600">Page not found</p>
      <Link to="/dashboard" className="mt-4 inline-block text-primary-600 hover:text-primary-700">
        Go to Dashboard
      </Link>
    </div>
  )
}

function App() {
  const [, setAuthTick] = useState(0)
  const syncAuth = useCallback(() => setAuthTick(t => t + 1), [])

  useEffect(() => {
    window.addEventListener('auth-change', syncAuth)
    return () => window.removeEventListener('auth-change', syncAuth)
  }, [syncAuth])

  useEffect(() => {
    const bootstrapAuth = async () => {
      const { code, state, error: oauthError } = oauthCallback

      try {
        if (code && state) {
          const session = await authApi.completeGoogleLogin(code, state)
          setSession(session)
          toast.success(`Welcome, ${session.name || session.email || session.username}`)
        } else if (oauthError) {
          toast.error('Google sign-in was cancelled or denied')
        } else {
          const session = await authApi.me()
          setSession(session)
        }
      } catch (error) {
        if (code || oauthError) {
          toast.error(error instanceof Error ? error.message : 'Google sign-in failed')
        }
        // No valid session cookie is expected for anonymous visitors.
      }
    }
    bootstrapAuth()
  }, [])

  return (
    <Routes>
      <Route path="/" element={<Layout />}>
        <Route
          index
          element={
            <Navigate
              to={{
                pathname: '/clusters',
                search: window.location.search,
                hash: window.location.hash,
              }}
              replace
            />
          }
        />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="clusters" element={<Clusters />} />
        <Route path="clusters/:id" element={<ClusterDetail />} />
        <Route path="reservations" element={<Reservations />} />
        <Route path="calendar" element={<Calendar />} />
        <Route path="testing" element={<Testing />} />
        <Route path="testing/jobs/:name" element={<TestingJobDetail />} />
        <Route path="testing/schedules/:name/runs" element={<ScheduleRuns />} />
        <Route path="results" element={<Results />} />
        <Route path="cost-explorer" element={isAdmin() ? <CostExplorer /> : <Navigate to="/dashboard" replace />} />
        <Route path="settings" element={isAdmin() ? <Settings /> : <Navigate to="/dashboard" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}

export default App
