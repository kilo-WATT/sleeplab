import type { SessionSummary } from '../api/client'
import { leakToLpm } from './units'

/**
 * The single, explicit window the Overview headline metrics are computed over.
 *
 * This mirrors the backend AI-summary window (`/stats/ai-summary?days=30`,
 * `start_date = today - 29`, denominator = `days`) so that the hero cards, the
 * AI Insights denominator, and the Nights-to-Review list all describe the same
 * span of nights and read identical values.
 */
export const OVERVIEW_WINDOW_NIGHTS = 30

/** A directional change for a headline metric versus the prior equal-length window. */
export interface MetricDelta {
  /** current - prior in the metric's native units, or null when either side is missing. */
  value: number | null
  /** Whether the raw value rose, fell, or held steady. */
  direction: 'up' | 'down' | 'flat'
  /** Whether the change is an improvement, a regression, or neutral for this metric. */
  tone: 'good' | 'bad' | 'neutral'
}

/** A night surfaced in the Nights-to-Review list, with a numeric severity for ordering. */
export interface FlaggedNight {
  date: string
  label: string
  /** One-line, human-readable reason the night is flagged. */
  detail: string
  /** 0–100; higher means more clinically/▁adherence severe. Drives ordering and dot color. */
  severity: number
  /** Coarse bucket derived from `severity`, used for the row dot color. */
  tone: 'high' | 'warn' | 'info'
}

/** Fully reconciled Overview view-model. Every headline figure derives from here. */
export interface OverviewMetrics {
  windowNights: number
  /** Latest recorded night inside the window (ISO date), or null when the window is empty. */
  through: string | null
  /** Nights inside the window that have usable data. */
  recordedNights: number
  // AHI — lower is better.
  avgAhi: number | null
  ahiSeries: number[]
  ahiDelta: MetricDelta
  // Compliance — higher is better. compliantNights / windowNights.
  compliantNights: number
  compliancePct: number
  complianceDelta: MetricDelta
  // Pressure — directionally neutral.
  avgPressure: number | null
  pressureSeries: number[]
  pressureDelta: MetricDelta
  // Secondary strip.
  respEventsPerNight: number | null
  avgUsageHours: number | null
  avgLeakLpm: number | null
  // Review.
  flaggedNights: FlaggedNight[]
}

interface PrimaryNight {
  date: string
  ahi: number | null
  hours: number
  pressure: number | null
  leak: number | null
  events: number
}

function pad2(value: number): string {
  return String(value).padStart(2, '0')
}

