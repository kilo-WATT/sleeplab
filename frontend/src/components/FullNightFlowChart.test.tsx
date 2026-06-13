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
  it('renders explicit units and large-night point-limit messaging', () => {
    render(
      <FullNightFlowChart
        waveform={waveform}
        events={[event]}
        timeDomain={[
          new Date(waveform.start_time).getTime(),
          new Date(waveform.end_time).getTime(),
        ]}
        wholeNight
        onSelectWindow={vi.fn()}
        onPan={vi.fn()}
        onSelectRange={vi.fn()}
      />,
    )

    expect(screen.getByText('Flow (L/s)')).toBeInTheDocument()
    expect(screen.getByText(/900,000 source samples/)).toBeInTheDocument()
    expect(screen.getByText(/preserves local extrema/)).toBeInTheDocument()
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
        onSelectWindow={onSelectWindow}
        onPan={onPan}
        onSelectRange={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '10 min' }))
    fireEvent.click(screen.getByRole('button', { name: 'Earlier waveform window' }))
    fireEvent.click(screen.getByRole('button', { name: 'Whole night' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reset view' }))

    expect(onSelectWindow).toHaveBeenNthCalledWith(1, 10)
    expect(onPan).toHaveBeenCalledWith(-1)
    expect(onSelectWindow).toHaveBeenNthCalledWith(2, null)
    expect(onSelectWindow).toHaveBeenNthCalledWith(3, null)
  })

  it('emits a time range after a desktop drag selection', () => {
    const onSelectRange = vi.fn()
    render(
      <FullNightFlowChart
        waveform={waveform}
        events={[event]}
        timeDomain={[
          new Date(waveform.start_time).getTime(),
          new Date(waveform.end_time).getTime(),
        ]}
        wholeNight
        onSelectWindow={vi.fn()}
        onPan={vi.fn()}
        onSelectRange={onSelectRange}
      />,
    )

    const overlay = screen.getByLabelText('Drag to zoom flow chart')
    vi.spyOn(overlay, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 1000,
      bottom: 200,
      width: 1000,
      height: 200,
      toJSON: () => ({}),
    })
    fireEvent.pointerDown(overlay, { clientX: 250, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerMove(overlay, { clientX: 750, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerUp(overlay, { clientX: 750, pointerId: 1, pointerType: 'mouse' })

    const start = new Date(waveform.start_time).getTime()
    expect(onSelectRange).toHaveBeenCalledWith([
      start + 150_000,
      start + 450_000,
    ])
  })
})
