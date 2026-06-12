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

function eventBounds(event: EventRecord): [number, number] {
  const end = new Date(event.event_datetime).getTime()
  const durationMs = Math.max((event.duration_seconds ?? 2) * 1000, 2000)
  return [end - durationMs, end]
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
  onSelectEvent: (event: EventRecord) => void
  onSelectWindow: (minutes: number | null) => void
  onPan: (direction: -1 | 1) => void
}

export default function FullNightFlowChart({
  waveform,
  events,
  timeDomain,
  wholeNight,
  loading = false,
  onSelectEvent,
  onSelectWindow,
  onPan,
}: Props) {
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
    const [start, end] = eventBounds(event)
    return end >= domainStart && start <= domainEnd
  })

  return (
    <Card>
      <CardHeader className="gap-4">
        <div>
          <CardTitle>Full-night flow rate</CardTitle>
          <CardDescription>
            ResMed Flow.40ms at {waveform.sample_rate_hz.toFixed(0)} Hz. Event markers open the detailed
            inspector; window controls request only overlapping compressed chunks.
          </CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2" aria-label="Flow chart time window">
          <Button variant={wholeNight ? 'default' : 'outline'} size="sm" onClick={() => onSelectWindow(null)}>
            Whole night
          </Button>
          {[30, 10, 5].map((minutes) => (
            <Button
              key={minutes}
              variant={!wholeNight && Math.round(domainDuration / 60_000) === minutes ? 'default' : 'outline'}
              size="sm"
              onClick={() => onSelectWindow(minutes)}
            >
              {minutes} min
            </Button>
          ))}
          <div className="inline-flex rounded-full border border-[var(--border)] bg-[var(--surface-soft)] p-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 rounded-full px-2"
              disabled={wholeNight}
              aria-label="Earlier waveform window"
              onClick={() => onPan(-1)}
            >
              <ChevronLeftIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 rounded-full px-2"
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
      <CardContent className="space-y-3">
        <div className="relative h-7 rounded-[10px] bg-[var(--surface-soft)]" aria-label="Flow event markers">
          {visibleEvents.length ? visibleEvents.map((event) => {
            const [eventStart, eventEnd] = eventBounds(event)
            const clippedStart = Math.max(eventStart, domainStart)
            const clippedEnd = Math.min(eventEnd, domainEnd)
            const left = ((clippedStart - domainStart) / domainDuration) * 100
            const width = Math.max(((clippedEnd - clippedStart) / domainDuration) * 100, 0.45)
            const time = formatTime(eventEnd)
            return (
              <button
                key={event.id}
                type="button"
                className="absolute top-1 h-5 min-w-1 rounded-sm opacity-90 transition hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                style={{
                  left: `${Math.min(Math.max(left, 0), 99.5)}%`,
                  width: `${Math.min(width, Math.max(0.5, 100 - left))}%`,
                  background: EVENT_COLORS[event.event_type] ?? '#888',
                }}
                aria-label={`Inspect ${event.event_type} at ${time}`}
                title={`${event.event_type} at ${time}`}
                onClick={() => onSelectEvent(event)}
              />
            )
          }) : (
            <span className="flex h-full items-center px-3 text-xs text-[var(--muted-foreground)]">
              No scored respiratory events in this window.
            </span>
          )}
        </div>
        <p className="text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
          Flow ({waveform.unit})
        </p>
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
              const [start, end] = eventBounds(event)
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
