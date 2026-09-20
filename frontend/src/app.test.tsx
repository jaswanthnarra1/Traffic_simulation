import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { forecastRows } from './components/ForecastChart'
import { ErrorState, KpiTable } from './components/ui'
import { ApiError } from './services/api'
import type { ForecastResponse } from './types'
import { clampTime, fmt, shiftTime, signed, vcColor } from './utils/format'

describe('simulation clock', () => {
  it('steps across midnight on the 5-minute grid', () => {
    expect(shiftTime('2026-01-16 23:55:00', 5)).toBe('2026-01-17 00:00:00')
    expect(shiftTime('2026-01-16 00:00:00', -60)).toBe('2026-01-15 23:00:00')
  })
  it('clamps to the data range', () => {
    expect(clampTime('2025-12-31 00:00:00', '2026-01-01 00:00:00', '2026-01-19 23:55:00')).toBe('2026-01-01 00:00:00')
  })
})

describe('formatting never invents values', () => {
  it('renders missing numbers as a dash', () => {
    expect(fmt(null)).toBe('—')
    expect(fmt(Number.NaN)).toBe('—')
    expect(signed(undefined)).toBe('—')
    expect(signed(2.5, 1, '%')).toBe('+2.5%')
  })
  it('colours v/c by the same scale on both simulation maps', () => {
    expect(vcColor(0.3)).not.toBe(vcColor(1.2))
  })
})

const fc: ForecastResponse = {
  time: '2026-01-16 13:30:00', segment_id: 'R0360', mode: 'LIVE', uncertainty: '',
  forecast: {
    speed: { history: [{ time: 'a', value: 30 }, { time: 'b', value: 12 }],
      forecast: [{ horizon_min: 0, p50: 12, p10: 12, p90: 12 }, { horizon_min: 15, p50: 18, p10: 15, p90: 21 }] },
    flow: { history: [], forecast: [] }, congestion: { history: [], forecast: [] },
  },
}

describe('forecast chart data', () => {
  it('joins history and forecast at "now" and never adds future actuals in live mode', () => {
    const rows = forecastRows(fc, 'speed')
    expect(rows.map(r => r.x)).toEqual([-5, 0, 15])
    expect(rows.find(r => r.x === 0)).toMatchObject({ actual: 12, p50: 12 })
    expect(rows.some(r => r.future !== undefined)).toBe(false)
  })
  it('overlays recorded future values only when the response is a backtest', () => {
    const rows = forecastRows({ ...fc, mode: 'BACKTEST', actual_future: { speed: [{ horizon_min: 15, value: 20 }], flow: [], congestion: [] } }, 'speed')
    expect(rows.find(r => r.x === 15)?.future).toBe(20)
  })
})

describe('components', () => {
  it('KPI table shows absolute and percent change', () => {
    render(<KpiTable kpis={{ total_delay_veh_h: { baseline: 100, counterfactual: 90, abs_change: -10, pct_change: -10 } }} />)
    expect(screen.getByText('Total delay (veh·h)')).toBeInTheDocument()
    expect(screen.getByText('-10.00%')).toBeInTheDocument()
  })
  it('backend failure renders "No data available", not a fabricated value', () => {
    render(<ErrorState error={new ApiError(0, 'x')} what="traffic" />)
    expect(screen.getByRole('alert')).toHaveTextContent('No traffic available')
    expect(screen.getByRole('alert')).toHaveTextContent('Backend unreachable')
  })
})
