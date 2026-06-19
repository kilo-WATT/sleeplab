import { type CSSProperties, useEffect, useMemo, useRef, useState } from 'react'

import {
  type NightCell,
  type NightFilter,
  type NightMetric,
  buildMonthGrid,
  matchesFilter,
  metricColor,
  metricLegend,
  metricLongValue,
  metricTileValue,
  toIso,
} from '../lib/nightExplorer'
import { EYEBROW } from './nightExplorerUi'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const MONTHS_LONG = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]
const DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']

// Diagonal slashes overlaid on a solid color: "used, but not fully recorded".
// Kept subtle so it reads as a texture cue without overpowering the tile color.
const SLASH_OVERLAY =
  'repeating-linear-gradient(45deg, rgba(255,255,255,0.32), rgba(255,255,255,0.32) 1.5px, transparent 1.5px, transparent 6px)'
// Gray slashes on the card background: past days with no session at all (gaps).
const EMPTY_HATCH =
  'repeating-linear-gradient(45deg, var(--surface-muted), var(--surface-muted) 4px, transparent 4px, transparent 8px)'

/** Properties and structure for the night calendar. */
interface Props {
  cells: Map<string, NightCell>
  metric: NightMetric
  filter: NightFilter
  selectedDate: string | null
  onSelect: (iso: string) => void
}

/**
 * Calendar-first single-month grid for the Night Explorer. Each recorded night
 * is a focusable tile carrying its severity color, the selected metric value,
 * and subtle markers (equipment change, needs-review, missing recording). The
 * active quick filter dims non-matching nights rather than hiding days, so the
 * month structure stays intact.
 *
 * @returns The rendered React element.
 */
