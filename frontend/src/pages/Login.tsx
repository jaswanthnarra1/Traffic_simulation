import { AlertCircle, Eye, EyeOff, Loader2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, type Location } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { ApiError } from '../services/api'

const INPUT = 'w-full h-10 px-3 rounded-md border bg-white text-[14px] text-ink placeholder:text-ink-3 outline-none transition-colors ' +
  'focus:border-ink focus:ring-2 focus:ring-ink/10'

export default function Login() {
  const { login, isAuthenticated, isLoading } = useAuth()
  const from = (useLocation().state as { from?: Location } | null)?.from
  const target = from && from.pathname !== '/login' ? `${from.pathname}${from.search}` : '/dashboard'

  const [loginId, setLoginId] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  if (!isLoading && isAuthenticated) return <Navigate to={target} replace />

  const idMissing = touched && !loginId.trim()
  const pwMissing = touched && !password

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (pending) return                         // one submission at a time
    setTouched(true)
    setError(null)
    if (!loginId.trim() || !password) return
    setPending(true)
    try {
      await login(loginId.trim(), password)  // session set -> the <Navigate> above redirects (single navigation)
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0
      setError(status === 401 ? 'Invalid login ID or password.' : 'Unable to sign in right now. Please try again.')
      setPassword('')
      setTouched(false)                       // the cleared password is not a validation error
      setPending(false)
    }
  }

  return (
    <main className="min-h-full flex items-center justify-center px-5 py-10 bg-paper">
      <div className="w-full max-w-[420px]">
        <div className="bg-panel border border-line rounded-xl px-6 py-8 sm:px-9 sm:py-10 shadow-[0_1px_2px_rgba(22,24,29,0.04),0_12px_32px_-12px_rgba(22,24,29,0.10)]">
          <div className="flex flex-col items-center text-center">
            <img src="/flowsense-logo.png" alt="FlowSense AI" width={548} height={120} className="h-9 w-auto" />
            <p className="mt-2 text-[12px] text-ink-3">AI Traffic Intelligence &amp; Intervention Simulator</p>
            <h1 className="mt-7 text-[20px] font-semibold tracking-tight text-ink">Sign in to continue</h1>
            <p className="mt-1.5 text-[13.5px] text-ink-2">Please sign in to start your session.</p>
          </div>

          <form className="mt-7 space-y-4" onSubmit={submit} noValidate aria-describedby={error ? 'login-error' : undefined}>
            {error && (
              <div id="login-error" role="alert" className="flex items-start gap-2 rounded-md border border-[#f1c9c4] bg-[#fdf3f2] px-3 py-2.5 text-[13px] text-[#9f2a1c]">
                <AlertCircle className="size-4 mt-px shrink-0" aria-hidden /> {error}
              </div>
            )}

            <div>
              <label htmlFor="login-id" className="block text-[13px] font-medium text-ink mb-1.5">Login ID</label>
              <input id="login-id" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false}
                placeholder="Enter login ID" value={loginId} disabled={pending} autoFocus
                onChange={e => setLoginId(e.target.value)} aria-invalid={idMissing} aria-describedby={idMissing ? 'login-id-err' : undefined}
                className={`${INPUT} ${idMissing ? 'border-state-critical' : 'border-line-strong'}`} />
              {idMissing && <p id="login-id-err" className="mt-1 text-[12px] text-state-critical">Enter your login ID.</p>}
            </div>

            <div>
              <label htmlFor="password" className="block text-[13px] font-medium text-ink mb-1.5">Password</label>
              <div className="relative">
                <input id="password" name="password" type={show ? 'text' : 'password'} autoComplete="current-password"
                  placeholder="Enter password" value={password} disabled={pending}
                  onChange={e => setPassword(e.target.value)} aria-invalid={pwMissing} aria-describedby={pwMissing ? 'password-err' : undefined}
                  className={`${INPUT} pr-10 ${pwMissing ? 'border-state-critical' : 'border-line-strong'}`} />
                <button type="button" onClick={() => setShow(s => !s)} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}
                  aria-controls="password"
                  className="absolute inset-y-0 right-0 w-10 grid place-items-center text-ink-3 hover:text-ink rounded-r-md">
                  {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
                </button>
              </div>
              {pwMissing && <p id="password-err" className="mt-1 text-[12px] text-state-critical">Enter your password.</p>}
            </div>

            <button type="submit" disabled={pending} aria-busy={pending}
              className="w-full h-10 mt-2 rounded-md bg-ink text-paper text-[14px] font-semibold inline-flex items-center justify-center gap-2
                hover:bg-black disabled:opacity-60 disabled:cursor-not-allowed transition-colors">
              {pending && <Loader2 className="size-4 animate-spin" aria-hidden />}
              {pending ? 'Signing in…' : 'Sign In'}
            </button>
          </form>
          {/* secondary entry to the public rider portal: no account, no credentials */}
          <Link to="/user" className="mt-3 w-full h-9 rounded-md border border-line-strong text-ink-2 text-[12.5px] font-semibold tracking-wide uppercase
            inline-flex items-center justify-center hover:border-ink hover:text-ink transition-colors">User login</Link>
        </div>
        <p className="mt-5 text-center text-[11.5px] text-ink-3">Authorized personnel only · advisory simulation system</p>
      </div>
    </main>
  )
}
