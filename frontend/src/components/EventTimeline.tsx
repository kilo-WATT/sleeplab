import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'

import type { EventRecord } from '../api/client'
import { getDisplayTz } from '../lib/displayTz'

/**
 * Properties and structure for the props.
 */
interface Props {
  events: EventRecord[]
  durationSeconds: number
  startDatetime: string
  wholeNightDomain: [number, number]
  selectedTimeDomain?: [number, number] | null
  selectedEventId?: number | null
  onSelectEvent?: (event: EventRecord) => void
  onWindowChange?: (window: [number, number]) => void
}

/**
 * React component or element to render the e v e n t_ c o l o r s.
 *
 * @returns The rendered React element.
 */
const EVENT_COLORS: Record<string, string> = {
  'Central Apnea':     '#5251A7',
  'Obstructive Apnea': '#8E3D40',
  'Hypopnea':          '#E9784B',
  'Apnea':             '#C9B715',
  'Arousal':           '#6AA136',
  'Large Leak':        '#b8b8b8',
}

/**
 * Formats a timestamp into the user's configured display timezone.
 */
function fmtTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: getDisplayTz() })
}

/**
 * Returns the best available timestamp for an event.
 */
function eventTimestamp(event: EventRecord, startTs: number): number {
  const ts = new Date(event.event_datetime).getTime()
  return Number.isFinite(ts) ? ts : startTs + event.onset_seconds * 1000
}

/**
 * Renders a proportional event timeline for the current session.
 */