export default function NightCalendar({ cells, metric, filter, selectedDate, onSelect }: Props) {
  const [pickerOpen, setPickerOpen] = useState(false)
  const pickerRef = useRef<HTMLDivElement | null>(null)

  const dates = useMemo(() => [...cells.keys()].sort(), [cells])
  const latestMonth = useMemo(() => {
    if (dates.length === 0) return { year: new Date().getFullYear(), month: new Date().getMonth() }
    const latest = dates[dates.length - 1].split('-').map(Number)
    return { year: latest[0], month: latest[1] - 1 }
  }, [dates])

  const [view, setView] = useState(latestMonth)
  // Follow the selected night into its month when it lives elsewhere. Adjusting
  // state during render (rather than in an effect) is React's recommended pattern
  // for reacting to a prop change without an extra paint.
  const [trackedSelection, setTrackedSelection] = useState<string | null>(selectedDate)
  if (selectedDate !== trackedSelection) {
    setTrackedSelection(selectedDate)
    if (selectedDate) {
      const [year, month] = selectedDate.split('-').map(Number)
      setView({ year, month: month - 1 })
    }
  }

  useEffect(() => {
    if (!pickerOpen) return
    function handlePointerDown(event: PointerEvent) {
      if (!pickerRef.current?.contains(event.target as Node)) setPickerOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [pickerOpen])

  const grid = useMemo(() => buildMonthGrid(view.year, view.month), [view.year, view.month])

  const years = useMemo(() => {
    const set = new Set(dates.map((date) => Number(date.split('-')[0])))
    set.add(view.year)
    return [...set].sort((a, b) => a - b)
  }, [dates, view.year])

  const today = new Date()
  today.setHours(0, 0, 0, 0)

  function shiftMonth(delta: number) {
    const next = new Date(view.year, view.month + delta, 1)
    setView({ year: next.getFullYear(), month: next.getMonth() })
    setPickerOpen(false)
  }

  function cellBackground(cell: NightCell): CSSProperties {
    const color = metricColor(cell, metric)
    if (cell.missingData) return { backgroundColor: color, backgroundImage: SLASH_OVERLAY }
    return { background: color }
  }

  function tooltip(iso: string, cell: NightCell): string {
    const lines = [iso, metricLongValue(cell, metric)]
    if (cell.missingData) lines.push('Used but not recorded (no SD card)')
    if (cell.equipmentChanges.length > 0) {
      lines.push(`Equipment: ${cell.equipmentChanges.map((c) => c.label).join(', ')}`)
    }
    return lines.join('\n')
  }

  return (
    <div className="space-y-4">
      <div className="flex min-h-9 items-center justify-between gap-3">
        <p className={EYEBROW}>Night calendar</p>

        <div className="relative flex items-center gap-1" ref={pickerRef}>
          <button
            type="button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] text-base font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]"
            onClick={() => shiftMonth(-1)}
            aria-label="Show previous month"
          >
            <span aria-hidden="true">{'<'}</span>
          </button>

          <button
            type="button"
            className="min-w-0 rounded-full px-3 py-1.5 text-center text-sm font-extrabold leading-none text-[var(--foreground)] transition hover:bg-[var(--surface-soft)]"
            onClick={() => setPickerOpen((open) => !open)}
            aria-expanded={pickerOpen}
          >
            {MONTHS_LONG[view.month]} {view.year}
          </button>

          <button
            type="button"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-[var(--border)] bg-[var(--surface-soft)] text-base font-bold text-[var(--accent)] transition hover:border-[var(--accent-border)] hover:bg-[var(--accent-soft)]"
            onClick={() => shiftMonth(1)}
            aria-label="Show next month"
          >
            <span aria-hidden="true">{'>'}</span>
          </button>

          {pickerOpen && (
            <div className="absolute right-0 top-11 z-20 w-[min(20rem,calc(100vw-2rem))] rounded-[16px] border border-[var(--border)] bg-[var(--popover-surface)] p-3 shadow-lg">
            <div className="mb-3 flex items-center justify-between gap-2">
              <button
                type="button"
                className="rounded-full px-3 py-1 text-sm font-bold text-[var(--accent)] transition hover:bg-[var(--accent-soft)]"
                onClick={() => setView({ year: view.year - 1, month: view.month })}
                aria-label="Previous year"
              >
                {'<'}
              </button>
              <select
                className="min-w-24 rounded-full border border-[var(--border)] bg-[var(--surface-strong)] px-3 py-1.5 text-center text-sm font-bold text-[var(--foreground)]"
                value={view.year}
                onChange={(event) => setView({ year: Number(event.target.value), month: view.month })}
                aria-label="Select year"
              >
                {years.map((year) => (
                  <option key={year} value={year}>{year}</option>
                ))}
              </select>
              <button
                type="button"
                className="rounded-full px-3 py-1 text-sm font-bold text-[var(--accent)] transition hover:bg-[var(--accent-soft)]"
                onClick={() => setView({ year: view.year + 1, month: view.month })}
                aria-label="Next year"
              >
                {'>'}
              </button>
            </div>
            <div className="grid grid-cols-3 gap-1.5">
              {MONTHS.map((label, month) => (
                <button
                  key={label}
                  type="button"
                  className={`rounded-[10px] px-2 py-2 text-sm font-bold transition ${
                    view.month === month
                      ? 'bg-[var(--accent-soft)] text-[var(--accent)]'
                      : 'text-[var(--muted-foreground)] hover:bg-[var(--surface-soft)] hover:text-[var(--foreground)]'
                  }`}
                  onClick={() => {
                    setView({ year: view.year, month })
                    setPickerOpen(false)
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          )}
        </div>
      </div>

      <div className="rounded-[18px] border border-[var(--border)] bg-[var(--surface-soft)] p-2.5 sm:p-4">
        <div className="mb-2 grid grid-cols-7 gap-1.5 sm:gap-2">
          {DAYS.map((day) => (
            <span key={day} className="text-center text-[11px] font-bold uppercase tracking-[0.08em] text-[var(--muted-foreground)]">
              {day}
            </span>
          ))}
        </div>

        <div className="grid grid-cols-7 gap-1.5 sm:gap-2" data-testid="night-grid">
          {grid.map((date, index) => {
            if (!date) return <span key={`empty-${index}`} className="aspect-square" />

            const iso = toIso(date)
            const cell = cells.get(iso)
            const isFuture = date.getTime() > today.getTime()

            if (!cell) {
              return (
                <span
                  key={iso}
                  className="flex aspect-square min-w-0 items-start justify-start rounded-[10px] p-1 text-[10px] font-bold leading-none text-[var(--muted-foreground)] opacity-40 sm:text-[11px]"
                  style={{ background: isFuture ? 'var(--surface-muted)' : EMPTY_HATCH }}
                  title={`${iso}\n${isFuture ? 'Upcoming' : 'No session imported'}`}
                  aria-label={`${iso} ${isFuture ? 'upcoming' : 'no session imported'}`}
                >
                  {date.getDate()}
                </span>
              )
            }

            const dimmed = !matchesFilter(cell, filter)
            const isSelected = selectedDate === iso
            const value = metricTileValue(cell, metric)

            return (
              <button
                key={iso}
                type="button"
                aria-pressed={isSelected}
                aria-label={`${tooltip(iso, cell).replace(/\n/g, '. ')}${isSelected ? '. Selected' : ''}`}
                title={tooltip(iso, cell)}
                onClick={() => onSelect(iso)}
                className={`relative flex aspect-square min-w-0 flex-col rounded-[10px] border p-1 text-white transition hover:scale-[1.04] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--foreground)] ${
                  isSelected ? 'border-white ring-2 ring-[var(--foreground)]' : 'border-transparent'
                } ${dimmed ? 'opacity-30' : ''}`}
                style={{ ...cellBackground(cell), cursor: 'pointer' }}
              >
                <span className="text-[10px] font-bold leading-none text-white/85 sm:text-[11px]">
                  {date.getDate()}
                </span>
                <span className="flex flex-1 items-center justify-center">
                  {value ? (
                    <span className="text-[12px] font-extrabold leading-none sm:text-sm">{value}</span>
                  ) : null}
                </span>
                {cell.equipmentChanges.length > 0 ? (
                  <span
                    className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[#5251A7] ring-1 ring-white/80"
                    aria-hidden="true"
                  />
                ) : null}
                {cell.needsReview ? (
                  <span
                    className="absolute bottom-1 right-1 h-1.5 w-1.5 rounded-full bg-black/55 ring-1 ring-white/80"
                    aria-hidden="true"
                  />
                ) : null}
              </button>
            )
          })}
        </div>
      </div>

      {/* Legends — quiet, evenly spaced reference for color + markers */}
      <div className="space-y-2 border-t border-[var(--border)] pt-3">
        <div className="flex flex-wrap items-center gap-1.5">
          {metricLegend(metric).map(([color, label]) => (
            <span
              key={label}
              className="inline-flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-2 py-1 text-[10px] leading-none text-[var(--muted-foreground)]"
            >
              <span className="inline-block h-2 w-2 rounded-sm" style={{ background: color }} />
              {label}
            </span>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[10px] leading-none text-[var(--muted-foreground)]">
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-[#5251A7] ring-1 ring-[var(--border)]" /> Equipment change
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-2 rounded-full bg-black/55 ring-1 ring-[var(--border)]" /> Needs review
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span
              className="inline-block h-2.5 w-2.5 rounded-[3px] ring-1 ring-[var(--border)]"
              style={{ backgroundColor: 'var(--calendar-empty)', backgroundImage: SLASH_OVERLAY }}
            />
            Used, not recorded
          </span>
        </div>
      </div>
    </div>
  )
}
