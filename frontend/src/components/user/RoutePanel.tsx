import { AlertTriangle, ArrowRight, Info, Route as RouteIcon, TrafficCone } from 'lucide-react'
import type { Diversion, RouteOption, UserAlert } from '../../types/user'
import { LEVEL_COLOR, LEVEL_LABEL, delayText, km, minutes } from '../../utils/userTraffic'

const DIVERSION_REASON: Record<string, string> = {
  severe: 'Traffic ahead is severe.', heavy: 'Traffic ahead is heavy.', worsening: 'Traffic ahead is expected to get heavier.',
}
const ALERT_COLOR: Record<string, string> = { severe: '#c62828', heavy: '#e0661b', warning: '#b07d09', info: '#4a4d55' }

export function AlertList({ alerts, onWhy }: { alerts: UserAlert[]; onWhy?: (a: UserAlert) => void }) {
  if (!alerts.length) return null
  return (
    <ul className="space-y-2" aria-label="Traffic alerts">
      {alerts.map(a => (
        <li key={`${a.type}-${a.title}-${a.segment_id ?? ''}`} className="flex gap-2.5 rounded-xl border border-line bg-white px-3 py-2.5" role="status">
          <AlertTriangle className="size-4 mt-0.5 shrink-0" style={{ color: ALERT_COLOR[a.severity] ?? ALERT_COLOR.info }} aria-hidden />
          <div><div className="text-[13px] font-semibold">{a.title}</div><div className="text-[12.5px] text-ink-2">{a.message}</div>
            {onWhy && a.lat != null && a.type !== 'forecast' && <button type="button" onClick={() => onWhy(a)} className="mt-1 text-[12.5px] text-accent hover:underline">Why is traffic heavy?</button>}</div>
        </li>
      ))}
    </ul>
  )
}

export function DiversionCard({ diversion, routes, selectedId, onView, onKeep, dismissed }: {
  diversion: Diversion; routes: RouteOption[]; selectedId: string | null; onView: () => void; onKeep: () => void; dismissed: boolean
}) {
  const alt = routes.find(r => r.id === diversion.alternative_route_id)
  if (!alt || dismissed) return null
  const onAlt = selectedId === alt.id
  return (
    <section aria-label="Diversion recommended" className="rounded-xl border border-[#ecc99a] bg-[#fff8ee] px-3.5 py-3">
      <div className="flex items-center gap-2 text-[11px] font-semibold tracking-wider uppercase text-[#8a4a06]">
        <TrafficCone className="size-3.5" aria-hidden /> Diversion recommended</div>
      <div className="mt-1 text-[14px] font-semibold">{DIVERSION_REASON[diversion.reason] ?? 'Traffic ahead is heavy.'}</div>
      <div className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
        <span className="text-ink-3">Current route</span><span className="num">{minutes(diversion.current_eta_min)}</span>
        <span className="text-ink-3">Alternative</span>
        <span><span className="num">{minutes(diversion.alternative_eta_min)}</span> <span className="text-ink-3">· {LEVEL_LABEL[diversion.alternative_traffic_level].toLowerCase()}</span></span>
        <span className="text-ink-3">Estimated saving</span><span className="num font-semibold">about {diversion.estimated_saving_min} min</span>
        <span className="text-ink-3">Confidence</span><span>{diversion.confidence}</span>
      </div>
      <div className="mt-2.5 flex gap-2">
        <button type="button" onClick={onView} disabled={onAlt} className="h-8 px-3 rounded-lg bg-ink text-paper text-[12.5px] font-semibold disabled:opacity-60 inline-flex items-center gap-1.5">
          {onAlt ? 'Alternative selected' : <>View alternative <ArrowRight className="size-3.5" aria-hidden /></>}</button>
        <button type="button" onClick={onKeep} className="h-8 px-3 rounded-lg border border-line-strong text-[12.5px] text-ink-2 hover:text-ink">Keep current route</button>
      </div>
      <p className="mt-2 text-[11px] text-ink-3">Estimates only, based on simulated traffic. Actual savings are not guaranteed.</p>
    </section>
  )
}

