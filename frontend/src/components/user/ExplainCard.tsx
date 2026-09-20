import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { useState } from 'react'
import { userApi } from '../../services/userApi'
import type { Explanation, ExplainTarget } from '../../types/user'
import { LEVEL_COLOR } from '../../utils/userTraffic'

const CERTAINTY: Record<string, string> = { reported: 'Reported', likely: 'Likely', possible: 'Possible', unknown: 'Unknown' }
const HORIZONS = [['15m', '+15 min'], ['30m', '+30 min'], ['60m', '+60 min']] as const
const FAIL = 'Traffic disruption detected, but the cause could not be determined.'

/**
 * "Why is traffic heavy?" — renders backend-computed evidence only (POST /api/user/explain). No rules live here:
 * the wording of each line, the certainty and the forecast all come from FlowSense's diagnosis. Fact vs inference is explicit:
 * Reported (a record exists) / Likely / Possible / Unknown.
 */
export function ExplainCard({ target, step, saving, onViewAlternative, onClose, auto }: {
  target: ExplainTarget; step: number; saving?: number | null; onViewAlternative?: () => void; onClose?: () => void; auto?: boolean
}) {
  const [open, setOpen] = useState(Boolean(target.open))
  const q = useQuery({
    queryKey: ['u-explain', step, target.segment_id ?? null, target.lat.toFixed(3), target.lon.toFixed(3)],
    queryFn: () => userApi.explain(target, step), staleTime: 20_000, retry: 0,
  })
  if (q.isPending) return <p role="status" className="rounded-xl border border-line bg-white px-3.5 py-3 text-[12.5px] text-ink-3">Checking what is behind this traffic…</p>
  const e: Explanation | undefined = q.data
  if (q.isError || !e || e.status === 'unavailable') {
    return <section aria-label="Traffic explanation" className="rounded-xl border border-line bg-white px-3.5 py-3 text-[13px] text-ink-2">{FAIL}</section>
  }
  if (e.status === 'normal') {
    return auto ? null : (
      <section aria-label="Traffic explanation" className="rounded-xl border border-line bg-white px-3.5 py-3 text-[13px] text-ink-2 flex justify-between gap-2">
        <span>{e.summary}</span>{onClose && <button type="button" onClick={onClose} aria-label="Close explanation" className="text-ink-3"><X className="size-4" aria-hidden /></button>}
      </section>
    )
  }
  const c = e.cause, f = e.forecast
  const shown = open ? e.evidence : e.evidence.slice(0, 3)
  const conf = c?.confidence != null ? `${c.confidence_label} (${Math.round(c.confidence * 100)}%)` : c?.confidence_label ?? null
  const worst = f && HORIZONS.map(([k]) => f.horizons[k]?.confidence).filter(Boolean).sort((a, b) => ['Low', 'Medium', 'High'].indexOf(a!) - ['Low', 'Medium', 'High'].indexOf(b!))[0]
  return (
    <section aria-label="Traffic explanation" className="rounded-xl border border-line bg-white px-3.5 py-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2 text-[11px] font-semibold tracking-wider uppercase text-ink-2">
            <span className="size-2 rounded-full" style={{ background: LEVEL_COLOR[e.traffic_level] }} aria-hidden />{e.headline}
          </div>
          <div className="mt-1.5 text-[11px] text-ink-3">{c?.certainty === 'reported' ? 'Reported cause' : c?.certainty === 'unknown' ? 'Cause' : 'Possible cause'}</div>
          <div className="text-[15px] font-semibold leading-tight">{c?.label ?? 'Cause unclear'}</div>
        </div>
        {onClose && <button type="button" onClick={onClose} aria-label="Close explanation" className="text-ink-3 hover:text-ink"><X className="size-4" aria-hidden /></button>}
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-2">
        {c && <span>Certainty: <b className="font-semibold text-ink">{CERTAINTY[c.certainty] ?? 'Unknown'}</b></span>}
        {conf && <span>Confidence: <b className="font-semibold text-ink">{conf}</b></span>}
        {c?.type && <span>Type: <b className="font-semibold text-ink">{c.type}</b></span>}
      </div>
      <p className="mt-2 text-[12.5px] text-ink-2">{e.summary}</p>
      {c?.type_note && open && <p className="mt-1 text-[12px] text-ink-3">{c.type_note}</p>}

      {shown.length > 0 && (
        <ul className="mt-2 space-y-1 text-[12.5px] text-ink-2" aria-label="Why FlowSense says this">
          {shown.map(i => <li key={i.signal} className="flex gap-2"><span className="text-ink-3" aria-hidden>•</span>{i.message}</li>)}
        </ul>
      )}

      {f && (
        <div className="mt-2.5" aria-label="Traffic forecast">
          <div className="text-[11px] font-semibold tracking-wider uppercase text-ink-3">{open ? 'Traffic forecast (estimated)' : 'Expected next'}</div>
          {open ? (
            <>
              <div className="mt-1 grid grid-cols-4 gap-1.5 text-center text-[12px]">
                {[['Now', f.now, f.now_label] as const, ...HORIZONS.map(([k, l]) => [l, f.horizons[k]?.level, f.horizons[k]?.label] as const)].map(([l, lv, t]) => (
                  <div key={l} className="rounded-lg border border-line px-1 py-1.5"><div className="text-ink-3">{l}</div>
                    <div className="font-medium flex items-center justify-center gap-1"><span className="size-1.5 rounded-full" style={{ background: lv ? LEVEL_COLOR[lv] : '#9ca3af' }} aria-hidden />{t?.replace(' traffic', '') ?? '—'}</div></div>
                ))}
              </div>
              <p className="mt-1.5 text-[12.5px] text-ink-2">{f.why}{f.spread ? ` ${f.spread}` : ''}{worst ? ` Forecast confidence: ${worst}.` : ''}</p>
            </>
          ) : (
            <p className="text-[12.5px] text-ink-2">{f.trend === 'worsening' ? 'Traffic may worsen in the next hour.' : f.trend === 'easing' ? 'Traffic is expected to ease.' : 'Traffic is expected to stay about the same.'}</p>
          )}
        </div>
      )}
      {open && <p className="mt-2 text-[11.5px] text-ink-3">Based on the FlowSense traffic simulation ({e.updated_at.slice(11, 16)}), not live city traffic. An inference is not a confirmed incident report.</p>}

      <div className="mt-2.5 flex items-center gap-3 flex-wrap">
        <button type="button" onClick={() => setOpen(o => !o)} aria-expanded={open}
          className="h-8 px-3 rounded-full border border-line-strong text-[12.5px] font-medium hover:border-ink-3 inline-flex items-center gap-1">
          {open ? 'Hide details' : 'Why is traffic heavy?'}{open ? <ChevronUp className="size-3.5" aria-hidden /> : <ChevronDown className="size-3.5" aria-hidden />}
        </button>
        {onViewAlternative && <button type="button" onClick={onViewAlternative} className="text-[12.5px] text-accent hover:underline">
          View alternative route{saving ? ` (estimated ${saving} min faster)` : ''}</button>}
      </div>
    </section>
  )
}
