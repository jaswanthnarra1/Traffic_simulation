import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SegmentCard } from './components/SegmentCard'
import { tipHtml } from './components/TrafficMap'
import type { ForecastResponse, PropagationNeighbor } from './types'
import { speedTrend } from './utils/insight'
import { changeStyle, forecastStyle, propagationStyle, trafficStyle, vcStyle } from './utils/mapStyles'
import { buildAdjacency, corridor } from './utils/network'

// A tiny 2x2 grid of two-way roads: nodes A-B on the bottom, C-D on the top.
const seg = (id: string, s: string, t: string) => ({ properties: { segment_id: id, road_class: 'collector', lanes: 2, source_node: s, target_node: t } })
const FEATURES = [seg('AB', 'A', 'B'), seg('BA', 'B', 'A'), seg('BC', 'B', 'C'), seg('CB', 'C', 'B'), seg('CD', 'C', 'D'), seg('DC', 'D', 'C')] as never

describe('corridor', () => {
  const adj = buildAdjacency(FEATURES)
  it('pairs each segment with its reverse twin and never lists the twin as a neighbor', () => {
    expect(adj.twin.get('AB')).toBe('BA')
    expect(adj.down.get('AB')).toEqual(['BC'])
    expect(adj.up.get('BC')).toEqual(['AB'])
    expect(adj.down.get('AB')).not.toContain('BA')
  })
  it('frames the segment plus its immediate neighbors only', () => {
    expect(corridor('BC', adj).sort()).toEqual(['AB', 'BC', 'CD'])
  })
})

describe('map styling hierarchy', () => {
  it('leaves normal traffic to the faint base network', () => {
    expect(trafficStyle('NORMAL')).toBeNull()
    expect(vcStyle(0.4)).toBeNull()
    expect(changeStyle(0.004, 1)).toBeNull()                     // 0.4% change is noise
  })
  it('gives worse states more visual weight', () => {
    const w = (s: 'MODERATE' | 'HEAVY' | 'CRITICAL') => trafficStyle(s)!.weight!
    expect(w('CRITICAL')).toBeGreaterThan(w('HEAVY'))
    expect(w('HEAVY')).toBeGreaterThan(w('MODERATE'))
  })
  it('marks only newly-predicted impact as dashed in forecast mode', () => {
    expect(forecastStyle('HEAVY', true)!.dashed).toBe(true)
    expect(forecastStyle('HEAVY', false)!.dashed).toBeUndefined()
    expect(forecastStyle('NORMAL', true)).toBeNull()
  })
  it('animates only propagation paths, and hides minimal risk', () => {
    const n = (impact_level: string, hop = 1): PropagationNeighbor => ({ segment_id: 'X', hop, direction: 'upstream', predicted_speed_ratio_drop: 0.2,
      risk_score: 0.5, impact_level, estimated_time_to_impact_min: 5, reason: '' })
    expect(propagationStyle(n('MINIMAL'))).toBeNull()
    expect(propagationStyle(n('HIGH'))!.animate).toBe(true)
    expect(propagationStyle(n('HIGH'))!.label).toBe('5m')        // time to impact on first-hop neighbors
    expect(propagationStyle(n('HIGH', 2))!.label).toBeUndefined()
    expect(trafficStyle('CRITICAL')!.animate).toBeUndefined()
  })
})

describe('tooltips', () => {
  it('escape markup so a label can never inject HTML', () => {
    const html = tipHtml({ title: '<img src=x onerror=alert(1)>', rows: [['a&b', '"q"']] })
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
    expect(html).toContain('a&amp;b')
  })
})

describe('forecast trend', () => {
  const fc = (now: number, later: number) => ({ forecast: { speed: { history: [], forecast: [
    { horizon_min: 0, p50: now, p10: now, p90: now }, { horizon_min: 30, p50: later, p10: later, p90: later }] } } }) as unknown as ForecastResponse
  it('reads worsening / recovering / stable relative to free-flow speed', () => {
    expect(speedTrend(fc(40, 30), 60)!.trend).toBe('worsening')
    expect(speedTrend(fc(12, 24), 30)!.trend).toBe('recovering')
    expect(speedTrend(fc(40, 41), 60)!.trend).toBe('stable')
  })
  it('returns nothing rather than guessing when the forecast is missing', () => {
    expect(speedTrend(undefined, 60)).toBeNull()
  })
})

describe('segment card never invents a street name', () => {
  afterEach(() => vi.unstubAllGlobals())
  it('shows the simulation segment, node coordinates and a basemap pointer — no "Street:" line', async () => {
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } })
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.includes('/api/network')) return json({ geojson: { features: [] }, nodes: [
        { node_id: 'N1', x: 0, y: 0, lat: 17.4, lon: 78.5 }, { node_id: 'N2', x: 1, y: 0, lat: 17.42, lon: 78.52 }], bounds: [[17, 78], [18, 79]], disclaimer: '' })
      if (url.includes('/api/traffic/segment')) return json({ time: 't', segment: { segment_id: 'R0067', road_class: 'arterial', lanes: 2, source_node: 'N1', target_node: 'N2',
        free_flow_speed_kmh: 60 }, state: { state: 'CRITICAL', speed_kmh: 12.6 }, evidence: [],
        diagnosis: { cause: 'TRANSIENT_INCIDENT', reasons: [], detected: true, confidence: 0.94, confidence_label: 'HIGH' } })
      if (url.includes('/api/forecast/')) return json({ mode: 'LIVE', forecast: { speed: { history: [], forecast: [
        { horizon_min: 0, p50: 12, p10: 12, p90: 12 }, { horizon_min: 30, p50: 40, p10: 30, p90: 50 }] } } })
      return json({})
    }))
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SegmentCard seg="R0067" t="2026-01-16 13:30:00" /></QueryClientProvider>)
    expect(await screen.findByText('Simulation Segment')).toBeInTheDocument()
    expect(screen.getByText('Incident detected')).toBeInTheDocument()
    expect(screen.getByText(/17\.4100, 78\.5100/)).toBeInTheDocument()          // midpoint of the two dataset nodes
    expect(screen.getByText(/Recovering by \+30 min/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/street:|road name/i)
    expect(document.querySelector('[title*="not a mapped street"]')).not.toBeNull()
  })
})