function isoOf(date: Date): string {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`
}

function shiftDays(date: Date, days: number): Date {
  const next = new Date(date)
  next.setDate(next.getDate() + days)
  return next
}

function mean(values: number[]): number | null {
  if (values.length === 0) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

/** Collapse multi-block nights to the single longest block, keyed by recorded date. */
function primaryNightsByDate(sessions: SessionSummary[]): Map<string, PrimaryNight> {
  const longest = new Map<string, SessionSummary>()
  for (const session of sessions) {
    const existing = longest.get(session.folder_date)
    if (!existing || session.duration_seconds > existing.duration_seconds) {
      longest.set(session.folder_date, session)
    }
  }

  const nights = new Map<string, PrimaryNight>()
  for (const [date, session] of longest) {
    nights.set(date, {
      date,
      ahi: session.ahi,
      hours: session.duration_hours,
      pressure: session.avg_pressure,
      leak: leakToLpm(session.avg_leak, session.leak_unit),
      events: session.total_ahi_events,
    })
  }
  return nights
}

function avgAhiOf(nights: PrimaryNight[]): number | null {
  return mean(nights.filter((n) => n.ahi != null).map((n) => n.ahi as number))
}

function compliancePctOf(nights: PrimaryNight[], windowNights: number): number {
  if (windowNights <= 0) return 0
  const compliant = nights.filter((n) => n.hours >= 4).length
  return (compliant / windowNights) * 100
}

function avgPressureOf(nights: PrimaryNight[]): number | null {
  return mean(nights.filter((n) => n.pressure != null).map((n) => n.pressure as number))
}

function delta(
  current: number | null,
  prior: number | null,
  betterWhen: 'lower' | 'higher' | 'neutral',
  epsilon: number,
): MetricDelta {
  if (current == null || prior == null) {
    return { value: null, direction: 'flat', tone: 'neutral' }
  }
  const value = current - prior
  const direction: MetricDelta['direction'] = value > epsilon ? 'up' : value < -epsilon ? 'down' : 'flat'
  let tone: MetricDelta['tone'] = 'neutral'
  if (betterWhen !== 'neutral' && direction !== 'flat') {
    const improved = betterWhen === 'lower' ? direction === 'down' : direction === 'up'
    tone = improved ? 'good' : 'bad'
  }
  return { value, direction, tone }
}

function toneForSeverity(severity: number): FlaggedNight['tone'] {
  if (severity >= 50) return 'high'
  if (severity >= 25) return 'warn'
  return 'info'
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value))
}

function buildFlaggedNights(nights: PrimaryNight[]): FlaggedNight[] {
  const byDate = new Map<string, FlaggedNight>()

  const consider = (night: FlaggedNight) => {
    const existing = byDate.get(night.date)
    if (!existing || night.severity > existing.severity) {
      byDate.set(night.date, night)
    }
  }

  // Every short night threatens adherence; severity scales with how far under 4h it is.
  for (const night of nights.filter((n) => n.hours < 4)) {
    const reason = night.hours < 0.5 ? 'no usable data' : night.hours >= 3.4 ? 'just under 4h' : 'below 4h compliance'
    consider({
      date: night.date,
      label: 'Short session',
      detail: `${night.hours.toFixed(1)}h usage · ${reason}`,
      severity: clamp01((4 - night.hours) / 4) * 100,
      tone: 'info',
    })
  }

  const worstAhi = nights.filter((n) => n.ahi != null).sort((a, b) => (b.ahi as number) - (a.ahi as number))[0]
  if (worstAhi) {
    const ahi = worstAhi.ahi as number
    const qualifier = ahi < 5 ? 'still within normal' : ahi < 15 ? 'mild range' : ahi < 30 ? 'moderate range' : 'severe range'
    consider({
      date: worstAhi.date,
      label: 'Highest AHI',
      detail: `AHI ${ahi.toFixed(1)} · ${qualifier}`,
      severity: clamp01(ahi / 15) * 100,
      tone: 'info',
    })
  }

  const worstLeak = nights.filter((n) => n.leak != null).sort((a, b) => (b.leak as number) - (a.leak as number))[0]
  if (worstLeak) {
    const leak = worstLeak.leak as number
    consider({
      date: worstLeak.date,
      label: 'Highest leak',
      detail: `${leak.toFixed(1)} L/min · check mask fit`,
      severity: clamp01(leak / 40) * 100,
      tone: 'info',
    })
  }

  return [...byDate.values()]
    .map((night) => ({ ...night, tone: toneForSeverity(night.severity) }))
    .sort((a, b) => b.severity - a.severity)
    .slice(0, 5)
}

/**
 * Compute every Overview headline metric from the loaded sessions over a single,
 * explicit trailing window. This is the one source of truth for AHI, compliance,
 * pressure, usage, leak, and the flagged-night list.
 *
 * @param sessions All loaded session summaries (any window; this function slices).
 * @param options.windowNights Length of the trailing window in calendar nights.
 * @param options.now Anchor "today"; the window is `[now - (windowNights - 1), now]`.
 * @returns The reconciled view-model.
 */
export function computeOverviewMetrics(
  sessions: SessionSummary[],
  options: { windowNights?: number; now?: Date } = {},
): OverviewMetrics {
  const windowNights = options.windowNights ?? OVERVIEW_WINDOW_NIGHTS
  const now = options.now ?? new Date()

  const startIso = isoOf(shiftDays(now, -(windowNights - 1)))
  const endIso = isoOf(now)
  const priorEndIso = isoOf(shiftDays(now, -windowNights))
  const priorStartIso = isoOf(shiftDays(now, -(windowNights * 2 - 1)))

  const allNights = [...primaryNightsByDate(sessions).values()]
  const windowNightsList = allNights
    .filter((n) => n.date >= startIso && n.date <= endIso)
    .sort((a, b) => a.date.localeCompare(b.date))
  const priorNightsList = allNights.filter((n) => n.date >= priorStartIso && n.date <= priorEndIso)

  const avgAhi = avgAhiOf(windowNightsList)
  const compliantNights = windowNightsList.filter((n) => n.hours >= 4).length
  const compliancePct = compliancePctOf(windowNightsList, windowNights)
  const avgPressure = avgPressureOf(windowNightsList)

  const eventNights = windowNightsList.filter((n) => n.ahi != null)
  const respEventsPerNight = eventNights.length > 0
    ? eventNights.reduce((sum, n) => sum + n.events, 0) / eventNights.length
    : null
  const avgUsageHours = mean(windowNightsList.map((n) => n.hours))
  const avgLeakLpm = mean(windowNightsList.filter((n) => n.leak != null).map((n) => n.leak as number))

  return {
    windowNights,
    through: windowNightsList.length > 0 ? windowNightsList[windowNightsList.length - 1].date : null,
    recordedNights: windowNightsList.length,
    avgAhi,
    ahiSeries: windowNightsList.filter((n) => n.ahi != null).map((n) => n.ahi as number),
    ahiDelta: delta(avgAhi, avgAhiOf(priorNightsList), 'lower', 0.1),
    compliantNights,
    compliancePct,
    complianceDelta: delta(compliancePct, compliancePctOf(priorNightsList, windowNights), 'higher', 0.5),
    avgPressure,
    pressureSeries: windowNightsList.filter((n) => n.pressure != null).map((n) => n.pressure as number),
    pressureDelta: delta(avgPressure, avgPressureOf(priorNightsList), 'neutral', 0.3),
    respEventsPerNight,
    avgUsageHours,
    avgLeakLpm,
    flaggedNights: buildFlaggedNights(windowNightsList),
  }
}

/** Pluralize `noun` based on `count` (`1 night` vs `3 nights`). */
export function pluralize(count: number, noun: string, plural = `${noun}s`): string {
  return count === 1 ? noun : plural
}
