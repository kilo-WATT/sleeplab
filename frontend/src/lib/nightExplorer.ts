import type { Equipment, EquipmentType, SessionSummary } from '../api/client'
import { leakToLpm } from './units'

/**
 * Night Explorer view-model + pure helpers for the Calendar page.
 *
 * Everything here is derivable from the bulk `GET /sessions/` payload (plus the
 * one-shot `GET /equipment/` list) so the calendar can render markers, severity
 * colors, filters, and gaps without a per-night detail fetch. The selected-night
 * inspector layers richer detail (notes, waveforms, mask context) on top via a
 * lazy `GET /sessions/{date}` — that lives in the page, not here.
 */

/** Metric a calendar tile is colored/labelled by. */
export type NightMetric = 'ahi' | 'usage' | 'leak' | 'events'

/** Quick filter applied to the calendar + recent-nights list. */
export type NightFilter = 'all' | 'review' | 'short' | 'leak' | 'ahi' | 'equipment'

/** A single equipment swap that started on a given night. */
export interface EquipmentChange {
  equipment_type: EquipmentType
  label: string
}

/** One recorded (or summary-only) night, reconciled to a single primary block. */
export interface NightCell {
  date: string
  ahi: number | null
  hours: number
  leak: number | null
  events: number | null
  pressure: number | null
  /**
   * True when the night has a detailed recording. False for summary-only nights
   * (machine ran and logged usage, but nothing was recorded — e.g. SD card left
   * out). Mirrors the heuristic used by the Dashboard calendar heatmap.
   */
  recorded: boolean
  equipmentChanges: EquipmentChange[]
  /** Usage below the 4h adherence floor. */
  shortUsage: boolean
  /** Average leak in the elevated/high band. */
  highLeak: boolean
  /** AHI in the moderate range or worse. */
  highAhi: boolean
  /** Used but not recorded — detailed data missing. */
  missingData: boolean
  /** Any reason this night is worth a closer look. */
  needsReview: boolean
}

export const HIGH_LEAK_LPM = 24
export const HIGH_AHI = 15
export const SHORT_USAGE_HOURS = 4

const EQUIPMENT_LABELS: Record<EquipmentType, string> = {
  cushion: 'Cushion',
  headgear: 'Headgear',
  tubing: 'Tubing',
  humidifier_chamber: 'Humidifier chamber',
  filter: 'Filter',
}

/** AHI severity color (kept identical to the Dashboard calendar legend). */
export function ahiColor(ahi: number | null): string {
  if (ahi === null) return 'var(--calendar-empty)'
  if (ahi < 5) return '#6AA136'
  if (ahi < 15) return '#C9B715'
  if (ahi < 30) return '#E9784B'
  return '#8E3D40'
}

export function ahiLabel(ahi: number | null): string {
  if (ahi === null) return 'No data'
  if (ahi < 5) return 'Normal'
  if (ahi < 15) return 'Mild'
  if (ahi < 30) return 'Moderate'
  return 'Severe'
}

function usageColor(hours: number | null): string {
  if (hours === null) return 'var(--calendar-empty)'
  if (hours >= 7) return '#6AA136'
  if (hours >= 4) return '#C9B715'
  return '#E9784B'
}

function leakColor(leak: number | null): string {
  if (leak === null) return 'var(--calendar-empty)'
  if (leak < 10) return '#6AA136'
  if (leak < HIGH_LEAK_LPM) return '#C9B715'
  return '#E9784B'
}

function eventsColor(events: number | null): string {
  if (events === null) return 'var(--calendar-empty)'
  if (events < 30) return '#6AA136'
  if (events < 90) return '#C9B715'
  if (events < 180) return '#E9784B'
  return '#8E3D40'
}

/** Color for a cell under the currently selected metric. */
export function metricColor(cell: NightCell, metric: NightMetric): string {
  switch (metric) {
    case 'usage':
      return usageColor(cell.hours)
    case 'leak':
      return leakColor(cell.leak)
    case 'events':
      return eventsColor(cell.recorded ? cell.events : null)
    default:
      return ahiColor(cell.ahi)
  }
}

/** Short value rendered inside a tile when space allows ('' when unavailable). */
export function metricTileValue(cell: NightCell, metric: NightMetric): string {
  switch (metric) {
    case 'usage':
      return `${cell.hours.toFixed(1)}h`
    case 'leak':
      return cell.leak == null ? '' : cell.leak.toFixed(0)
    case 'events':
      return cell.recorded && cell.events != null ? String(cell.events) : ''
    default:
      return cell.ahi == null ? '' : cell.ahi.toFixed(1)
  }
}

/** Longer, human-readable value for tooltips / the inspector. */
export function metricLongValue(cell: NightCell, metric: NightMetric): string {
  switch (metric) {
    case 'usage':
      return `${cell.hours.toFixed(1)}h usage`
    case 'leak':
      return cell.leak == null ? 'Leak not recorded' : `${cell.leak.toFixed(1)} L/min leak`
    case 'events':
      return cell.recorded && cell.events != null ? `${cell.events} events` : 'Events not recorded'
    default:
      return cell.ahi == null ? 'AHI not recorded' : `AHI ${cell.ahi.toFixed(1)} (${ahiLabel(cell.ahi)})`
  }
}

