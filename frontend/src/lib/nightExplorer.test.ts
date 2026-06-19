import { describe, expect, it } from 'vitest'

import type { Equipment, SessionSummary } from '../api/client'
import {
  buildMonthGrid,
  buildNightIndex,
  findImportGaps,
  matchesFilter,
  metricColor,
  metricTileValue,
} from './nightExplorer'

function session(overrides: Partial<SessionSummary> & Pick<SessionSummary, 'folder_date'>): SessionSummary {
  return {
    id: `id-${overrides.folder_date}`,
    session_id: `s-${overrides.folder_date}`,
    block_index: 0,
    start_datetime: `${overrides.folder_date}T22:00:00Z`,
    end_datetime: null,
    duration_seconds: 28800,
    duration_hours: 8,
    ahi: 4,
    central_apnea_count: 0,
    obstructive_apnea_count: 0,
    hypopnea_count: 0,
    apnea_count: 0,
    arousal_count: 0,
    total_ahi_events: 10,
    avg_pressure: 10,
    p95_pressure: 12,
    avg_leak: 0.1,
    leak_unit: 'L/s',
    has_spo2: false,
    machine_tz: 'UTC',
    ...overrides,
  }
}

describe('buildNightIndex', () => {
  it('collapses multi-block nights to the longest block', () => {
    const cells = buildNightIndex([
      session({ folder_date: '2024-01-01', duration_seconds: 3600, duration_hours: 1, ahi: 9 }),
      session({ folder_date: '2024-01-01', duration_seconds: 28800, duration_hours: 8, ahi: 2 }),
    ])
    expect(cells.get('2024-01-01')?.ahi).toBe(2)
    expect(cells.get('2024-01-01')?.hours).toBe(8)
  })

  it('flags short usage, high leak, high AHI, and missing data', () => {
    const cells = buildNightIndex([
      session({ folder_date: '2024-01-01', duration_hours: 3 }),
      session({ folder_date: '2024-01-02', avg_leak: 0.5 }), // 30 L/min
      session({ folder_date: '2024-01-03', ahi: 22 }),
      session({ folder_date: '2024-01-04', avg_pressure: null, avg_leak: null }),
    ])
    expect(cells.get('2024-01-01')?.shortUsage).toBe(true)
    expect(cells.get('2024-01-02')?.highLeak).toBe(true)
    expect(cells.get('2024-01-03')?.highAhi).toBe(true)
    const summaryOnly = cells.get('2024-01-04')
    expect(summaryOnly?.missingData).toBe(true)
    expect(summaryOnly?.recorded).toBe(false)
    expect(summaryOnly?.needsReview).toBe(true)
  })

  it('attaches equipment changes to their start date', () => {
    const equipment: Equipment[] = [
      {
        id: 'e1',
        equipment_type: 'cushion',
        start_date: '2024-01-02',
        replacement_days: 30,
        mask_category: null,
        brand: null,
        model: null,
        notes: null,
        days_in_use: 1,
        is_default: true,
        created_at: '',
        updated_at: '',
      },
    ]
    const cells = buildNightIndex([session({ folder_date: '2024-01-02' })], equipment)
    expect(cells.get('2024-01-02')?.equipmentChanges).toHaveLength(1)
    expect(cells.get('2024-01-02')?.equipmentChanges[0].label).toBe('Cushion')
  })
})

describe('matchesFilter', () => {
  const cells = buildNightIndex([
    session({ folder_date: '2024-01-01', duration_hours: 3 }),
    session({ folder_date: '2024-01-02', ahi: 1 }),
  ])
  const short = cells.get('2024-01-01')!
  const normal = cells.get('2024-01-02')!

  it('passes everything for "all"', () => {
    expect(matchesFilter(short, 'all')).toBe(true)
    expect(matchesFilter(normal, 'all')).toBe(true)
  })

  it('narrows to short-usage nights', () => {
    expect(matchesFilter(short, 'short')).toBe(true)
    expect(matchesFilter(normal, 'short')).toBe(false)
  })

  it('narrows to needs-review nights', () => {
    expect(matchesFilter(short, 'review')).toBe(true)
    expect(matchesFilter(normal, 'review')).toBe(false)
  })
})

describe('findImportGaps', () => {
  it('detects a contiguous gap between recorded nights, most recent first', () => {
    const cells = buildNightIndex([
      session({ folder_date: '2024-01-01' }),
      session({ folder_date: '2024-01-05' }),
      session({ folder_date: '2024-01-06' }),
      session({ folder_date: '2024-01-10' }),
    ])
    const gaps = findImportGaps(cells)
    expect(gaps).toEqual([
      { start: '2024-01-07', end: '2024-01-09', nights: 3 },
      { start: '2024-01-02', end: '2024-01-04', nights: 3 },
    ])
  })

  it('returns no gaps for a single night', () => {
    expect(findImportGaps(buildNightIndex([session({ folder_date: '2024-01-01' })]))).toEqual([])
  })
})

describe('metric helpers', () => {
  const cell = buildNightIndex([
    session({ folder_date: '2024-01-01', ahi: 2, duration_hours: 8, avg_leak: 0.1, total_ahi_events: 20 }),
  ]).get('2024-01-01')!

  it('formats per-metric tile values', () => {
    expect(metricTileValue(cell, 'ahi')).toBe('2.0')
    expect(metricTileValue(cell, 'usage')).toBe('8.0h')
    expect(metricTileValue(cell, 'leak')).toBe('6')
    expect(metricTileValue(cell, 'events')).toBe('20')
  })

  it('colors a normal night green across metrics', () => {
    expect(metricColor(cell, 'ahi')).toBe('#6AA136')
    expect(metricColor(cell, 'usage')).toBe('#6AA136')
  })
})

describe('buildMonthGrid', () => {
  it('pads to whole weeks with leading blanks', () => {
    // Jan 2024 starts on a Monday → one leading blank for Sunday.
    const grid = buildMonthGrid(2024, 0)
    expect(grid.length % 7).toBe(0)
    expect(grid[0]).toBeNull()
    expect(grid[1]?.getDate()).toBe(1)
  })
})
