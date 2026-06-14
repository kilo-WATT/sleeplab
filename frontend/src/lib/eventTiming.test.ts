import { describe, expect, it } from 'vitest'

import { eventInterval } from './eventTiming'

describe('eventInterval', () => {
  it('extends parser respiratory events forward from their onset', () => {
    const onset = new Date('2026-06-02T04:05:00Z').getTime()
    expect(eventInterval({
      id: 1,
      event_type: 'Obstructive Apnea',
      onset_seconds: 300,
      duration_seconds: 17,
      event_datetime: '2026-06-02T04:05:00Z',
    })).toEqual({ start: onset, end: onset + 17_000 })
  })

  it('keeps derived large-leak spans anchored at their end', () => {
    const end = new Date('2026-06-02T04:05:00Z').getTime()
    expect(eventInterval({
      id: 2,
      event_type: 'Large Leak',
      onset_seconds: 300,
      duration_seconds: 120,
      event_datetime: '2026-06-02T04:05:00Z',
    })).toEqual({ start: end - 120_000, end })
  })
})