function RouteCard({ r, selected, onSelect, count }: { r: RouteOption; selected: boolean; onSelect: () => void; count: number }) {
  const f30 = r.forecast?.['30']
  const shift = f30 == null ? 0 : f30 - r.duration_min
  return (
    <li>
      <button type="button" onClick={onSelect} aria-pressed={selected}
        className={`w-full text-left rounded-xl border px-3.5 py-3 transition-colors ${selected ? 'border-ink bg-white shadow-[0_2px_10px_-6px_rgba(22,24,29,.4)]' : 'border-line bg-white/70 hover:border-line-strong'}`}>
        <div className="flex items-center justify-between gap-2">
          <span className={`text-[11px] font-semibold tracking-wider uppercase ${r.recommended ? 'text-better' : 'text-ink-3'}`}>{r.label}</span>
          <span className="text-[11px] text-ink-3 num">{r.ranking} of {count}</span>
        </div>
        <div className="mt-0.5 flex items-baseline gap-3">
          <span className="text-[22px] font-semibold tracking-tight num">{minutes(r.duration_min)}</span>
          <span className="text-[13px] text-ink-2 num">{km(r.distance_km)}</span>
        </div>
        <div className="mt-1 flex items-center gap-2 text-[12.5px] text-ink-2">
          <span className="inline-block size-2.5 rounded-full" style={{ background: LEVEL_COLOR[r.traffic_level] }} aria-hidden />
          {LEVEL_LABEL[r.traffic_level]} · {delayText(r)}
        </div>
        {shift >= 2 && <div className="mt-1 text-[12px] text-[#9a5a06]">Expected to take about {minutes(f30)} in 30 min</div>}
        {shift <= -2 && <div className="mt-1 text-[12px] text-ink-3">May ease to about {minutes(f30)} in 30 min</div>}
        {r.simulation_coverage_pct < 50 && <div className="mt-1 text-[11.5px] text-ink-3">Simulated traffic covers only part of this route (gray = no data)</div>}
      </button>
    </li>
  )
}

export function RouteList({ status, routes, selectedId, onSelect, error, onRetry, note }: {
  status: 'idle' | 'loading' | 'ok' | 'error'; routes: RouteOption[]; selectedId: string | null
  onSelect: (id: string) => void; error?: string; onRetry: () => void; note?: string
}) {
  if (status === 'loading' && routes.length === 0) {
    return (
      <div role="status" aria-label="Calculating routes" className="space-y-2">
        {[0, 1].map(i => <div key={i} className="h-[92px] rounded-xl border border-line bg-white/70 animate-pulse" />)}
        <p className="text-[12.5px] text-ink-3 px-1">Calculating routes…</p>
      </div>
    )
  }
  if (status === 'error') {
    return (
      <div role="alert" className="rounded-xl border border-[#f1c9c4] bg-[#fdf3f2] px-3.5 py-3 text-[13px] text-[#9f2a1c]">
        <div>{error ?? 'Route could not be calculated. Please try another destination.'}</div>
        <button type="button" onClick={onRetry} className="mt-1.5 text-[12.5px] font-semibold underline underline-offset-2">Try again</button>
      </div>
    )
  }
  if (!routes.length) return null
  return (
    <div>
      <ul className="space-y-2" aria-label="Route options">
        {routes.map(r => <RouteCard key={r.id} r={r} count={routes.length} selected={selectedId === r.id} onSelect={() => onSelect(r.id)} />)}
      </ul>
      <p className="mt-2.5 flex gap-1.5 text-[11px] text-ink-3"><Info className="size-3.5 shrink-0 mt-px" aria-hidden />
        {note ?? 'Traffic is simulated by FlowSense and only approximately matched to these roads.'}</p>
      {routes.length === 1 && <p className="mt-1 flex gap-1.5 text-[11px] text-ink-3"><RouteIcon className="size-3.5 shrink-0 mt-px" aria-hidden />
        No alternative route was available for this trip.</p>}
    </div>
  )
}
