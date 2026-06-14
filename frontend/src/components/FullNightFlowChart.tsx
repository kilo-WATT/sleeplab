import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { EventRecord, WaveformSignalResponse } from '../api/client'
import { getDisplayTz } from '../lib/displayTz'
import { eventInterval } from '../lib/eventTiming'
import { ChevronLeftIcon, ChevronRightIcon } from './icons/ChevronIcons'
import { Button } from './ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'

const EVENT_COLORS: Record<string, string> = {
  'Central Apnea': '#5251A7',
  'Obstructive Apnea': '#8E3D40',
  'Hypopnea': '#E9784B',
  'Apnea': '#C9B715',
  'Arousal': '#6AA136',
  'Large Leak': '#b8b8b8',
}

function formatTime(value: unknown) {
  return new Date(Number(value)).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: getDisplayTz(),
  })
}

function timeTicks([start, end]: [number, number]): number[] {
  const span = end - start
  const interval = span <= 10 * 60_000
    ? 60_000
    : span <= 30 * 60_000
      ? 5 * 60_000
      : span <= 2 * 60 * 60_000
        ? 15 * 60_000
        : 60 * 60_000
  const ticks: number[] = []
  const first = Math.ceil(start / interval) * interval
  for (let tick = first; tick <= end; tick += interval) ticks.push(tick)
  return ticks.length >= 2 ? ticks : [start, end]
}

interface Props {
  waveform: WaveformSignalResponse
  events: EventRecord[]
  timeDomain: [number, number]
  wholeNight: boolean
  loading?: boolean
  onSelectWindow: (minutes: number | null) => void
  onPan: (direction: -1 | 1) => void
  onSelectRange: (window: [number, number]) => void
}