export const METRIC_LABELS: Record<NightMetric, string> = {
  ahi: 'AHI',
  usage: 'Usage',
  leak: 'Leak',
  events: 'Events',
}

/** Legend swatches for the active metric. */
export function metricLegend(metric: NightMetric): [string, string][] {
  switch (metric) {
    case 'usage':
      return [
        ['#6AA136', '≥7h'],
        ['#C9B715', '4–7h'],
        ['#E9784B', '<4h'],
        ['var(--calendar-empty)', 'No data'],
      ]
    case 'leak':
      return [
        ['#6AA136', '<10 L/min'],
        ['#C9B715', '10–24'],
        ['#E9784B', '24+'],
        ['var(--calendar-empty)', 'No data'],
      ]
    case 'events':
      return [
        ['#6AA136', '<30'],
        ['#C9B715', '30–90'],
        ['#E9784B', '90–180'],
        ['#8E3D40', '180+'],
        ['var(--calendar-empty)', 'No data'],
      ]
    default:
      return [
        ['#6AA136', '<5 Normal'],
        ['#C9B715', '5–15 Mild'],
        ['#E9784B', '15–30 Moderate'],
        ['#8E3D40', '30+ Severe'],
        ['var(--calendar-empty)', 'No data'],
      ]
  }
}

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

export function toIso(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

export function parseIso(iso: string): Date {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(year, month - 1, day)
}

/**
 * Build the night index from the raw session list and (optionally) the user's
 * equipment list. Multi-block nights collapse to their single longest block,
 * matching the Dashboard/overview reconciliation.
 */
export function buildNightIndex(
  sessions: SessionSummary[],
  equipment: Equipment[] = [],
): Map<string, NightCell> {
  const longest = new Map<string, SessionSummary>()
  for (const session of sessions) {
    const existing = longest.get(session.folder_date)
    if (!existing || session.duration_seconds > existing.duration_seconds) {
      longest.set(session.folder_date, session)
    }
  }

  const changesByDate = new Map<string, EquipmentChange[]>()
  for (const item of equipment) {
    const list = changesByDate.get(item.start_date) ?? []
    list.push({ equipment_type: item.equipment_type, label: EQUIPMENT_LABELS[item.equipment_type] })
    changesByDate.set(item.start_date, list)
  }

  const cells = new Map<string, NightCell>()
  for (const [date, session] of longest) {
    const leak = leakToLpm(session.avg_leak, session.leak_unit)
    const recorded = session.avg_pressure != null || leak != null
    const shortUsage = session.duration_hours < SHORT_USAGE_HOURS
    const highLeak = leak != null && leak >= HIGH_LEAK_LPM
    const highAhi = session.ahi != null && session.ahi >= HIGH_AHI
    const missingData = !recorded
    cells.set(date, {
      date,
      ahi: session.ahi,
      hours: session.duration_hours,
      leak,
      events: session.total_ahi_events,
      pressure: session.avg_pressure,
      recorded,
      equipmentChanges: changesByDate.get(date) ?? [],
      shortUsage,
      highLeak,
      highAhi,
      missingData,
      needsReview: shortUsage || highLeak || highAhi || missingData,
    })
  }

  return cells
}

/** Whether a night passes the active quick filter. */
export function matchesFilter(cell: NightCell, filter: NightFilter): boolean {
  switch (filter) {
    case 'review':
      return cell.needsReview
    case 'short':
      return cell.shortUsage
    case 'leak':
      return cell.highLeak
    case 'ahi':
      return cell.highAhi
    case 'equipment':
      return cell.equipmentChanges.length > 0
    default:
      return true
  }
}

/** A contiguous run of calendar days with no recorded night, inside the data range. */
export interface ImportGap {
  start: string
  end: string
  nights: number
}

/**
 * Find calendar gaps (missing nights) between the first and last recorded night.
 * Returns most-recent-first. Useful for spotting unimported stretches.
 */
export function findImportGaps(cells: Map<string, NightCell>): ImportGap[] {
  const dates = [...cells.keys()].sort()
  if (dates.length < 2) return []

  const present = new Set(dates)
  const gaps: ImportGap[] = []
  const cursor = parseIso(dates[0])
  const last = parseIso(dates[dates.length - 1])

  let runStart: Date | null = null
  while (cursor <= last) {
    const iso = toIso(cursor)
    if (present.has(iso)) {
      if (runStart) {
        const end = new Date(cursor)
        end.setDate(end.getDate() - 1)
        gaps.push({
          start: toIso(runStart),
          end: toIso(end),
          nights: Math.round((end.getTime() - runStart.getTime()) / 86_400_000) + 1,
        })
        runStart = null
      }
    } else if (!runStart) {
      runStart = new Date(cursor)
    }
    cursor.setDate(cursor.getDate() + 1)
  }

  return gaps.reverse()
}

/** Six-week-padded day grid for a month; null entries are leading/trailing blanks. */
export function buildMonthGrid(year: number, month: number): (Date | null)[] {
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const firstDow = new Date(year, month, 1).getDay()
  const days: (Date | null)[] = Array(firstDow).fill(null)
  for (let day = 1; day <= daysInMonth; day += 1) {
    days.push(new Date(year, month, day))
  }
  // Trim to whole weeks (multiples of 7), dropping only fully-empty trailing rows.
  while (days.length % 7 !== 0) days.push(null)
  return days
}