export default function EventTimeline({
  events,
  durationSeconds,
  startDatetime,
  wholeNightDomain,
  selectedTimeDomain,
  selectedEventId,
  onSelectEvent,
  onWindowChange,
}: Props) {
  const [activeTooltip, setActiveTooltip] = useState<{
    eventType: string
    timeLabel: string
    durationLabel: string
    leftPct: number
  } | null>(null)
  const [dragPreview, setDragPreview] = useState<[number, number] | null>(null)
  const navigatorRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{
    pointerId: number
    startX: number
    initialWindow: [number, number]
  } | null>(null)

  const startTs = new Date(startDatetime).getTime()
  const fallbackStartTs = Number.isFinite(startTs) ? startTs : 0
  const fallbackEndTs = fallbackStartTs + durationSeconds * 1000
  const domainStart = Number.isFinite(wholeNightDomain[0]) ? wholeNightDomain[0] : fallbackStartTs
  const domainEnd = Number.isFinite(wholeNightDomain[1]) ? wholeNightDomain[1] : fallbackEndTs
  const timelineDurationMs = Math.max(domainEnd - domainStart, 1)
  const visibleWindow = dragPreview ?? selectedTimeDomain ?? wholeNightDomain
  const windowStart = Math.max(visibleWindow[0], domainStart)
  const windowEnd = Math.min(visibleWindow[1], domainEnd)
  const windowLeftPct = ((windowStart - domainStart) / timelineDurationMs) * 100
  const windowWidthPct = Math.max(((windowEnd - windowStart) / timelineDurationMs) * 100, 0.8)
  const eventTypes = [...new Set(events.map(e => e.event_type))]

  function handleWindowPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (!selectedTimeDomain || !onWindowChange) return
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      initialWindow: selectedTimeDomain,
    }
    event.currentTarget.setPointerCapture?.(event.pointerId)
    event.preventDefault()
  }

  function handleNavigatorPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current
    const rect = navigatorRef.current?.getBoundingClientRect()
    if (!drag || !rect?.width) return
    const duration = drag.initialWindow[1] - drag.initialWindow[0]
    const deltaMs = ((event.clientX - drag.startX) / rect.width) * timelineDurationMs
    const nextStart = Math.min(
      Math.max(drag.initialWindow[0] + deltaMs, domainStart),
      domainEnd - duration,
    )
    setDragPreview([nextStart, nextStart + duration])
  }

  function finishWindowDrag() {
    if (dragRef.current && dragPreview && onWindowChange) {
      onWindowChange(dragPreview)
    }
    dragRef.current = null
    setDragPreview(null)
  }

  return (
    <div className="min-w-0 space-y-4 overflow-hidden">
      <div className="flex flex-wrap gap-3 text-xs text-[var(--muted-foreground)]">
        {eventTypes.map(t => (
          <span key={t} className="inline-flex items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--surface-soft)] px-3 py-1">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: EVENT_COLORS[t] ?? '#888' }} />
            {t}
          </span>
        ))}
      </div>

      <div className="relative">
        <div className="relative mb-2 h-4 text-[10px] text-[var(--muted-foreground)]">
          {[0, 0.25, 0.5, 0.75, 1].map(pct => (
            <span
              key={pct}
              className={`absolute whitespace-nowrap ${
                pct === 0 ? '' : pct === 1 ? '-translate-x-full' : '-translate-x-1/2'
              }`}
              style={{ left: `${pct * 100}%` }}
            >
              {fmtTime(domainStart + timelineDurationMs * pct)}
            </span>
          ))}
        </div>

        {activeTooltip ? (
          <div
            className="pointer-events-none absolute -top-14 z-10 -translate-x-1/2 rounded-[16px] border border-[var(--border)] bg-[var(--popover-surface)] px-3 py-2 text-xs shadow-sm"
            style={{ left: `${activeTooltip.leftPct}%` }}
          >
            <p className="font-bold text-[var(--foreground)]">{activeTooltip.eventType}</p>
            <p className="mt-1 text-[var(--muted-foreground)]">
              {activeTooltip.timeLabel} · {activeTooltip.durationLabel}
            </p>
          </div>
        ) : null}

        <div
          ref={navigatorRef}
          className="relative h-9 touch-pan-y rounded-[12px] bg-[rgba(125,105,93,0.14)]"
          aria-label="Whole-night event navigator"
          onPointerMove={handleNavigatorPointerMove}
          onPointerUp={finishWindowDrag}
          onPointerCancel={finishWindowDrag}
        >
          <div
            className={`absolute inset-y-0 rounded-[10px] border-2 border-[var(--accent)] bg-[rgba(82,81,167,0.10)] ${
              selectedTimeDomain ? 'cursor-grab active:cursor-grabbing' : ''
            }`}
            style={{ left: `${windowLeftPct}%`, width: `${Math.min(windowWidthPct, 100 - windowLeftPct)}%` }}
            aria-label="Selected graph window"
            onPointerDown={handleWindowPointerDown}
          />
          {events.map((evt) => {
            const isSelected = selectedEventId === evt.id
            const ts = eventTimestamp(evt, fallbackStartTs)
            const markerStartTs = evt.duration_seconds
              ? Math.max(domainStart, ts - evt.duration_seconds * 1000)
              : ts
            const widthPct = evt.duration_seconds
              ? Math.max((evt.duration_seconds * 1000 / timelineDurationMs) * 100, isSelected ? 0.8 : 0.3)
              : 0.3
            const xPct = Math.min(Math.max(((markerStartTs - domainStart) / timelineDurationMs) * 100, 0), 100 - widthPct)
            const color = EVENT_COLORS[evt.event_type] ?? '#888'
            const timeLabel = fmtTime(ts)
            const durationLabel = evt.duration_seconds ? `${evt.duration_seconds}s` : 'Duration not available'
            return (
              <button
                key={evt.id}
                type="button"
                aria-label={`${evt.event_type} at ${timeLabel}`}
                className={`absolute rounded-sm opacity-85 transition focus:outline-none focus:ring-2 focus:ring-[var(--accent)] ${
                  isSelected
                    ? 'top-1 h-7 z-10 min-w-1.5 ring-2 ring-white shadow-[0_0_0_3px_rgba(148,139,255,0.35),0_0_18px_rgba(148,139,255,0.75)]'
                    : 'top-1.5 h-6 min-w-1'
                }`}
                style={{
                  left: `${xPct}%`,
                  width: `${widthPct}%`,
                  background: color,
                }}
                onBlur={() => setActiveTooltip(null)}
                onFocus={() =>
                  setActiveTooltip({
                    eventType: evt.event_type,
                    timeLabel,
                    durationLabel,
                    leftPct: Math.min(Math.max(xPct + widthPct / 2, 6), 94),
                  })
                }
                onMouseEnter={() =>
                  setActiveTooltip({
                    eventType: evt.event_type,
                    timeLabel,
                    durationLabel,
                    leftPct: Math.min(Math.max(xPct + widthPct / 2, 6), 94),
                  })
                }
                onMouseLeave={() => setActiveTooltip(null)}
                onClick={() => onSelectEvent?.(evt)}
              />
            )
          })}
          {!events.length ? (
            <span className="absolute inset-0 flex items-center justify-center text-[10px] font-semibold text-[var(--muted-foreground)]">
              No respiratory events recorded this session
            </span>
          ) : null}
        </div>
        <p className="mt-2 text-xs text-[var(--muted-foreground)]">
          Whole-night navigator. Drag the outlined window to pan all graph tracks.
        </p>
      </div>
    </div>
  )
}