export default function FullNightFlowChart({
  waveform,
  events,
  timeDomain,
  wholeNight,
  loading = false,
  onSelectWindow,
  onPan,
  onSelectRange,
}: Props) {
  const selectionRef = useRef<HTMLDivElement>(null)
  const [selection, setSelection] = useState<{
    startX: number
    currentX: number
    left: number
    width: number
  } | null>(null)
  const data = waveform.timestamps.map((timestamp, index) => ({
    ts: new Date(timestamp).getTime(),
    value: waveform.values[index],
  }))
  const finiteValues = waveform.values.filter((value): value is number => value != null)
  const amplitude = Math.max(1, ...finiteValues.map((value) => Math.abs(value)))
  const limit = Math.ceil(amplitude * 10) / 10
  const [domainStart, domainEnd] = timeDomain
  const domainDuration = Math.max(domainEnd - domainStart, 1)
  const xTicks = timeTicks(timeDomain)
  const visibleEvents = events.filter((event) => {
    const { start, end } = eventInterval(event, domainStart)
    return end >= domainStart && start <= domainEnd
  })

  function xToTimestamp(clientX: number) {
    const rect = selectionRef.current?.getBoundingClientRect()
    if (!rect?.width) return domainStart
    const ratio = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1)
    return domainStart + ratio * domainDuration
  }

  function startSelection(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.pointerType === 'touch') return
    const rect = selectionRef.current?.getBoundingClientRect()
    if (!rect?.width) return
    setSelection({ startX: event.clientX, currentX: event.clientX, left: event.clientX - rect.left, width: 0 })
    event.currentTarget.setPointerCapture?.(event.pointerId)
    event.preventDefault()
  }

  function moveSelection(event: ReactPointerEvent<HTMLDivElement>) {
    const rect = selectionRef.current?.getBoundingClientRect()
    if (!rect?.width) return
    setSelection((current) => current ? {
      ...current,
      currentX: event.clientX,
      left: Math.min(current.startX, event.clientX) - rect.left,
      width: Math.abs(event.clientX - current.startX),
    } : null)
  }

  function finishSelection() {
    if (!selection) return
    if (Math.abs(selection.currentX - selection.startX) >= 8) {
      const start = xToTimestamp(Math.min(selection.startX, selection.currentX))
      const end = xToTimestamp(Math.max(selection.startX, selection.currentX))
      onSelectRange([start, end])
    }
    setSelection(null)
  }

  return (
    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="min-w-0 gap-4">
        <div className="min-w-0">
          <CardTitle>Full-night flow rate</CardTitle>
          <CardDescription>
            ResMed Flow.40ms at {waveform.sample_rate_hz.toFixed(0)} Hz. Drag across the desktop chart
            to zoom; visible windows request only overlapping compressed chunks.
          </CardDescription>
        </div>
        <div className="grid grid-cols-2 gap-2 min-[430px]:flex min-[430px]:flex-wrap min-[430px]:items-center" aria-label="Flow chart time window">
          <Button
            variant={wholeNight ? 'default' : 'outline'}
            size="sm"
            className="min-h-11 min-w-0 px-3 sm:min-h-0"
            onClick={() => onSelectWindow(null)}
          >
            Whole night
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="min-h-11 min-w-0 px-3 sm:min-h-0"
            onClick={() => onSelectWindow(null)}
          >
            Reset view
          </Button>
          {[30, 10, 5].map((minutes) => (
            <Button
              key={minutes}
              variant={!wholeNight && Math.round(domainDuration / 60_000) === minutes ? 'default' : 'outline'}
              size="sm"
              className="min-h-11 min-w-0 px-3 sm:min-h-0"
              onClick={() => onSelectWindow(minutes)}
            >
              {minutes} min
            </Button>
          ))}
          <div className="col-span-2 inline-flex w-fit rounded-full border border-[var(--border)] bg-[var(--surface-soft)] p-1 min-[430px]:col-span-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-11 rounded-full px-3 sm:h-7 sm:px-2"
              disabled={wholeNight}
              aria-label="Earlier waveform window"
              onClick={() => onPan(-1)}
            >
              <ChevronLeftIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-11 rounded-full px-3 sm:h-7 sm:px-2"
              disabled={wholeNight}
              aria-label="Later waveform window"
              onClick={() => onPan(1)}
            >
              <ChevronRightIcon className="h-4 w-4" />
            </Button>
          </div>
          {loading ? (
            <span className="text-xs font-semibold text-[var(--muted-foreground)]">Loading window...</span>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="min-w-0 space-y-3 overflow-hidden">
        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
          Flow ({waveform.unit})
        </p>
        <div className="relative min-w-0 overflow-hidden">
          <ResponsiveContainer width="100%" height={220}>
            <LineChart data={data} margin={{ top: 6, right: 14, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(125,105,93,0.18)" vertical={false} />
            <XAxis
              dataKey="ts"
              type="number"
              domain={timeDomain}
              allowDataOverflow
              scale="time"
              ticks={xTicks}
              tick={{ fill: '#7d695d', fontSize: 10 }}
              tickFormatter={formatTime}
              minTickGap={42}
            />
            <YAxis domain={[-limit, limit]} tick={{ fill: '#7d695d', fontSize: 10 }} width={44} />
            <ReferenceLine y={0} stroke="rgba(125,105,93,0.45)" />
            {visibleEvents.map((event) => {
              const { start, end } = eventInterval(event, domainStart)
              const color = EVENT_COLORS[event.event_type] ?? '#888'
              return (
                <ReferenceArea
                  key={event.id}
                  x1={Math.max(start, domainStart)}
                  x2={Math.min(end, domainEnd)}
                  fill={color}
                  fillOpacity={0.12}
                  stroke={color}
                  strokeOpacity={0.55}
                />
              )
            })}
            <Tooltip
              contentStyle={{
                background: 'rgba(255,251,245,0.96)',
                border: '1px solid rgba(125,105,93,0.2)',
                borderRadius: 12,
                color: '#3c2b22',
                fontSize: 12,
              }}
              labelFormatter={formatTime}
              formatter={(value) => [
                value == null ? 'N/A' : `${Number(value).toFixed(3)} ${waveform.unit}`,
                'Flow',
              ]}
            />
            <Line
              type="linear"
              dataKey="value"
              stroke="#5251A7"
              dot={false}
              strokeWidth={1}
              connectNulls={false}
              isAnimationActive={false}
            />
            </LineChart>
          </ResponsiveContainer>
          <div
            ref={selectionRef}
            className="absolute inset-y-0 left-11 right-4 hidden cursor-crosshair touch-pan-y md:block"
            aria-label="Drag to zoom flow chart"
            onPointerDown={startSelection}
            onPointerMove={moveSelection}
            onPointerUp={finishSelection}
            onPointerCancel={() => setSelection(null)}
            onDoubleClick={() => onSelectWindow(null)}
          >
            {selection ? (
              <div
                className="pointer-events-none absolute inset-y-1 rounded border border-[var(--accent)] bg-[rgba(82,81,167,0.14)]"
                style={{ left: selection.left, width: selection.width }}
              />
            ) : null}
          </div>
        </div>
        <p className="text-xs leading-5 text-[var(--muted-foreground)]">
          Showing {waveform.returned_sample_count.toLocaleString()} rendered points from{' '}
          {waveform.sample_count.toLocaleString()} source samples.
          {waveform.sample_count >= 500_000
            ? ' Large-night rendering preserves local extrema within the point limit.'
            : ''}
          {!wholeNight ? ' Flow, pressure, leak, and the event timeline share this time window.' : ''}
        </p>
      </CardContent>
    </Card>
  )
}
