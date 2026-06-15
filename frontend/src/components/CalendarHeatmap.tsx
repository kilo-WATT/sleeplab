import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import type { SessionSummary } from '../api/client'
import { leakToLpm } from '../lib/units'

export type CalendarMetric = 'ahi' | 'usage' | 'leak'

/** Properties and structure for the props. */
interface Props {
  sessions: SessionSummary[]
  metric?: CalendarMetric
  mode?: 'all' | 'single'
  collapseOnMobile?: boolean
}

interface CalendarEntry {
  ahi: number | null
  hours: number
  leak: number | null
  pressure: number | null
  durationSeconds: number
  /** True when the night has detailed recorded metrics; false for summary-only
   *  nights (machine ran and logged usage, but nothing was recorded — e.g. the
   *  SD card was left out). */
  recorded: boolean
}

// Diagonal slashes overlaid on a solid color: "used, but not fully recorded".
const SLASH_OVERLAY =
  'repeating-linear-gradient(45deg, rgba(255,255,255,0.5), rgba(255,255,255,0.5) 2px, transparent 2px, transparent 6px)'
// Gray slashes on the card background: future days and days with no data at all.
const EMPTY_HATCH =
  'repeating-linear-gradient(45deg, var(--surface-muted), var(--surface-muted) 4px, transparent 4px, transparent 8px)'

/**
 * Helper function for get ahi color.
 */
function getAhiColor(ahi: number | null): string {
  if (ahi === null) return 'var(--calendar-empty)'
  if (ahi < 5) return '#6AA136'
  if (ahi < 15) return '#C9B715'
  if (ahi < 30) return '#E9784B'
  return '#8E3D40'
}

/**
 * Helper function for get ahi label.
 */
function getAhiLabel(ahi: number | null): string {
  if (ahi === null) return 'No data'
  if (ahi < 5) return 'Normal'
  if (ahi < 15) return 'Mild'
  if (ahi < 30) return 'Moderate'
  return 'Severe'
}

function getUsageColor(hours: number | null): string {
  if (hours === null) return 'var(--calendar-empty)'
  if (hours >= 7) return '#6AA136'
  if (hours >= 4) return '#C9B715'
  return '#E9784B'
}

function getUsageLabel(hours: number | null): string {
  if (hours === null) return 'No data'
  if (hours >= 7) return `${hours.toFixed(1)}h (>=7h)`
  if (hours >= 4) return `${hours.toFixed(1)}h (compliant)`
  return `${hours.toFixed(1)}h (<4h)`
}

function getLeakColor(leak: number | null): string {
  if (leak === null) return 'var(--calendar-empty)'
  if (leak < 10) return '#6AA136'
  if (leak < 24) return '#C9B715'
  return '#E9784B'
}

