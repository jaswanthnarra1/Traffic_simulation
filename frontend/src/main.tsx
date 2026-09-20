import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Layout } from './components/Layout'
import { AuthProvider, ME_KEY, RequireAuth } from './hooks/useAuth'
import Dashboard from './pages/Dashboard'
import Evaluation from './pages/Evaluation'
import Forecast from './pages/Forecast'
import Incidents from './pages/Incidents'
import Interventions from './pages/Interventions'
import Login from './pages/Login'
import UserPortal from './pages/UserPortal'
import Propagation from './pages/Propagation'
import Flyover from './pages/Flyover'
import Simulation from './pages/Simulation'
import { ApiError } from './services/api'
import './styles/index.css'

// A 401 from any data call means the session expired: mark signed-out once, centrally; RequireAuth redirects.
const onError = (e: unknown) => { if (e instanceof ApiError && e.status === 401) qc.setQueryData(ME_KEY, null) }
const qc = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: { queries: { retry: (n, e) => !(e instanceof ApiError && e.status === 401) && n < 1, refetchOnWindowFocus: false, staleTime: 60_000 } },
})

/** "/" -> "/dashboard", keeping ?t=&seg= */
function ToDashboard() {
  return <Navigate to={{ pathname: '/dashboard', search: useLocation().search }} replace />
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="login" element={<Login />} />
            {/* public rider portal: outside RequireAuth on purpose; it uses only /api/user/* (no operator data) */}
            <Route path="user/*" element={<UserPortal />} />
            <Route element={<RequireAuth />}>
              <Route element={<Layout />}>
                <Route index element={<ToDashboard />} />
                <Route path="dashboard" element={<Dashboard />} />
                <Route path="incidents" element={<Incidents />} />
                <Route path="forecast" element={<Forecast />} />
                <Route path="propagation" element={<Propagation />} />
                <Route path="interventions" element={<Interventions />} />
                <Route path="simulation" element={<Simulation />} />
                <Route path="flyover" element={<Flyover />} />
                <Route path="evaluation" element={<Evaluation />} />
                <Route path="*" element={<ToDashboard />} />
              </Route>
            </Route>
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
