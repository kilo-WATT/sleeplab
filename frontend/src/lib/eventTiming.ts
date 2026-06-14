import type { EventRecord } from '../api/client'

export interface EventInterval {
  start: number
  end: number
}

export function eventInterval(event: EventRecord, fallbackStart = 0): EventInterval {
  const parsedTimestamp = new Date(event.event_datetime).getTime()
  const timestamp = Number.isFinite(parsedTimestamp)
    ? parsedTimestamp
    : fallbackStart + event.onset_seconds * 1000
  const durationMs = Math.max((event.duration_seconds ?? 2) * 1000, 2000)

  // Device-scored respiratory events are onset-anchored. Derived Large Leak
  // spans retain the legacy end-anchor used by both importer paths.
  return event.event_type === 'Large Leak'
    ? { start: timestamp - durationMs, end: timestamp }
    : { start: timestamp, end: timestamp + durationMs }
}
