import { describe, expect, it } from 'vitest'

import type { OverviewDailyStat } from '../api/client'
import { deriveInsightChips, deriveNightNotes } from './trendInsights'

/** Build an OverviewDailyStat with all-null clinical fields, overriding only what a test needs. */
function night(overrides: Partial<OverviewDailyStat> = {}): OverviewDailyStat {
  return {
    folder_date: '2024-01-01',
    session_id: 'sess-1',
    ahi: null,
    central_apnea_index: null,
    obstructive_apnea_index: null,
    hypopnea_index: null,
    apnea_index: null,
    arousal_index: null,
    usage_hours: 7,
    session_start_hour: null,
    session_end_hour: null,
    avg_pressure: null,
    p95_pressure: null,
    avg_leak: null,
    leak_unit: 'L/min',
    large_leak_minutes: null,
    avg_flow_lim: null,
    avg_tidal_vol: null,
    avg_min_vent: null,
    avg_resp_rate: null,
    min_spo2: null,
    avg_spo2: null,
    avg_pulse: null,
    equipment_age_days: null,
    ...overrides,
  }
}

describe('deriveInsightChips', () => {
  it('marks AHI controlled when the average is below 5', () => {
    const chips = deriveInsightChips([night({ avg_leak: 10 })], 2.1)
    expect(chips).toContainEqual({ label: 'AHI controlled', tone: 'good' })
  })

  it('flags an elevated AHI', () => {
    const chips = deriveInsightChips([night()], 18)
    expect(chips).toContainEqual({ label: 'AHI elevated', tone: 'alert' })
  })

  it('flags elevated central apnea', () => {
    const chips = deriveInsightChips([night({ central_apnea_index: 6 })], 3)
    expect(chips).toContainEqual({ label: 'Central apnea elevated', tone: 'alert' })
  })

  it('reports leaks as intermittent when there is large-leak time but low average', () => {
    const chips = deriveInsightChips([night({ avg_leak: 8, large_leak_minutes: 12 })], 3)
    expect(chips).toContainEqual({ label: 'Leaks intermittent', tone: 'watch' })
  })

  it('reports SpO2 as unavailable when no oximetry exists', () => {
    const chips = deriveInsightChips([night({ avg_leak: 5 })], 3)
    expect(chips).toContainEqual({ label: 'SpO₂ unavailable', tone: 'neutral' })
  })

  it('never returns more than four chips', () => {
    const chips = deriveInsightChips(
      [night({ avg_leak: 40, central_apnea_index: 9, min_spo2: 80 })],
      20,
    )
    expect(chips.length).toBeLessThanOrEqual(4)
  })
})

describe('deriveNightNotes', () => {
  it('flags a short session', () => {
    expect(deriveNightNotes(night({ usage_hours: 2.5 }))).toContainEqual({ label: 'Short session', tone: 'watch' })
  })

  it('flags a high leak night', () => {
    expect(deriveNightNotes(night({ avg_leak: 30 }))).toContainEqual({ label: 'High leak', tone: 'alert' })
  })

  it('flags nights where central events dominate AHI', () => {
    expect(deriveNightNotes(night({ ahi: 6, central_apnea_index: 4 }))).toContainEqual({ label: 'More CA', tone: 'watch' })
  })

  it('returns no notes for a clean night', () => {
    expect(deriveNightNotes(night({ ahi: 1, central_apnea_index: 0.1, avg_leak: 12, usage_hours: 7 }))).toEqual([])
  })
})
