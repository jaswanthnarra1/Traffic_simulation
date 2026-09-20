import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, LogOut, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useSim } from '../hooks/useSim'
import { api } from '../services/api'
import { dateLabel, pct, shiftTime } from '../utils/format'
import { Tag } from './ui'

const NAV = [
  ['/dashboard', 'Dashboard'], ['/incidents', 'Incidents'], ['/forecast', 'Forecast'], ['/propagation', 'Propagation'],
  ['/interventions', 'Interventions'], ['/flyover', 'Infrastructure'], ['/simulation', 'Simulation'], ['/evaluation', 'Evaluation'],
] as const

export function Layout() {
  const { search } = useLocation()
  return (
    <div className="h-full flex flex-col">
      <header className="h-14 shrink-0 bg-panel border-b border-line px-3 sm:px-4 flex items-center gap-4 xl:gap-8">
        <Link to={{ pathname: '/dashboard', search }} className="shrink-0 flex items-center" aria-label="FlowSense AI — Dashboard">
          {/* official brand asset (transparent PNG made from the supplied logo); width/height attrs reserve space → no layout shift */}
          <img src="/flowsense-logo.png" alt="FlowSense AI" width={548} height={120}
            className="h-7 sm:h-8 lg:h-9 w-auto" />
        </Link>
        {/* compact on tablet, horizontally scrollable on mobile (no separate mobile menu) */}
        <nav className="h-full min-w-0 flex items-stretch gap-0.5 overflow-x-auto [scrollbar-width:none]" aria-label="Main">
          {NAV.map(([to, label]) => (
            <NavLink key={to} to={{ pathname: to, search }} 
              className={({ isActive }) => `shrink-0 px-2 xl:px-3 flex items-center border-b-2 text-[12.5px] xl:text-[13px] ${isActive
                ? 'border-ink text-ink font-semibold' : 'border-transparent text-ink-2 hover:text-ink'}`}>
              {label}
            </NavLink>
          ))}
        </nav>
        <SessionMenu />
      </header>
      <StatusBar />
      <main className="flex-1 min-h-0 overflow-auto"><Outlet /></main>
    </div>
  )
}

/** Subtle signed-in indicator + logout, far right of the header. */
function SessionMenu() {
  const { currentUser, logout } = useAuth()
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  if (!currentUser) return null
  const signOut = async () => {
    setBusy(true)
    try { await logout() } finally { navigate('/login', { replace: true }) }
  }
  return (
    <div className="ml-auto shrink-0 flex items-center gap-3 pl-2">
      <div className="hidden xl:block text-right leading-tight">
        <div className="text-[11px] font-semibold tracking-wider uppercase text-ink">{currentUser.display_name}</div>
        <div className="text-[10.5px] text-ink-3 inline-flex items-center gap-1">
          <span className="size-1.5 rounded-full bg-state-normal" aria-hidden />Authenticated</div>
      </div>
      <button onClick={signOut} disabled={busy} title="Log out"
        className="h-8 px-2.5 inline-flex items-center gap-1.5 rounded-md border border-line text-[12px] text-ink-2 hover:text-ink hover:border-ink-3 disabled:opacity-50">
        <LogOut className="size-3.5" aria-hidden /><span className="hidden sm:inline">Logout</span><span className="sm:hidden sr-only">Logout</span>
      </button>
    </div>
  )
}

function StatusBar() {
  const { t, meta, setTime } = useSim()
  const snap = useQuery({ queryKey: ['traffic', t], queryFn: () => api.traffic(t), enabled: Boolean(t) })
  const s = snap.data
  const normal = s ? (s.summary.states.NORMAL ?? 0) / s.segments.length : null
  const step = (m: number) => setTime(shiftTime(t, m))
  const btn = 'size-7 grid place-items-center rounded-sm border border-line hover:border-ink-3 text-ink-2 disabled:opacity-40'
  return (
    <div className="shrink-0 bg-panel/60 border-b border-line px-3 sm:px-4 min-h-12 py-1.5 flex flex-wrap items-center gap-x-6 gap-y-1.5">
      <span className="text-ink-3 text-[12px] hidden xl:inline">AI Traffic Intelligence &amp; Intervention Simulator</span>

      <div className="flex items-center gap-1.5" role="group" aria-label="Simulation clock">
        <button className={btn} onClick={() => step(-60)} aria-label="Back 1 hour" disabled={!t}><ChevronsLeft className="size-4" /></button>
        <button className={btn} onClick={() => step(-5)} aria-label="Back 5 minutes" disabled={!t}><ChevronLeft className="size-4" /></button>
        <label className="sr-only" htmlFor="simclock">Simulation time</label>
        <input id="simclock" type="datetime-local" step={300} className="num h-7 px-2 border border-line rounded-sm bg-paper text-[12px]"
          value={t ? t.slice(0, 16) : ''} min={meta?.data_range[0].slice(0, 16)} max={meta?.data_range[1].slice(0, 16)}
          onChange={e => e.target.value && setTime(e.target.value.replace('T', ' ') + ':00')} />
        <button className={btn} onClick={() => step(5)} aria-label="Forward 5 minutes" disabled={!t}><ChevronRight className="size-4" /></button>
        <button className={btn} onClick={() => step(60)} aria-label="Forward 1 hour" disabled={!t}><ChevronsRight className="size-4" /></button>
        <button className={btn} onClick={() => meta && setTime(meta.demo_time)} aria-label="Reset to demo time" title="Reset to demo time">
          <RotateCcw className="size-3.5" /></button>
      </div>

      <div className="flex items-center gap-5 ml-auto text-[12px] whitespace-nowrap overflow-x-auto">
        <Status label="Simulation time" value={t ? `${dateLabel(t)} ${t.slice(11, 16)}` : '—'} />
        <Status label="Data quality" value={s ? pct(s.summary.mean_data_quality) : '—'} />
        <Status label="Network health" value={normal !== null ? `${pct(normal, 0)} normal` : '—'} />
        <Status label="Last update" value={snap.dataUpdatedAt ? new Date(snap.dataUpdatedAt).toLocaleTimeString() : '—'} />
        <div className="flex gap-1.5">
          <Tag kind="LIVE">Live</Tag>
          {s?.in_training_period && <Tag kind="INFERRED">in-sample period</Tag>}
        </div>
      </div>
    </div>
  )
}

function Status({ label, value }: { label: string; value: string }) {
  return (
    <div className="leading-tight">
      <div className="eyebrow">{label}</div>
      <div className="num text-ink">{value}</div>
    </div>
  )
}
