import { describe, expect, it } from 'vitest'

import type { SessionSummary } from '../api/client'
import { computeOverviewMetrics, pluralize } from './overviewMetrics'

const NOW = new Date(2026, 5, 14) // 2026-06-14, local

function session(overrides: Partial<SessionSummary> & { folder_date: string }): SessionSummary {
  return {
    id: overrides.folder_date,
    session_id: overrides.folder_date,
    block_index: 0,
    start_datetime: `${overrides.folder_date}T22:00:00`,
    end_datetime: null,
    duration_seconds: Math.round((overrides.duration_hours ?? 7) * 3600),
    duration_hours: overrides.duration_hours ?? 7,
    ahi: overrides.ahi ?? null,
    central_apnea_count: 0,
    obstructive_apnea_count: 0,
    hypopnea_count: 0,
    apnea_count: 0,
    arousal_count: 0,
    total_ahi_events: overrides.total_ahi_events ?? 0,
    avg_pressure: overrides.avg_pressure ?? null,
    p95_pressure: null,
    avg_leak: overrides.avg_leak ?? null,
    leak_unit: overrides.leak_unit ?? 'L/min',
    has_spo2: false,
    machine_tz: 'UTC',
    ...overrides,
  }
}

describe('computeOverviewMetrics', () => {
  it('computes AHI and compliance over a single explicit window', () => {
    const sessions: SessionSummary[] = [
      session({ folder_date: '2026-06-13', ahi: 0.6, duration_hours: 7.0 }),
      session({ folder_date: '2026-06-12', ahi: 0.8, duration_hours: 6.7 }),
      session({ folder_date: '2026-06-11', ahi: 1.0, duration_hours: 3.0 }), // non-compliant
    ]

    const m = computeOverviewMetrics(sessions, { now: NOW, windowNights: 30 })

    expect(m.recordedNights).toBe(3)
    expect(m.compliantNights).toBe(2)
    expect(m.compliancePct).toBeCloseTo((2 / 30) * 100, 5)
    expect(m.avgAhi).toBeCloseTo((0.6 + 0.8 + 1.0) / 3, 5)
    expect(m.through).toBe('2026-06-13')
  })

  it('excludes nights outside the window', () => {
    const sessions: SessionSummary[] = [
      session({ folder_date: '2026-06-13', ahi: 1, duration_hours: 7 }),
      session({ folder_date: '2026-03-01', ahi: 9, duration_hours: 7 }), // far outside
    ]

    const m = computeOverviewMetrics(sessions, { now: NOW, windowNights: 30 })

    expect(m.recordedNights).toBe(1)
    expect(m.avgAhi).toBeCloseTo(1, 5)
  })

  it('collapses multi-block nights to the longest block', () => {
    const sessions: SessionSummary[] = [
      session({ folder_date: '2026-06-13', ahi: 5, duration_hours: 2, id: 'short' }),
      session({ folder_date: '2026-06-13', ahi: 1, duration_hours: 7, id: 'long' }),
    ]

    const m = computeOverviewMetrics(sessions, { now: NOW, windowNights: 30 })

    expect(m.recordedNights).toBe(1)
    expect(m.avgAhi).toBeCloseTo(1, 5)
    expect(m.compliantNights).toBe(1)
  })

  it('flags nights by severity, threatening adherence above a mild high-AHI night', () => {
    const sessions: SessionSummary[] = [
      session({ folder_date: '2026-06-13', ahi: 0.6, duration_hours: 0.0 }), // no data
      session({ folder_date: '2026-06-12', ahi: 0.9, duration_hours: 1.8 }), // below 4h
      session({ folder_date: '2026-06-11', ahi: 2.4, duration_hours: 7.0, avg_leak: 3 }), // highest AHI
      session({ folder_date: '2026-06-10', ahi: 0.5, duration_hours: 7.0, avg_leak: 16 }), // highest leak
      session({ folder_date: '2026-06-09', ahi: 0.7, duration_hours: 3.4 }), // just under 4h
    ]

    const m = computeOverviewMetrics(sessions, { now: NOW, windowNights: 30 })
    const order = m.flaggedNights.map((n) => n.label === 'Short session' ? `short-${n.date.slice(-2)}` : n.label)

    expect(order).toEqual(['short-13', 'short-12', 'Highest leak', 'Highest AHI', 'short-09'])
    expect(m.flaggedNights[0].tone).toBe('high')
    expect(m.flaggedNights[2].tone).toBe('warn') // leak
    expect(m.flaggedNights[3].tone).toBe('info') // mild AHI
    expect(m.flaggedNights[0].detail).toContain('no usable data')
    expect(m.flaggedNights[1].detail).toContain('below 4h compliance')
    expect(m.flaggedNights[4].detail).toContain('just under 4h')
  })

  it('reports an improving AHI delta as a green "down" change', () => {
    const sessions: SessionSummary[] = [
      // current window (last 30 nights): low AHI
      session({ folder_date: '2026-06-13', ahi: 0.5, duration_hours: 7 }),
      session({ folder_date: '2026-06-10', ahi: 0.7, duration_hours: 7 }),
      // prior window (31–60 nights ago): higher AHI
      session({ folder_date: '2026-05-10', ahi: 2.0, duration_hours: 7 }),
      session({ folder_date: '2026-05-05', ahi: 2.4, duration_hours: 7 }),
    ]

    const m = computeOverviewMetrics(sessions, { now: NOW, windowNights: 30 })

    expect(m.ahiDelta.direction).toBe('down')
    expect(m.ahiDelta.tone).toBe('good')
    expect(m.ahiDelta.value).toBeLessThan(0)
  })

  it('returns nulls for an empty window', () => {
    const m = computeOverviewMetrics([], { now: NOW, windowNights: 30 })
    expect(m.avgAhi).toBeNull()
    expect(m.through).toBeNull()
    expect(m.compliancePct).toBe(0)
    expect(m.flaggedNights).toEqual([])
  })
})

describe('pluralize', () => {
  it('keeps the singular form for exactly one', () => {
    expect(pluralize(1, 'night')).toBe('night')
    expect(pluralize(0, 'night')).toBe('nights')
    expect(pluralize(2, 'night')).toBe('nights')
  })

  it('honors an explicit plural', () => {
    expect(pluralize(2, 'is', 'are')).toBe('are')
  })
})
