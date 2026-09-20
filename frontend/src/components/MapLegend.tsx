import { STATE_HEX } from '../utils/format'
import { BETTER, RISK, WORSE } from '../utils/mapStyles'
import { Legend, LegendRow, NETWORK_GREY } from './TrafficMap'

/** One compact legend for every map: TRAFFIC · FLOWSENSE · (SIMULATION). */
export function MapLegend({ simulation = false }: { simulation?: boolean }) {
  const sections = [
    { title: 'Traffic', rows: <>
      <LegendRow color={NETWORK_GREY} weight={1.5} label="Normal" />
      <LegendRow color={STATE_HEX.MODERATE} weight={3} label="Moderate" />
      <LegendRow color={STATE_HEX.HEAVY} weight={4.5} label="Heavy" />
      <LegendRow color={STATE_HEX.CRITICAL} weight={6} label="Critical" /></> },
    { title: 'FlowSense', rows: <>
      <LegendRow color="" dot="CRITICAL" label="Incident" />
      <LegendRow color={STATE_HEX.HEAVY} weight={4} dashed label="Predicted impact" />
      <LegendRow color={RISK} weight={3} dashed label="Propagation risk" />
      <LegendRow color="#16181d" weight={5} label="Selected segment" /></> },
  ]
  if (simulation) sections.push({ title: 'Simulation', rows: <>
    <LegendRow color={BETTER} weight={4.5} label="Improved" />
    <LegendRow color={WORSE} weight={4.5} label="Worsened" /></> })
  return <Legend sections={sections} />
}
