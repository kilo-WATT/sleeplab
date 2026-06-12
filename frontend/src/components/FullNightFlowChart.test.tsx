import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FullNightFlowChart from './FullNightFlowChart'

const waveform = {
  signal_name: 'flow_rate',
  unit: 'L/s',
  sample_rate_hz: 25,
  start_time: '2026-06-02T04:00:00Z',
  end_time: '2026-06-02T04:10:00Z',
  sample_count: 900_000,
  chunk_count: 2,
  encoding: 'float32-le-zlib-v1',
  returned_sample_count: 3,
  timestamps: [
    '2026-06-02T04:00:00Z',
    '2026-06-02T04:05:00Z',
    '2026-06-02T04:10:00Z',
  ],
  values: [-1, 0.5, 1],
}

const event = {
  id: 7,
  event_type: 'Obstructive Apnea',
  onset_seconds: 300,
  duration_seconds: 12,
  event_datetime: '2026-06-02T04:05:00Z',
}

describe('FullNightFlowChart', () => {
  it('renders explicit units, large-night point-limit messaging, and clickable event markers', () => {
    const onSelectEvent = vi.fn()

    render(
      <FullNightFlowChart
        waveform={waveform}
        events={[event]}
        timeDomain={[
          new Date(waveform.start_time).getTime(),
          new Date(waveform.end_time).getTime(),
        ]}
        wholeNight
        onSelectEvent={onSelectEvent}
        onSelectWindow={vi.fn()}
        onPan={vi.fn()}
      />,
    )

    expect(screen.getByText('Flow (L/s)')).toBeInTheDocument()
    expect(screen.getByText(/900,000 source samples/)).toBeInTheDocument()
    expect(screen.getByText(/preserves local extrema/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /inspect obstructive apnea/i }))
    expect(onSelectEvent).toHaveBeenCalledWith(event)
  })

  it('exposes selectable windows and pan controls', () => {
    const onSelectWindow = vi.fn()
    const onPan = vi.fn()

    render(
      <FullNightFlowChart
        waveform={waveform}
        events={[]}
        timeDomain={[
          new Date(waveform.start_time).getTime(),
          new Date(waveform.end_time).getTime(),
        ]}
        wholeNight={false}
        onSelectEvent={vi.fn()}
        onSelectWindow={onSelectWindow}
        onPan={onPan}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '10 min' }))
    fireEvent.click(screen.getByRole('button', { name: 'Earlier waveform window' }))
    fireEvent.click(screen.getByRole('button', { name: 'Whole night' }))

    expect(onSelectWindow).toHaveBeenNthCalledWith(1, 10)
    expect(onPan).toHaveBeenCalledWith(-1)
    expect(onSelectWindow).toHaveBeenNthCalledWith(2, null)
  })
})
