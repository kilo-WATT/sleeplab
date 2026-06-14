import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import EventTimeline from './EventTimeline'

describe('EventTimeline', () => {
  it('keeps events after summed session duration inside the timeline bar', () => {
    render(
      <EventTimeline
        startDatetime="2026-06-02T03:57:00Z"
        durationSeconds={16440}
        wholeNightDomain={[
          new Date('2026-06-02T03:57:00Z').getTime(),
          new Date('2026-06-02T08:47:00Z').getTime(),
        ]}
        events={[
          {
            id: 1,
            event_type: 'Hypopnea',
            onset_seconds: 17400,
            duration_seconds: null,
            event_datetime: '2026-06-02T08:47:00Z',
          },
        ]}
      />,
    )

    const marker = screen.getByRole('button', { name: /hypopnea/i })

    expect(Number.parseFloat(marker.style.left)).toBeLessThanOrEqual(100)
    expect(Number.parseFloat(marker.style.left) + Number.parseFloat(marker.style.width)).toBeLessThanOrEqual(100)
  })

  it('positions events against the provided wall-clock domain', () => {
    const domainStart = new Date('2026-06-02T03:45:00Z').getTime()
    const domainEnd = new Date('2026-06-02T10:45:00Z').getTime()

    render(
      <EventTimeline
        startDatetime="2026-06-02T03:57:00Z"
        durationSeconds={16440}
        wholeNightDomain={[domainStart, domainEnd]}
        events={[
          {
            id: 1,
            event_type: 'Hypopnea',
            onset_seconds: 17400,
            duration_seconds: null,
            event_datetime: '2026-06-02T08:47:00Z',
          },
        ]}
      />,
    )

    const marker = screen.getByRole('button', { name: /hypopnea/i })

    expect(Number.parseFloat(marker.style.left)).toBeGreaterThan(70)
    expect(Number.parseFloat(marker.style.left)).toBeLessThan(75)
  })

  it('positions duration events from their start when the stored timestamp is the end', () => {
    const domainStart = new Date('2026-06-02T04:00:00Z').getTime()
    const domainEnd = new Date('2026-06-02T04:10:00Z').getTime()

    render(
      <EventTimeline
        startDatetime="2026-06-02T04:00:00Z"
        durationSeconds={600}
        wholeNightDomain={[domainStart, domainEnd]}
        events={[
          {
            id: 1,
            event_type: 'Large Leak',
            onset_seconds: 300,
            duration_seconds: 120,
            event_datetime: '2026-06-02T04:05:00Z',
          },
        ]}
      />,
    )

    const marker = screen.getByRole('button', { name: /large leak/i })
    expect(Number.parseFloat(marker.style.left)).toBeCloseTo(30)
    expect(Number.parseFloat(marker.style.width)).toBeCloseTo(20)
  })

  it('positions respiratory event duration forward from the stored onset', () => {
    const domainStart = new Date('2026-06-02T04:00:00Z').getTime()
    const domainEnd = new Date('2026-06-02T04:10:00Z').getTime()

    render(
      <EventTimeline
        startDatetime="2026-06-02T04:00:00Z"
        durationSeconds={600}
        wholeNightDomain={[domainStart, domainEnd]}
        events={[
          {
            id: 1,
            event_type: 'Obstructive Apnea',
            onset_seconds: 300,
            duration_seconds: 60,
            event_datetime: '2026-06-02T04:05:00Z',
          },
        ]}
      />,
    )

    const marker = screen.getByRole('button', { name: /obstructive apnea/i })
    expect(Number.parseFloat(marker.style.left)).toBeCloseTo(50)
    expect(Number.parseFloat(marker.style.width)).toBeCloseTo(10)
  })

  it('keeps whole-night events visible while showing the selected graph window', () => {
    const domainStart = new Date('2026-06-02T04:00:00Z').getTime()
    const domainEnd = new Date('2026-06-02T04:10:00Z').getTime()

    render(
      <EventTimeline
        startDatetime="2026-06-02T04:00:00Z"
        durationSeconds={3600}
        wholeNightDomain={[domainStart, new Date('2026-06-02T05:00:00Z').getTime()]}
        selectedTimeDomain={[domainStart, domainEnd]}
        events={[
          {
            id: 1,
            event_type: 'Hypopnea',
            onset_seconds: 300,
            duration_seconds: 10,
            event_datetime: '2026-06-02T04:05:00Z',
          },
          {
            id: 2,
            event_type: 'Obstructive Apnea',
            onset_seconds: 1800,
            duration_seconds: 10,
            event_datetime: '2026-06-02T04:30:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByRole('button', { name: /hypopnea/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /obstructive apnea/i })).toBeInTheDocument()
    expect(Number.parseFloat(screen.getByLabelText('Selected graph window').style.width)).toBeCloseTo(16.67, 1)
  })

  it('renders a selected event as one solid marker without a second selection ring', () => {
    const domainStart = new Date('2026-06-02T04:00:00Z').getTime()

    render(
      <EventTimeline
        startDatetime="2026-06-02T04:00:00Z"
        durationSeconds={3600}
        wholeNightDomain={[domainStart, domainStart + 3_600_000]}
        selectedTimeDomain={[domainStart + 1_500_000, domainStart + 2_100_000]}
        selectedEventId={2}
        events={[
          {
            id: 2,
            event_type: 'Obstructive Apnea',
            onset_seconds: 1800,
            duration_seconds: 10,
            event_datetime: '2026-06-02T04:30:00Z',
          },
        ]}
      />,
    )

    const marker = screen.getByRole('button', { name: /obstructive apnea/i })
    expect(marker).toHaveAttribute('aria-pressed', 'true')
    expect(marker).toHaveClass('opacity-100')
    expect(marker).not.toHaveClass('ring-2', 'ring-white')
    expect(marker.className).not.toContain('shadow-[')
  })

  it('pans the selected window by dragging the navigator box', () => {
    const domainStart = new Date('2026-06-02T04:00:00Z').getTime()
    const onWindowChange = vi.fn()
    render(
      <EventTimeline
        startDatetime="2026-06-02T04:00:00Z"
        durationSeconds={3600}
        wholeNightDomain={[domainStart, domainStart + 3_600_000]}
        selectedTimeDomain={[domainStart, domainStart + 600_000]}
        events={[]}
        onWindowChange={onWindowChange}
      />,
    )

    const navigator = screen.getByLabelText('Whole-night event navigator')
    vi.spyOn(navigator, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      left: 0,
      top: 0,
      right: 600,
      bottom: 36,
      width: 600,
      height: 36,
      toJSON: () => ({}),
    })
    const selectedWindow = screen.getByLabelText('Selected graph window')
    fireEvent.pointerDown(selectedWindow, { clientX: 0, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerMove(navigator, { clientX: 100, pointerId: 1, pointerType: 'mouse' })
    fireEvent.pointerUp(navigator, { clientX: 100, pointerId: 1, pointerType: 'mouse' })

    expect(onWindowChange).toHaveBeenCalledWith([
      domainStart + 600_000,
      domainStart + 1_200_000,
    ])
  })
})
