import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import type { Equipment, SessionSummary } from '../api/client'
import { api } from '../api/client'
import NightCalendar from '../components/NightCalendar'
import NightDetailPanel from '../components/NightDetailPanel'
import { CardSection, MICRO_LABEL } from '../components/nightExplorerUi'
import { Card } from '../components/ui/card'
import { IMPORT_COMPLETED_EVENT } from '../lib/aiSummaryCache'
import {
  type NightCell,
  type NightFilter,
  type NightMetric,
  buildNightIndex,
  findImportGaps,
  matchesFilter,
  metricColor,
} from '../lib/nightExplorer'

/** Helper function for current streak (consecutive most-recent nights with data). */
function currentStreak(dates: string[]): number {
  const unique = [...new Set(dates)].sort().reverse()
  if (unique.length === 0) return 0
  let streak = 1
  let previous = new Date(`${unique[0]}T00:00:00`)
  for (let index = 1; index < unique.length; index += 1) {
    const next = new Date(`${unique[index]}T00:00:00`)
    const diffDays = Math.round((previous.getTime() - next.getTime()) / 86_400_000)
    if (diffDays !== 1) break
    streak += 1
    previous = next
  }
  return streak
}

function formatShort(iso: string): string {
  const [year, month, day] = iso.split('-').map(Number)
  return new Date(year, month - 1, day).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const METRICS: { key: NightMetric; label: string }[] = [
  { key: 'ahi', label: 'AHI' },
  { key: 'usage', label: 'Usage' },
  { key: 'leak', label: 'Leak' },
  { key: 'events', label: 'Events' },
]

const FILTERS: { key: NightFilter; label: string }[] = [
  { key: 'all', label: 'All nights' },
  { key: 'review', label: 'Needs review' },
  { key: 'short', label: 'Short usage' },
  { key: 'leak', label: 'High leak' },
  { key: 'ahi', label: 'High AHI' },
  { key: 'equipment', label: 'Equipment' },
]

/**
 * Night Explorer — a sleep logbook for finding, previewing, and opening a
 * specific night. Calendar-first by design; it deliberately avoids the Overview
 * dashboard's AI insights, trend chart, and report export, focusing instead on
 * navigation, calendar-specific context (gaps, equipment changes, short usage,
 * high leak/AHI, missing recordings), and a quick selected-night inspector.
 *
 * @returns The rendered React element.
 */
export default function CalendarPage() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [equipment, setEquipment] = useState<Equipment[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [metric, setMetric] = useState<NightMetric>('ahi')
  const [filter, setFilter] = useState<NightFilter>('all')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  useEffect(() => {
    async function loadCalendar() {
      try {
        const [sessionData, equipmentData] = await Promise.all([
          api.getSessions({ per_page: 600 }),
          api.listEquipment().catch(() => [] as Equipment[]),
        ])
        setSessions(sessionData)
        setEquipment(equipmentData)
        setError(null)
      } catch (err) {
        setError(String(err))
      } finally {
        setLoading(false)
      }
    }

    void loadCalendar()

    function handleImportCompleted() {
      setLoading(true)
      void loadCalendar()
    }

    window.addEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
    return () => window.removeEventListener(IMPORT_COMPLETED_EVENT, handleImportCompleted)
  }, [])

  const cells = useMemo(() => buildNightIndex(sessions, equipment), [sessions, equipment])
  const sortedDates = useMemo(() => [...cells.keys()].sort(), [cells])
  const gaps = useMemo(() => findImportGaps(cells), [cells])

  const recentNights = useMemo(() => {
    return [...cells.values()]
      .filter((cell) => matchesFilter(cell, filter))
      .sort((a, b) => b.date.localeCompare(a.date))
      .slice(0, 6)
  }, [cells, filter])

  const selectedCell = selectedDate ? cells.get(selectedDate) ?? null : null

  if (loading) {
    return (
      <div className="rounded-[22px] border border-[var(--border)] bg-[var(--surface-strong)] p-10 text-center text-[var(--muted-foreground)]">
        Loading calendar…
      </div>
    )
  }

  if (error) {
    return (
      <div className="rounded-[22px] border border-[var(--accent-border)] bg-[var(--danger-soft)] p-10 text-center text-[var(--danger-text)]">
        Error loading calendar: {error}
      </div>
    )
  }

  if (sortedDates.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <p className="text-lg font-extrabold text-[var(--foreground)]">No nights imported yet</p>
          <p className="max-w-md text-sm text-[var(--muted-foreground)]">
            Once you import therapy data, every night shows up here as a browsable logbook — find a night,
            preview it, and jump straight into the session.
          </p>
          <Link
            to="/import"
            className="rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-bold text-[var(--accent-foreground)] transition hover:bg-[var(--accent-hover)]"
          >
            Go to import
          </Link>
        </div>
      </Card>
    )
  }

  const nightsWithData = sortedDates.length
  const streak = currentStreak(sortedDates)
  const rangeLabel = `${formatShort(sortedDates[0])} – ${formatShort(sortedDates[sortedDates.length - 1])}`
  const gapNights = gaps.reduce((sum, gap) => sum + gap.nights, 0)

  return (
    <div className="space-y-5">
      {/* Compact utility strip — navigation-focused, not a dashboard */}
      <div className="flex flex-wrap items-stretch gap-2 sm:gap-3" data-testid="calendar-utility-bar">
        <UtilityStat label="Nights with data" value={String(nightsWithData)} />
        <UtilityStat label="Current streak" value={`${streak} ${streak === 1 ? 'night' : 'nights'}`} />
        <UtilityStat label="Range" value={rangeLabel} />
        <UtilityStat label="Gap nights" value={String(gapNights)} hint={gaps.length > 0 ? `${gaps.length} gaps` : 'none'} />
      </div>

      {/* Controls: metric toggle + quick filters */}
      <div
        className="flex flex-col gap-x-6 gap-y-3 lg:flex-row lg:items-center lg:justify-between"
        data-testid="calendar-controls"
      >
        <div className="flex w-fit shrink-0 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] p-1">
          {METRICS.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={metric === item.key}
              onClick={() => setMetric(item.key)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-bold leading-none transition ${
                metric === item.key
                  ? 'bg-[var(--surface-strong)] text-[var(--accent)] shadow-sm'
                  : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap gap-2 lg:justify-end" role="group" aria-label="Filter nights">
          {FILTERS.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={filter === item.key}
              onClick={() => setFilter(item.key)}
              className={`rounded-full border px-3 py-1.5 text-xs font-bold leading-none transition ${
                filter === item.key
                  ? 'border-[var(--accent-border)] bg-[var(--accent-soft)] text-[var(--accent)]'
                  : 'border-[var(--border)] bg-[var(--surface-soft)] text-[var(--muted-foreground)] hover:border-[var(--accent-border)] hover:text-[var(--foreground)]'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      {/* Calendar (focus) + selected-night inspector */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1.6fr)_minmax(320px,1fr)] lg:items-start">
        <CardSection className="order-1">
          <NightCalendar
            cells={cells}
            metric={metric}
            filter={filter}
            selectedDate={selectedDate}
            onSelect={(iso) => setSelectedDate((current) => (current === iso ? null : iso))}
          />
        </CardSection>

        <div className="order-2 space-y-5 lg:sticky lg:top-4">
          <NightDetailPanel selectedDate={selectedDate} cell={selectedCell} />

          <RecentNights
            nights={recentNights}
            metric={metric}
            selectedDate={selectedDate}
            onSelect={setSelectedDate}
            filterActive={filter !== 'all'}
          />

          {gaps.length > 0 ? (
            <CardSection
              eyebrow="Import gaps"
              description="Stretches with no recorded night."
              testId="import-gaps"
            >
              <ul className="space-y-1.5">
                {gaps.slice(0, 4).map((gap) => (
                  <li
                    key={gap.start}
                    className="flex items-center justify-between gap-3 rounded-[12px] border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-2.5 text-xs"
                  >
                    <span className="font-semibold text-[var(--foreground)]">
                      {gap.start === gap.end ? formatShort(gap.start) : `${formatShort(gap.start)} – ${formatShort(gap.end)}`}
                    </span>
                    <span className="font-bold text-[var(--muted-foreground)]">
                      {gap.nights} {gap.nights === 1 ? 'night' : 'nights'}
                    </span>
                  </li>
                ))}
              </ul>
            </CardSection>
          ) : null}
        </div>
      </div>
    </div>
  )
}

function UtilityStat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex min-w-[8.5rem] flex-1 flex-col gap-2 rounded-[14px] border border-[var(--border)] bg-[var(--card)] px-3.5 py-3">
      <p className={MICRO_LABEL}>{label}</p>
      <p className="text-base font-extrabold leading-none text-[var(--foreground)]">{value}</p>
      <p className="text-[10px] leading-none text-[var(--muted-foreground)]">{hint ?? ' '}</p>
    </div>
  )
}

function RecentNights({
  nights,
  metric,
  selectedDate,
  onSelect,
  filterActive,
}: {
  nights: NightCell[]
  metric: NightMetric
  selectedDate: string | null
  onSelect: (iso: string) => void
  filterActive: boolean
}) {
  return (
    <CardSection eyebrow={filterActive ? 'Matching nights' : 'Recent nights'} testId="recent-nights">
      {nights.length === 0 ? (
        <p className="text-xs text-[var(--muted-foreground)]">No nights match this filter.</p>
      ) : (
        <ul className="space-y-1.5">
          {nights.map((cell) => (
            <li key={cell.date}>
              <button
                type="button"
                aria-pressed={selectedDate === cell.date}
                onClick={() => onSelect(cell.date)}
                className={`flex w-full items-center gap-3 rounded-[12px] border px-3 py-2.5 text-left transition ${
                  selectedDate === cell.date
                    ? 'border-[var(--accent-border)] bg-[var(--accent-soft)]'
                    : 'border-[var(--border)] bg-[var(--surface-soft)] hover:border-[var(--accent-border)]'
                }`}
              >
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: metricColor(cell, metric) }}
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 text-xs font-bold text-[var(--foreground)]">{formatShort(cell.date)}</span>
                <span className="shrink-0 text-[11px] leading-none text-[var(--muted-foreground)]">
                  {cell.ahi == null ? '—' : `AHI ${cell.ahi.toFixed(1)}`} · {cell.hours.toFixed(1)}h
                </span>
                {cell.needsReview ? (
                  <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--orange-500)]" aria-hidden="true" />
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </CardSection>
  )
}
