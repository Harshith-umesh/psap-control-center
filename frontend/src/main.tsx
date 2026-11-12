import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './index.css'
import { setLogLevelFromEnv, createLogger } from './utils/logger'
import { authConfigQueryOptions } from './hooks/useAuthConfig'

setLogLevelFromEnv()
const logger = createLogger('Main')
logger.info('PSAP Control Center starting up')

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000,
      refetchOnWindowFocus: false,
    },
  },
})

// Begin public authentication discovery before route components start their
// page-specific requests. The Login modal consumes this same cached query, so
// opening it never starts a second request during this application load.
void queryClient.prefetchQuery(authConfigQueryOptions)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
        <Toaster 
          position="top-right"
          toastOptions={{
            error: {
              duration: 8000,
            },
          }}
        />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
