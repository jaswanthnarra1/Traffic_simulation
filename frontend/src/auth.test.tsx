import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthProvider, RequireAuth, useAuth } from './hooks/useAuth'
import Login from './pages/Login'

const USER = { login_id: 'tgpolice', display_name: 'TG Police', expires_at: 9999999999 }

/** Minimal fake backend: `session` flips on a correct login and off on logout. */
function mockBackend({ signedIn = false, down = false } = {}) {
  let session = signedIn
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (down) throw new TypeError('network')
    const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
    if (url.endsWith('/api/auth/me')) return session ? json(200, USER) : json(401, { detail: 'Not authenticated' })
    if (url.endsWith('/api/auth/logout')) { session = false; return json(200, { authenticated: false }) }
    if (url.endsWith('/api/auth/login')) {
      const b = JSON.parse(String(init?.body))
      if (b.login_id === 'tgpolice' && b.password === 'tgpolice') { session = true; return json(200, USER) }
      return json(401, { detail: 'Invalid login ID or password.' })
    }
    return json(404, {})
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function LogoutButton() {
  const { logout, currentUser } = useAuth()
  return <div><span>Protected page for {currentUser?.display_name}</span><button onClick={() => logout()}>Logout</button></div>
}

function renderApp(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<RequireAuth />}>
              <Route path="/dashboard" element={<LogoutButton />} />
              <Route path="/forecast" element={<div>Forecast page</div>} />
            </Route>
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const fill = (id: string, pw: string) => {
  fireEvent.change(screen.getByLabelText('Login ID'), { target: { value: id } })
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: pw } })
  fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
}

afterEach(() => vi.unstubAllGlobals())

describe('login page', () => {
  it('renders only Login ID + password — no sign-up, recovery or social login', async () => {
    mockBackend()
    renderApp('/login')
    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toBeInTheDocument()
    expect(screen.getByAltText('FlowSense AI')).toBeInTheDocument()
    expect(screen.getByLabelText('Login ID')).toHaveAttribute('autocomplete', 'username')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
    expect(document.body.textContent).not.toMatch(/sign up|register|forgot|continue with|google/i)
  })

  it('password visibility toggle is accessible', async () => {
    mockBackend()
    renderApp('/login')
    const pw = await screen.findByLabelText('Password')
    expect(pw).toHaveAttribute('type', 'password')
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }))
    expect(pw).toHaveAttribute('type', 'text')
    expect(screen.getByRole('button', { name: 'Hide password' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('rejects empty fields without calling the backend', async () => {
    const f = mockBackend()
    renderApp('/login')
    await screen.findByLabelText('Login ID')
    fireEvent.click(screen.getByRole('button', { name: 'Sign In' }))
    expect(screen.getByText('Enter your login ID.')).toBeInTheDocument()
    expect(screen.getByText('Enter your password.')).toBeInTheDocument()
    expect(f.mock.calls.some(([u]) => String(u).endsWith('/api/auth/login'))).toBe(false)
  })

  it('shows one generic error for wrong credentials and stays on /login', async () => {
    mockBackend()
    renderApp('/login')
    await screen.findByLabelText('Login ID')
    fill('tgpolice', 'wrong')
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid login ID or password.')
    expect(screen.getByLabelText('Password')).toHaveValue('')
  })

  it('shows a retry message when the backend is unreachable', async () => {
    mockBackend()
    renderApp('/login')
    await screen.findByLabelText('Login ID')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('network') }))
    fill('tgpolice', 'tgpolice')
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to sign in right now. Please try again.')
  })

  it('correct credentials open the dashboard', async () => {
    mockBackend()
    renderApp('/login')
    await screen.findByLabelText('Login ID')
    fill('tgpolice', 'tgpolice')
    expect(await screen.findByText('Protected page for TG Police')).toBeInTheDocument()
  })
})

describe('route guard and session', () => {
  it('redirects unauthenticated users to /login, then back to the requested page', async () => {
    mockBackend()
    renderApp('/forecast')
    expect(await screen.findByRole('heading', { name: 'Sign in to continue' })).toBeInTheDocument()
    fill('tgpolice', 'tgpolice')
    expect(await screen.findByText('Forecast page')).toBeInTheDocument()
  })

  it('an existing session survives a reload (restored from /api/auth/me)', async () => {
    mockBackend({ signedIn: true })
    renderApp('/dashboard')
    expect(await screen.findByText('Protected page for TG Police')).toBeInTheDocument()
  })

  it('logout ends the session and returns to /login', async () => {
    mockBackend({ signedIn: true })
    renderApp('/dashboard')
    fireEvent.click(await screen.findByRole('button', { name: 'Logout' }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Sign in to continue' })).toBeInTheDocument())
  })
})