function getLeakLabel(leak: number | null): string {
  if (leak === null) return 'No data'
  if (leak < 10) return `${leak.toFixed(1)} L/min (normal)`
  if (leak < 24) return `${leak.toFixed(1)} L/min (elevated)`
  return `${leak.toFixed(1)} L/min (high)`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTHS_LONG = [
  'January',
  'February',
  'March',
  'April',
  'May',
  'June',
  'July',
  'August',
  'September',
  'October',
  'November',
  'December',
]
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const LEGENDS: Record<CalendarMetric, [string, string][]> = {
  ahi: [
    ['#6AA136', '<5 Normal'],
    ['#C9B715', '5-15 Mild'],
    ['#E9784B', '15-30 Moderate'],
    ['#8E3D40', '30+ Severe'],
    ['var(--calendar-empty)', 'No data'],
  ],
  usage: [
    ['#6AA136', '>=7h'],
    ['#C9B715', '4-7h'],
    ['#E9784B', '<4h'],
    ['var(--calendar-empty)', 'No data'],
  ],
  leak: [
    ['#6AA136', '<10 L/min'],
    ['#C9B715', '10-24'],
    ['#E9784B', '24+'],
    ['var(--calendar-empty)', 'No data'],
  ],
}

function toIso(date: Date): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function parseSessionDate(folderDate: string): Date {
  const [year, month, day] = folderDate.split('-').map(Number)
  return new Date(year, month - 1, day)
}

function buildMonthDays(year: number, month: number, padToSixWeeks = false): (Date | null)[] {
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const firstDow = new Date(year, month, 1).getDay()
  const days: (Date | null)[] = Array(firstDow).fill(null)

  for (let day = 1; day <= daysInMonth; day += 1) {
    days.push(new Date(year, month, day))
  }

  if (padToSixWeeks) {
    while (days.length < 42) {
      days.push(null)
    }
  }

  return days
}

/**
 * React component or element to render the calendar heatmap.
 *
 * @returns The rendered React element.
 */
export default function CalendarHeatmap({ sessions, metric = 'ahi', mode = 'all', collapseOnMobile = false }: Props) {
  const navigate = useNavigate()
  const [selectedMonth, setSelectedMonth] = useState<{ year: number; month: number } | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [calendarExpanded, setCalendarExpanded] = useState(false)
  const pickerRef = useRef<HTMLDivElement | null>(null)

  const byDate = useMemo(() => {
    const entries: Record<string, CalendarEntry> = {}

    for (const session of sessions) {
      const existing = entries[session.folder_date]
      if (!existing || session.duration_seconds > existing.durationSeconds) {
        const leak = leakToLpm(session.avg_leak, session.leak_unit)
        entries[session.folder_date] = {
          ahi: session.ahi,
          hours: session.duration_hours,
          leak,
          pressure: session.avg_pressure,
          durationSeconds: session.duration_seconds,
          // A real recording always yields pressure (and usually leak). Summary-only
          // nights (e.g. SD card left out) carry neither — AHI is excluded here
          // because a ghost night can still report AHI 0.
          recorded: session.avg_pressure != null || leak != null,
        }
      }
    }

    return entries
  }, [sessions])

  const dates = useMemo(() => sessions.map((session) => session.folder_date).sort(), [sessions])
  const latestSessionMonth = useMemo(() => {
    if (dates.length === 0) return null
    const latest = parseSessionDate(dates[dates.length - 1])
    return { year: latest.getFullYear(), month: latest.getMonth() }
  }, [dates])

  useEffect(() => {
    if (!pickerOpen) return

    function handlePointerDown(event: PointerEvent) {
      if (!pickerRef.current?.contains(event.target as Node)) {
        setPickerOpen(false)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [pickerOpen])

  if (sessions.length === 0 || !latestSessionMonth) {
    return <div className="py-8 text-center text-sm text-[var(--muted-foreground)]">No sessions imported yet.</div>
  }

  const selected = selectedMonth ?? latestSessionMonth
  const legend = LEGENDS[metric]

  function getDotColor(entry: CalendarEntry | undefined): string {
    if (!entry) return 'var(--calendar-empty)'
    if (metric === 'usage') return getUsageColor(entry.hours)
    if (metric === 'leak') return getLeakColor(entry.leak)
    return getAhiColor(entry.ahi)
  }

  // Background for a calendar entry. Summary-only nights (usage logged but no
  // detailed recording) take the same color as their metric value — a good
  // machine-reported AHI stays green — and are slashed to flag that the night's
  // detailed data is missing/incomplete, not to imply a worse reading.
  function cellStyle(entry: CalendarEntry): CSSProperties {
    if (!entry.recorded) {
      return { backgroundColor: getDotColor(entry), backgroundImage: SLASH_OVERLAY }
    }
    return { background: getDotColor(entry) }
  }

  function getDotTooltip(iso: string, entry: CalendarEntry | undefined): string {
    if (!entry) return `${iso}\nNo session`
    if (!entry.recorded) return `${iso}\nUsed ${entry.hours.toFixed(1)}h · not recorded (no SD card)`
    if (metric === 'usage') return `${iso}\nUsage: ${getUsageLabel(entry.hours)}`
    if (metric === 'leak') return `${iso}\nLeak: ${getLeakLabel(entry.leak)}`
    return `${iso}\nAHI: ${entry.ahi?.toFixed(1) ?? '-'} (${getAhiLabel(entry.ahi ?? null)})`
  }

  function shiftMonth(delta: number) {
    const next = new Date(selected.year, selected.month + delta, 1)
    setSelectedMonth({ year: next.getFullYear(), month: next.getMonth() })
    setPickerOpen(false)
  }

  function selectMonth(year: number, month: number) {
    setSelectedMonth({ year, month })
    setPickerOpen(false)
  }

  function renderLegend(compact = false) {
    return (
      <div className={`flex flex-wrap items-center text-xs text-[var(--muted-foreground)] ${compact ? 'gap-1.5' : 'gap-2 sm:gap-3'}`}>
        <span className={`uppercase text-[var(--muted-foreground)] ${compact ? 'text-[10px] tracking-[0.14em]' : 'basis-full tracking-[0.2em] sm:basis-auto'}`}>
          {metric === 'ahi' ? 'AHI' : metric === 'usage' ? 'Usage' : 'Leak'}
        </span>
        {legend.map(([color, label]) => (
          <span
            key={label}
            className={`inline-flex items-center gap-1 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] ${compact ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 sm:gap-2 sm:px-3'}`}
          >
            <span className="inline-block h-2 w-2 rounded-sm" style={{ background: color }} />
            {label}
          </span>
        ))}
      </div>
    )
  }

  if (mode === 'all') {
    const startDate = parseSessionDate(dates[0])
    const endDate = parseSessionDate(dates[dates.length - 1])
    const months: { year: number; month: number; days: (Date | null)[] }[] = []
    let current = new Date(startDate.getFullYear(), startDate.getMonth(), 1)
    const end = new Date(endDate.getFullYear(), endDate.getMonth(), 1)

    while (current <= end) {
      const year = current.getFullYear()
      const month = current.getMonth()
      months.push({ year, month, days: buildMonthDays(year, month) })
      current = new Date(year, month + 1, 1)
    }

    return (
      <div className="space-y-6">
        {renderLegend()}

        <div className="grid grid-cols-[repeat(auto-fit,minmax(132px,1fr))] gap-3 sm:flex sm:flex-wrap sm:gap-5">
          {months.map(({ year, month, days }) => (
            <div key={`${year}-${month}`} className="rounded-[18px] border border-[var(--border)] bg-[var(--surface-soft)] p-3 sm:rounded-[24px] sm:p-4">
              <div className="mb-3 text-sm font-bold text-[var(--foreground)]">{MONTHS[month]} {year}</div>
              <div className="mb-1 grid grid-cols-7 justify-items-center gap-1">
                {DAYS.map((day) => <span key={day} className="text-center text-[10px] text-[var(--muted-foreground)]">{day[0]}</span>)}
              </div>
              <div className="grid grid-cols-7 justify-items-center gap-1">
                {days.map((date, index) => {
                  if (!date) return <span key={index} className="h-3.5 w-3.5 sm:h-4 sm:w-4" />
                  const iso = toIso(date)
                  const entry = byDate[iso]
                  return (
                    <span
                      key={iso}
                      className="h-3.5 w-3.5 rounded-sm transition hover:scale-110 sm:h-4 sm:w-4"
                      style={{
                        ...(entry ? cellStyle(entry) : { background: 'var(--calendar-empty)' }),
                        cursor: entry ? 'pointer' : 'default',
                      }}
                      title={getDotTooltip(iso, entry)}
                      onClick={() => entry && navigate(`/sessions/${iso}`)}
                    />
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  const years = [...new Set(dates.map((date) => parseSessionDate(date).getFullYear()))]
  if (!years.includes(selected.year)) {
    years.push(selected.year)
    years.sort((a, b) => a - b)
  }

  const monthDays = buildMonthDays(selected.year, selected.month, true)
  const recordedMonthDays = monthDays.filter((date): date is Date => Boolean(date && byDate[toIso(date)]))
  // Show the most recent nights (ending on the current/last recorded day), not the
  // first days of the month — that is what a phone-width user wants to glance at.
  const previewDays = recordedMonthDays.slice(-10)
  const extraPreviewDays = Math.max(0, recordedMonthDays.length - previewDays.length)

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const isFutureDay = (date: Date) => date.getTime() > today.getTime()

  // Drop only the trailing padding weeks that contain no real calendar date (the
  // 6-week pad artifact) — so the trend chart isn't pushed down a screen. Keep
  // every week that holds an actual day of the month, including the current
  // month's future days, which render grayed out.
  const weeks: (Date | null)[][] = []
  for (let index = 0; index < monthDays.length; index += 7) {
    weeks.push(monthDays.slice(index, index + 7))
  }
  let lastRealWeek = -1
  weeks.forEach((week, index) => {
    if (week.some((date) => date != null)) lastRealWeek = index
  })
  const visibleDays = weeks.slice(0, lastRealWeek + 1).flat()

  return (
    <>
      {collapseOnMobile && !calendarExpanded ? (
        <div className="space-y-3 md:hidden">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-bold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              {MONTHS_LONG[selected.month]} {selected.year}
            </p>
            <button
              type="button"
              className="rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-4 py-2 text-sm font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]"
              onClick={() => setCalendarExpanded(true)}
            >
              Show calendar
            </button>
          </div>
          <div className="space-y-1.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-[var(--muted-foreground)]">
              Recorded nights
            </p>
            <div className="flex min-h-8 flex-wrap items-center gap-1.5">
            {extraPreviewDays > 0 ? (
              <span className="mr-1 text-sm font-semibold text-[var(--muted-foreground)]">+{extraPreviewDays} earlier</span>
            ) : null}
            {previewDays.length > 0 ? previewDays.map((date) => {
              const iso = toIso(date)
              const entry = byDate[iso]
              return (
                <button
                  key={iso}
                  type="button"
                  className="inline-flex h-7 min-w-7 items-center justify-center rounded-[7px] px-1 text-[11px] font-bold text-white transition hover:scale-105"
                  style={entry ? cellStyle(entry) : { background: 'var(--calendar-empty)' }}
                  title={getDotTooltip(iso, entry)}
                  onClick={() => navigate(`/sessions/${iso}`)}
                  aria-label={getDotTooltip(iso, entry).replace('\n', ' ')}
                >
                  {date.getDate()}
                </button>
              )
            }) : (
              <span className="text-sm text-[var(--muted-foreground)]">No recorded nights this month.</span>
            )}
            </div>
          </div>
        </div>
      ) : null}

      <div className={`space-y-4 ${collapseOnMobile && !calendarExpanded ? 'hidden md:block' : ''}`}>
      <div className="relative flex items-center justify-between gap-2" ref={pickerRef}>
        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] text-lg font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]"
          onClick={() => shiftMonth(-1)}
          aria-label="Show previous month"
        >
          <span aria-hidden="true">{'<'}</span>
        </button>

        <button
          type="button"
          className="min-w-0 rounded-full px-3 py-2 text-center text-sm font-extrabold text-[var(--foreground)] transition hover:bg-[var(--surface-soft)] sm:text-base"
          onClick={() => setPickerOpen((open) => !open)}
          aria-expanded={pickerOpen}
        >
          {MONTHS_LONG[selected.month]} {selected.year}
        </button>

        <button
          type="button"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] text-lg font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]"
          onClick={() => shiftMonth(1)}
          aria-label="Show next month"
        >
          <span aria-hidden="true">{'>'}</span>
        </button>

        {pickerOpen && (
          <div className="absolute left-1/2 top-11 z-20 w-[min(20rem,calc(100vw-2rem))] -translate-x-1/2 rounded-[16px] border border-[var(--border)] bg-[var(--popover-surface)] p-3 shadow-lg">
            <div className="mb-3 flex items-center justify-between gap-2">
              <button
                type="button"
                className="rounded-full px-3 py-1 text-sm font-bold text-[var(--accent)] transition hover:bg-[var(--accent-soft)]"
                onClick={() => setSelectedMonth({ year: selected.year - 1, month: selected.month })}
                aria-label="Previous year"
              >
                {'<'}
              </button>
              <select
                className="min-w-24 rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-3 py-1.5 text-center text-sm font-bold text-[var(--foreground)]"
                value={selected.year}
                onChange={(event) => setSelectedMonth({ year: Number(event.target.value), month: selected.month })}
                aria-label="Select year"
              >
                {years.map((year) => (
                  <option key={year} value={year}>{year}</option>
                ))}
              </select>
              <button
                type="button"
                className="rounded-full px-3 py-1 text-sm font-bold text-[var(--accent)] transition hover:bg-[var(--accent-soft)]"
                onClick={() => setSelectedMonth({ year: selected.year + 1, month: selected.month })}
                aria-label="Next year"
              >
                {'>'}
              </button>
            </div>

            <div className="grid grid-cols-3 gap-1.5">
              {MONTHS.map((monthLabel, month) => (
                <button
                  key={monthLabel}
                  type="button"
                  className={`rounded-[10px] px-2 py-2 text-sm font-bold transition ${
                    selected.month === month
                      ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                      : 'text-[var(--muted-foreground)] hover:bg-[var(--surface-soft)] hover:text-[var(--foreground)]'
                  }`}
                  onClick={() => selectMonth(selected.year, month)}
                >
                  {monthLabel}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {renderLegend(true)}

      <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface-soft)] p-3 sm:p-4">
        <div className="mb-2 grid grid-cols-7 gap-1.5 sm:gap-2">
          {DAYS.map((day) => (
            <span key={day} className="text-center text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">
              {day.slice(0, 2)}
            </span>
          ))}
        </div>

        <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
          {visibleDays.map((date, index) => {
            if (!date) {
              return <span key={`empty-${index}`} className="aspect-square rounded-[8px] bg-[var(--surface-muted)] opacity-35" />
            }

            const iso = toIso(date)
            const entry = byDate[iso]

            // No session: future days are plainly grayed out; past gaps (missing
            // data) keep the slashes to flag that data is absent.
            if (!entry) {
              const upcoming = isFutureDay(date)
              return (
                <span
                  key={iso}
                  className="flex aspect-square min-w-0 items-center justify-center rounded-[8px] text-xs font-bold text-[var(--muted-foreground)] opacity-50 sm:rounded-[10px] sm:text-sm"
                  style={{ background: upcoming ? 'var(--surface-muted)' : EMPTY_HATCH }}
                  title={`${iso}\n${upcoming ? 'Upcoming' : 'No session'}`}
                  aria-label={`${iso} ${upcoming ? 'upcoming' : 'no session'}`}
                >
                  {date.getDate()}
                </span>
              )
            }

            return (
              <button
                key={iso}
                type="button"
                className="aspect-square min-w-0 rounded-[8px] border border-transparent text-xs font-bold text-white transition hover:scale-[1.03] focus:outline-none focus:ring-2 focus:ring-[var(--accent-border)] sm:rounded-[10px] sm:text-sm"
                style={{ ...cellStyle(entry), cursor: 'pointer' }}
                title={getDotTooltip(iso, entry)}
                onClick={() => navigate(`/sessions/${iso}`)}
                aria-label={getDotTooltip(iso, entry).replace('\n', ' ')}
              >
                {date.getDate()}
              </button>
            )
          })}
        </div>
      </div>
      </div>
    </>
  )
}
