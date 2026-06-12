import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { WaveformSignalResponse } from '../api/client'
import { getDisplayTz } from '../lib/displayTz'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'

function formatTime(value: unknown) {
  return new Date(Number(value)).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    timeZone: getDisplayTz(),
  })
}

export default function FullNightFlowChart({ waveform }: { waveform: WaveformSignalResponse }) {
  const data = waveform.timestamps.map((timestamp, index) => ({
    ts: new Date(timestamp).getTime(),
    value: waveform.values[index],
  }))
  const finiteValues = waveform.values.filter((value): value is number => value != null)
  const amplitude = Math.max(1, ...finiteValues.map((value) => Math.abs(value)))
  const limit = Math.ceil(amplitude * 10) / 10

  return (
    <Card>
      <CardHeader>
        <CardTitle>Full-night flow rate</CardTitle>
        <CardDescription>
          ResMed Flow.40ms at {waveform.sample_rate_hz.toFixed(0)} Hz, stored in compressed chunks.
          The whole-night view preserves local peaks while limiting rendered points.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <p className="mb-1 text-xs font-bold uppercase tracking-[0.14em] text-[var(--muted-foreground)]">
          Flow ({waveform.unit})
        </p>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={data} margin={{ top: 6, right: 14, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(125,105,93,0.18)" vertical={false} />
            <XAxis
              dataKey="ts"
              type="number"
              domain={['dataMin', 'dataMax']}
              scale="time"
              tick={{ fill: '#7d695d', fontSize: 10 }}
              tickFormatter={formatTime}
              minTickGap={42}
            />
            <YAxis
              domain={[-limit, limit]}
              tick={{ fill: '#7d695d', fontSize: 10 }}
              width={44}
            />
            <ReferenceLine y={0} stroke="rgba(125,105,93,0.45)" />
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
      </CardContent>
    </Card>
  )
}
