import { useMutation, useQuery } from '@tanstack/react-query'
import { Play } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MapLegend } from '../components/MapLegend'
import { TrafficMap, type Fit, type IncidentMark, type SegmentStyle, type TipData } from '../components/TrafficMap'
import { Empty, ErrorState, KpiTable, Loading, Panel, Tag } from '../components/ui'
import { useSim } from '../hooks/useSim'
import { api } from '../services/api'
import type { SegmentEffect } from '../types'
import { deltaColor, fmt, signed } from '../utils/format'
import { changeStyle, vcStyle } from '../utils/mapStyles'
import { SegmentPicker } from './Forecast'

export default function Simulation() {
  const { t, seg, setSeg } = useSim()
  const [params, setParams] = useSearchParams()
  const [reduction, setReduction] = useState(Number(params.get('red') ?? 0.5))
  const [cand, setCand] = useState(params.get('cand') ?? '')
  const cands = useQuery({ queryKey: ['candidates'], queryFn: api.candidates, staleTime: Infinity })
  const run = useMutation({ mutationFn: api.simulate })
  const [rightView, setRightView] = useState<'change' | 'congestion'>('change')

  const submit = useCallback(() => {
    run.mutate({ time: t, incident_segment: seg ?? undefined, capacity_reduction: seg ? reduction : 0, candidate_id: cand || undefined })
    const p = new URLSearchParams(params)
    if (cand) p.set('cand', cand); else p.delete('cand')
    p.set('red', String(reduction))
    setParams(p, { replace: true })
  }, [run, t, seg, reduction, cand, params, setParams])

  // arriving from the Interventions page with a candidate: run immediately (explicit user click there)
  useEffect(() => { if (t && params.get('cand') && !run.data && !run.isPending) submit() }, [t]) // eslint-disable-line react-hooks/exhaustive-deps

  const res = run.data
  const per = useMemo(() => new Map((res?.per_segment ?? []).map(s => [s.segment_id, s])), [res])
  const highlight = useMemo(() => new Set([seg, cands.data?.find(c => c.candidate_id === cand)?.target_segment].filter(Boolean) as string[]), [seg, cand, cands.data])
  // the segments the scenario actually touches (> 1% travel-time change); everything else stays quiet context
  const affected = useMemo(() => new Set((res?.per_segment ?? []).filter(s => s.time_before_min > 0 && Math.abs(s.delta_time_min) / s.time_before_min > 0.01)
    .map(s => s.segment_id)), [res])
  // left map: baseline congestion; right map: what the scenario changes (default) or its congestion.
  const mapStyle = (which: 'before' | 'after') => (id: string): SegmentStyle | null => {
    const s = per.get(id)
    if (!s) return null
    const hl = highlight.has(id)
    let st: SegmentStyle | null
    if (which === 'after' && rightView === 'change') st = changeStyle(s.delta_time_min, s.time_before_min)
    else {
      st = vcStyle(which === 'before' ? s.vc_before : s.vc_after)
      if (st && !hl && !affected.has(id)) st = { ...st, opacity: 0.3, weight: Math.min(st.weight ?? 3, 2.4) }   // unaffected congestion is context, not message
    }
    return hl ? { ...(st ?? { color: '#16181d' }), weight: Math.max(st?.weight ?? 3, 5.5), opacity: 1 } : st
  }
  const tip = (id: string): TipData | null => {
    const s = per.get(id)
    return s ? { title: id, badge: affected.has(id) ? 'changed' : undefined, rows: [
      ['Volume / capacity', `${fmt(s.vc_before, 2)} → ${fmt(s.vc_after, 2)}`],
      ['Travel time', `${fmt(s.time_before_min, 2)} → ${fmt(s.time_after_min, 2)} min`],
      ['Change', `${signed(s.delta_time_min, 3)} min`]] } : null
  }
  // frame the target plus the improved and worsened segments; the baseline map is the camera master, the right map follows
  const fit = useMemo<Fit | null>(() => (res ? { key: `${res.time}|${res.counterfactual_description}|${seg}`,
    ids: [...highlight, ...(res.focus ?? []), ...res.largest_improvements, ...res.worst_side_effects].map(x => (typeof x === 'string' ? x : x.segment_id)) } : null),
    [res, highlight, seg])
  const incidents = useMemo<IncidentMark[]>(() => (seg && reduction > 0 ? [{ id: seg, severity: reduction >= 0.5 ? 'CRITICAL' : reduction >= 0.3 ? 'HEAVY' : 'MODERATE' }] : []), [seg, reduction])
  const simChip = <span className="fs-chip !bg-[#e6f4f1] !border-better text-better font-semibold tracking-wide">SIMULATION ESTIMATE</span>
  const canRun = Boolean(t && (cand || (seg && reduction > 0)))
  const noPool = cand === '' && !seg

  return (
    <div className="p-3 flex flex-col gap-3">
      <Panel title="Scenario" right={<Tag kind="SIMULATED">simulation estimate</Tag>}>
        <div className="flex flex-wrap items-end gap-5">
          <div><div className="eyebrow mb-1">Incident segment (current network)</div>
            <div className="flex items-center gap-2"><SegmentPicker value={seg} onChange={setSeg} />
              {seg && <button className="text-[11px] text-ink-3 underline" onClick={() => setSeg(null)}>none</button>}</div></div>
          <label className="block">
            <span className="eyebrow">Capacity loss on incident segment</span>
            <div className="flex items-center gap-2 mt-1">
              <input type="range" min={0} max={0.9} step={0.05} value={reduction} disabled={!seg}
                onChange={e => setReduction(Number(e.target.value))} aria-label="Capacity loss" />
              <span className="num w-10">{Math.round(reduction * 100)}%</span>
            </div>
          </label>
          <label className="block">
            <span className="eyebrow">Planning candidate (counterfactual)</span>
            <select value={cand} onChange={e => setCand(e.target.value)} className="mt-1 block h-7 border border-line rounded-sm bg-paper px-1 num">
              <option value="">— none: compare with incident cleared —</option>
              {cands.data?.map(c => <option key={c.candidate_id} value={c.candidate_id}>
                {c.candidate_id} · {c.intervention_type} · {c.target_segment} · +{c.capacity_delta_vph}</option>)}
            </select>
          </label>
          <button onClick={submit} disabled={!canRun || run.isPending}
            className="inline-flex items-center gap-1.5 h-8 px-3 bg-ink text-paper rounded-sm font-semibold disabled:opacity-40">
            <Play className="size-3.5" /> {run.isPending ? 'Simulating…' : 'SIMULATE'}</button>
        </div>
        <p className="text-[11px] text-ink-3 mt-2">Baseline = network at {t.slice(0, 16)} {seg ? `with the incident capacity loss on ${seg}` : ''}. Counterfactual = baseline + the candidate, or the incident cleared.
          Flows are pivoted on the observed flows at that time; nothing is applied to any real system.</p>
        {noPool && <p className="text-[12px] text-ink-2 mt-1">Pick an incident segment and/or a candidate.</p>}
      </Panel>

      {run.isPending && <Panel><Loading label="Solving equilibrium for baseline and counterfactual" /></Panel>}
      {run.error && <Panel><ErrorState error={run.error} what="simulation result" /></Panel>}
      {!res && !run.isPending && !run.error && <Panel><Empty>Configure a scenario and press SIMULATE.</Empty></Panel>}
      {res && (
        <>
          <div className="text-[12px] text-ink-2"><Tag kind="SIMULATED">{res.label}</Tag>
            <span className="ml-2"><b>Baseline:</b> {res.baseline_description}. <b>Counterfactual:</b> {res.counterfactual_description}.</span></div>
          <div className="grid grid-cols-2 gap-3 h-[380px]">
            {(['before', 'after'] as const).map(w => (
              <div key={w} className="border border-line rounded-md bg-panel">
                <TrafficMap className="size-full" styleFor={mapStyle(w)} tooltipFor={tip} wheel="on-click" syncKey="sim" follower={w === 'after'}
                  fit={fit} incidents={incidents} badge={simChip}
                  title={w === 'before' ? 'Baseline · congestion (v/c)' : rightView === 'change' ? 'Simulated counterfactual · change' : 'Simulated counterfactual · congestion (v/c)'}
                  controls={w === 'after' ? (
                    <div className="fs-card !rounded-sm p-0.5 flex text-[11px]" role="tablist" aria-label="Counterfactual view">
                      {(['change', 'congestion'] as const).map(v => (
                        <button key={v} role="tab" aria-selected={rightView === v} onClick={() => setRightView(v)}
                          className={`px-2 h-6 rounded-[3px] ${rightView === v ? 'bg-ink text-paper' : 'text-ink-2'}`}>{v}</button>))}
                    </div>) : undefined}
                  legend={w === 'after' ? <MapLegend simulation /> : undefined} />
              </div>
            ))}
          </div>
          <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] gap-3">
            <Panel title="KPI comparison" right={<Tag kind="SIMULATED" />}>
              <KpiTable kpis={res.kpis} />
              <div className={`mt-2 text-[12px] ${res.convergence.network_change_within_tolerance ? 'text-worse' : 'text-ink-3'}`}>
                {res.convergence.network_change_within_tolerance
                  ? `Network-wide change is within the solver's convergence tolerance (${fmt(res.convergence.tolerance_veh_h, 1)} veh·h): treat the network totals as "no measurable change" and read the local effects.`
                  : `Network-wide change exceeds the solver tolerance (${fmt(res.convergence.tolerance_veh_h, 1)} veh·h).`}
              </div>
            </Panel>
            <Panel title="Where it changes">
              <div className="flex gap-6 mb-2 num"><span className="text-better">{res.segments_improved} improved</span><span className="text-worse">{res.segments_worsened} worsened</span>
                <span className="text-ink-3 font-sans">(travel-time change &gt; 1%)</span></div>
              {res.focus && <EffectList title="Focus segments" rows={res.focus} />}
              <EffectList title="Largest improvements" rows={res.largest_improvements} />
              <EffectList title="Side effects (worsened)" rows={res.worst_side_effects} />
            </Panel>
          </div>
        </>
      )}
    </div>
  )
}

function EffectList({ title, rows }: { title: string; rows: SegmentEffect[] }) {
  if (!rows.length) return null
  return (
    <div className="mb-2">
      <div className="eyebrow mb-0.5">{title}</div>
      <table className="w-full num text-[12px]"><tbody>
        {rows.map(r => (
          <tr key={r.segment_id} className="border-b border-line/60">
            <td className="py-0.5">{r.segment_id}</td>
            <td className="text-right">{fmt(r.time_before_min, 2)} → {fmt(r.time_after_min, 2)} min</td>
            <td className="text-right" style={{ color: deltaColor(r.delta_time_min) }}>{signed(r.delta_time_min, 3)}</td>
            <td className="text-right text-ink-3">v/c {fmt(r.vc_before, 2)}→{fmt(r.vc_after, 2)}</td>
          </tr>
        ))}
      </tbody></table>
    </div>
  )
}
