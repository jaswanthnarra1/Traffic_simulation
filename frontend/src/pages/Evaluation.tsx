import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ErrorState, Loading, Panel, Tag } from '../components/ui'
import { api } from '../services/api'
import { fmt, pct } from '../utils/format'

/* eslint-disable @typescript-eslint/no-explicit-any */
function useEval(kind: string) {
  return useQuery({ queryKey: ['eval', kind], queryFn: () => api.evaluation<any>(kind), staleTime: Infinity })
}

function Block({ kind, title, tag, children }: { kind: string; title: string; tag: ReactNode; children: (d: any) => ReactNode }) {
  const q = useEval(kind)
  return (
    <Panel title={title} right={tag}>
      {q.isLoading ? <Loading /> : q.error || !q.data ? <ErrorState error={q.error} what={`${kind} metrics`} /> : children(q.data)}
    </Panel>
  )
}

const TH = ({ children }: { children: ReactNode }) => <th className="font-medium px-2 py-1.5 text-left">{children}</th>
const TD = ({ children, className = '' }: { children: ReactNode; className?: string }) => <td className={`px-2 py-1 num ${className}`}>{children}</td>

export default function Evaluation() {
  return (
    <div className="p-3 grid grid-cols-2 gap-3 items-start">
      <div className="col-span-2 text-[12px] text-ink-2">
        Every number below is read from <span className="num">backend/artifacts/metrics/*.json</span>, produced by the evaluation scripts. Categories are never mixed:
        <span className="ml-2 inline-flex gap-1.5"><Tag kind="VALIDATION">Validation</Tag><Tag kind="BENCHMARK">Benchmark</Tag>
          <Tag kind="SYNTHETIC">Synthetic robustness test</Tag><Tag kind="SIMULATED">Simulation estimate</Tag></span>
      </div>

      <div className="col-span-2">
        <Block kind="forecast" title="Forecast accuracy · held-out validation days" tag={<Tag kind="VALIDATION">Validation</Tag>}>
          {d => {
            const rows = Object.entries(d.results as Record<string, any>)
            const chart = rows.filter(([k]) => k.startsWith('speed')).map(([k, r]) => ({ h: k.replace('speed_', '+'), model: r.model.mae, persistence: r.persistence.mae }))
            return (
              <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-4">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-ink-3 border-b border-line"><TH>Target</TH><TH>Model MAE</TH><TH>Persistence MAE</TH><TH>Δ MAE</TH><TH>RMSE</TH><TH>P10–P90 coverage</TH></tr></thead>
                  <tbody>{rows.map(([k, r]) => (
                    <tr key={k} className="border-b border-line/60">
                      <TD className="font-sans">{k.replace('_', ' +')}</TD><TD>{fmt(r.model.mae, 4)}</TD><TD className="text-ink-3">{fmt(r.persistence.mae, 4)}</TD>
                      <TD className={r.mae_improvement_pct > 0 ? 'text-better' : 'text-worse'}>{r.mae_improvement_pct > 0 ? '+' : ''}{fmt(r.mae_improvement_pct, 1)}%</TD>
                      <TD>{fmt(r.model.rmse, 4)}</TD><TD>{pct(r.p10_p90_coverage)}</TD>
                    </tr>))}</tbody>
                </table>
                <div>
                  <div className="eyebrow mb-1">Speed MAE (km/h) · model vs persistence</div>
                  <div className="h-[220px]"><ResponsiveContainer>
                    <BarChart data={chart} margin={{ top: 4, right: 4, left: -16, bottom: 0 }}>
                      <CartesianGrid stroke="#ebe9e3" vertical={false} /><XAxis dataKey="h" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} />
                      <Tooltip formatter={(v: unknown) => fmt(v as number, 3)} contentStyle={{ fontSize: 12 }} /><Legend wrapperStyle={{ fontSize: 11 }} />
                      <Bar dataKey="persistence" fill="#b9b6ae" isAnimationActive={false} /><Bar dataKey="model" fill="#1d4ed8" isAnimationActive={false} />
                    </BarChart></ResponsiveContainer></div>
                  <p className="text-[11px] text-ink-3">{d.anchors} anchors × {d.segments} segments from forecast_targets_validation.csv (labels only). Nominal band coverage 80% — empirical, not calibrated.</p>
                </div>
              </div>
            )
          }}
        </Block>
      </div>

      <Block kind="detection" title="Incident detection" tag={<Tag kind="VALIDATION">Validation</Tag>}>
        {d => (
          <div className="space-y-2">
            <table className="w-full text-[12px]">
              <thead><tr className="text-ink-3 border-b border-line"><TH>Split</TH><TH>Precision</TH><TH>Recall</TH><TH>F1</TH><TH>FP slots/day</TH><TH>Incidents found</TH><TH>Median delay</TH></tr></thead>
              <tbody>{Object.entries(d.splits as Record<string, any>).map(([k, s]) => (
                <tr key={k} className="border-b border-line/60">
                  <TD className="font-sans">{k === 'train' ? 'train (calibration)' : 'validation (held-out)'}</TD>
                  <TD>{fmt(s.slot_level.precision, 3)}</TD><TD>{fmt(s.slot_level.recall, 3)}</TD><TD>{fmt(s.slot_level.f1, 3)}</TD>
                  <TD>{fmt(s.slot_level.false_positive_slots_per_day, 1)}</TD>
                  <TD>{s.event_level.incidents_detected}/{s.event_level.incidents}</TD><TD>{fmt(s.event_level.median_detection_delay_min, 0)} min</TD>
                </tr>))}</tbody>
            </table>
            <p className="text-[11px] text-ink-3">Positive = {d.positive_definition}. Detector {d.detector}, threshold {fmt(d.threshold, 3)} chosen on training incidents only. {d.note}</p>
            {d.splits.validation.type_diagnosis && (
              <p className="text-[12px]"><b>Incident-type diagnosis:</b> {pct(d.splits.validation.type_diagnosis.accuracy)} accuracy on validation
                ({pct(d.splits.validation.type_diagnosis.unknown_rate)} returned UNKNOWN; 20% = uniform chance). Absent from validation:
                {' '}{d.splits.validation.type_diagnosis.types_absent_from_this_split.join(', ')}. The UI therefore shows type as low-confidence.</p>
            )}
          </div>
        )}
      </Block>

      <Block kind="bottleneck" title="Recurring bottlenecks vs organizer flag" tag={<Tag kind="BENCHMARK">Benchmark</Tag>}>
        {d => (
          <div className="space-y-2 text-[12px]">
            <div className="grid grid-cols-4 gap-3 num">
              <div><div className="eyebrow">Precision</div>{fmt(d.precision, 2)}</div><div><div className="eyebrow">Recall</div>{fmt(d.recall, 2)}</div>
              <div><div className="eyebrow">F1</div>{fmt(d.f1, 2)}</div><div><div className="eyebrow">Ranking AUC</div>{fmt(d.ranking_auc, 3)}</div>
            </div>
            <p>Detected {d.n_detected} of {d.n_reference} reference segments at the pre-registered threshold z ≤ {d.threshold_z} (recovered: <span className="num">{d.segments_recovered.join(', ') || 'none'}</span>). {d.reference_in_top_16}/16 reference segments rank in the detector's top 16.</p>
            <p className="text-ink-3">{d.method}. The organizer flag is used only here, as a benchmark; the live UI shows only independently detected segments. Threshold sensitivity (label-informed, not used for selection) is in EVALUATION_REPORT.md.</p>
          </div>
        )}
      </Block>

      <Block kind="propagation" title="Propagation" tag={<Tag kind="VALIDATION">Indirect validation</Tag>}>
        {d => (
          <div className="space-y-2 text-[12px]">
            <div className="grid grid-cols-4 gap-3 num">
              <div><div className="eyebrow">Hit rate</div>{pct(d.neighbor_hit_rate_precision)}</div><div><div className="eyebrow">Recall</div>{pct(d.neighbor_recall)}</div>
              <div><div className="eyebrow">False propagation</div>{pct(d.false_propagation_rate)}</div><div><div className="eyebrow">Control base rate</div>{pct(d.control_base_rate)}</div>
            </div>
            <p className="text-ink-3">{d.evaluation}. Prediction issued {d.prediction_issued}; affected = {d.affected_definition}. {d.note}.</p>
          </div>
        )}
      </Block>

      <Block kind="simulation" title="Network simulation" tag={<Tag kind="SIMULATED">Simulation estimate</Tag>}>
        {d => (
          <div className="space-y-2 text-[12px]">
            <p><b>No ground truth</b> (intervention_reference.csv is withheld). Internal checks:</p>
            <ul className="list-disc pl-4">
              <li>All assigned routes respect turn restrictions: <b>{String(d.checks.all_assigned_paths_respect_turn_restrictions)}</b></li>
              <li>Validation incidents whose simulated capacity loss raises network delay: <b>{pct(d.checks.incident_increases_network_delay_share, 0)}</b></li>
              <li>Raw OD assignment vs observed flow correlation: <span className="num">{d.baseline_fit.map((b: any) => fmt(b.raw_assignment_flow_correlation_vs_observed, 2)).join(' / ')}</span> — low, hence the pivot-point method</li>
            </ul>
          </div>
        )}
      </Block>

      <div className="col-span-2">
        <Block kind="robustness" title="Robustness under injected noise" tag={<Tag kind="SYNTHETIC">Synthetic robustness test</Tag>}>
          {d => (
            <div>
              <table className="w-full text-[12px]">
                <thead><tr className="text-ink-3 border-b border-line"><TH>Corruption</TH><TH>Pipeline</TH><TH>Speed MAE +15</TH><TH>Flow MAE +15</TH><TH>Detection F1</TH>
                  <TH>FP slots/day</TH><TH>Incidents found</TH><TH>Data quality</TH><TH>Confidence on incidents</TH></tr></thead>
                <tbody>{Object.entries(d.runs as Record<string, any>).flatMap(([kind, row]) => (['sanitized', 'naive'] as const).filter(p => row[p]).map(p => {
                  const r = row[p]
                  return r.pipeline_failure ? (
                    <tr key={kind + p} className="border-b border-line/60"><TD className="font-sans">{kind}</TD><TD className="font-sans">{p}</TD>
                      <td colSpan={7} className="px-2 text-worse">pipeline failure: {r.pipeline_failure}</td></tr>
                  ) : (
                    <tr key={kind + p} className={`border-b border-line/60 ${p === 'naive' ? 'text-ink-3' : ''}`}>
                      <TD className="font-sans">{p === 'sanitized' ? kind : ''}</TD><TD className="font-sans">{p}</TD>
                      <TD>{fmt(r.forecast_mae_speed_15m, 3)}</TD><TD>{fmt(r.forecast_mae_flow_15m, 1)}</TD><TD>{fmt(r.detection_f1, 3)}</TD>
                      <TD>{fmt(r.false_positive_slots_per_day, 1)}</TD><TD>{r.incidents_detected}</TD><TD>{fmt(r.mean_data_quality, 3)}</TD>
                      <TD>{fmt(r.mean_confidence_on_incidents, 3)}</TD>
                    </tr>)
                }))}</tbody>
              </table>
              <p className="text-[11px] text-ink-3 mt-1">{d.label}. Corruption rate {pct(d.corruption_rate, 0)} on a copy of {d.slice.join(' → ')}; "naive" = same models without the sanitizer.</p>
            </div>
          )}
        </Block>
      </div>
    </div>
  )
}
