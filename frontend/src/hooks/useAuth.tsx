import { useQuery, useQueryClient } from '@tanstack/react-query'
import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Loading } from '../components/ui'
import { api, type SessionUser } from '../services/api'

/**
 * The single source of auth truth for the UI. The session itself is an HttpOnly cookie set by the
 * backend — the browser never sees a token or the password; this only mirrors GET /api/auth/me.
 */
interface Auth {
  currentUser: SessionUser | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (loginId: string, password: string) => Promise<SessionUser>
  logout: () => Promise<void>
}

const AuthContext = createContext<Auth | null>(null)
export const ME_KEY = ['auth', 'me'] as const

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient()
  const me = useQuery({ queryKey: ME_KEY, queryFn: api.me, staleTime: Infinity, retry: false })

  const login = useCallback(async (loginId: string, password: string) => {
    const user = await api.login(loginId, password)
    qc.setQueryData(ME_KEY, user)
    return user
  }, [qc])

  const logout = useCallback(async () => {
    try { await api.logout() } finally {
      // drop every cached traffic/model response with the session. Not qc.clear(): that would also
      // detach the live session query, leaving this provider showing the old user.
      qc.removeQueries({ predicate: q => q.queryKey[0] !== ME_KEY[0] })
      qc.setQueryData(ME_KEY, null)
    }
  }, [qc])

  const value = useMemo<Auth>(() => ({
    currentUser: me.data ?? null, isAuthenticated: Boolean(me.data), isLoading: me.isLoading, login, logout,
  }), [me.data, me.isLoading, login, logout])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): Auth {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}

/** Route guard for every application page: unauthenticated visitors go to /login (and come back after). */
export function RequireAuth() {
  const { isAuthenticated, isLoading } = useAuth()
  const location = useLocation()
  if (isLoading) return <div className="h-full grid place-items-center"><Loading label="Checking session" /></div>
  if (!isAuthenticated) return <Navigate to="/login" replace state={{ from: location }} />
  return <Outlet />
}
