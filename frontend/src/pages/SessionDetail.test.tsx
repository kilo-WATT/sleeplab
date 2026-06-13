import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setDisplayTz } from '../lib/displayTz'
import SessionDetail from './SessionDetail'

const apiMock = vi.hoisted(() => ({
  getSessionByDate: vi.fn(),
  getEvents: vi.fn(),
  getMetrics: vi.fn(),
  getWaveform: vi.fn(),
  getSessions: vi.fn(),
  getInferredEquipment: vi.fn(),
  getWearableData: vi.fn(),
  getSessionSpo2: vi.fn(),
  getSessionTherapyContext: vi.fn(),
  updateSessionTimezone: vi.fn(),
  getImportSettings: vi.fn(),
}))

vi.mock('../api/client', () => ({
  api: apiMock,
}))

function sessionDetail(machineTz: string | null) {
  return {
    id: '11111111-1111-1111-1111-111111111111',
    session_id: '11111111-1111-1111-1111-111111111111',
    folder_date: '2026-06-01',
    block_index: 0,
    start_datetime: '2026-06-02T03:59:51Z',
    end_datetime: '2026-06-02T09:31:00Z',
    pld_start_datetime: '2026-06-02T03:59:51Z',
    duration_seconds: 4680,
    duration_hours: 1.3,
    ahi: 1.2,
    central_apnea_count: 0,
    obstructive_apnea_count: 1,
    hypopnea_count: 1,
    apnea_count: 1,
    arousal_count: null,
    total_ahi_events: 2,
    avg_pressure: 4,
    p95_pressure: 6,
    avg_leak: 0.01,
    p95_leak: 0.04,
    leak_unit: 'L/s',
    has_spo2: false,
    machine_tz: machineTz,
    device_serial: 'SN12345',
    therapy_score: {
      total: 100,
      grade: 'A',
      low_confidence: false,
      callout: 'Excellent therapy night.',
      components: {
        ahi: { score: 40, max_score: 40, label: 'AHI', value: 1.2, unit: 'events/hr' },
        leak: { score: 25, max_score: 25, label: 'Leak', value: 10, unit: 'mL/s' },
        duration: { score: 20, max_score: 20, label: 'Duration', value: 1.3, unit: 'hours' },
        spo2: null,
      },
    },
    score_vs_30d_avg: null,
    note: null,
    tags: [],
    avg_resp_rate: 14,
    avg_tidal_vol: null,
    avg_min_vent: null,
    avg_snore: null,
    avg_flow_lim: null,
    avg_spo2: null,
    min_spo2: null,
    therapy_mode: null,
    mask_type: null,
    humidity_level: null,
    temperature_c: null,
    data_availability: {
      import_backend: 'cpap-parser',
      event_count: 2,
      metric_sample_count: 0,
      waveform_sample_count: 0,
      events_available: true,
      therapy_graphs_available: false,
      event_waveforms_available: false,
      full_night_flow_available: false,
      spo2_available: false,
      settings_available: false,
    },
  }
}

const emptyMetrics = {
  timestamps: [],
  mask_pressure: [],
  pressure: [],
  epr_pressure: [],
  leak: [],
  resp_rate: [],
  tidal_vol: [],
  min_vent: [],
  snore: [],
  flow_lim: [],
}

function renderSessionDetail() {
  render(
    <MemoryRouter initialEntries={['/sessions/2026-06-01']}>
      <Routes>
        <Route path="/sessions/:date" element={<SessionDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('SessionDetail timezone display', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setDisplayTz('UTC')
    apiMock.getSessionByDate.mockResolvedValue(sessionDetail('America/New_York'))
    apiMock.getEvents.mockResolvedValue([])
    apiMock.getMetrics.mockResolvedValue(emptyMetrics)
    apiMock.getSessions.mockResolvedValue([])
    apiMock.getInferredEquipment.mockResolvedValue({
      cushion: null,
      headgear: null,
      tubing: null,
      humidifier_chamber: null,
      filter: null,
    })
    apiMock.getWearableData.mockResolvedValue({ hr: [], spo2: [], stages: [] })
    apiMock.getSessionTherapyContext.mockResolvedValue(null)
    apiMock.getImportSettings.mockResolvedValue({ llm_configured: false })
  })

  it('shows stored machine timezone instead of the not-recorded fallback', async () => {
    renderSessionDetail()

    expect(await screen.findByText(/America\/New_York/)).toBeInTheDocument()

    await waitFor(() => {
      expect(screen.queryByText(/timezone not recorded/i)).not.toBeInTheDocument()
    })
  })

  it('uses the stored end timestamp instead of adding usage duration to the start', async () => {
    renderSessionDetail()

    expect((await screen.findAllByText(/03:59 AM/)).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/09:31 AM/).length).toBeGreaterThan(0)
  })

  it('shows leak P95 in L/min and never pressure units or pressure P95 on the leak card', async () => {
    // Regression: the leak card previously rendered `P95 {p95_pressure} cmH₂O`,
    // bleeding the pressure 95th percentile (and its unit) onto the leak stat.
    renderSessionDetail()

    const leakNote = await screen.findByText(
      (_, element) => element?.tagName === 'P' && element.textContent?.includes('P95 2.4') === true,
    )
    const leakCard = leakNote.parentElement as HTMLElement
    // p95_leak 0.04 L/s -> 2.4 L/min, matching OSCAR-style leak P95 reporting.
    expect(leakCard.textContent).toMatch(/P95 2\.4 L\/min/)
    // No pressure unit and no pressure P95 value (6) may appear on the leak card.
    expect(leakCard.textContent).not.toMatch(/cmH₂O/)
    expect(leakCard.textContent).not.toMatch(/P95 6/)
  })

  it('labels the pressure pill as average and surfaces pressure P95 there', async () => {
    renderSessionDetail()

    const pressureCaption = await screen.findByText(
      (_, el) => el?.textContent === 'avg cmH₂O · P95 6.0',
    )
    expect(pressureCaption).toBeInTheDocument()
  })

  it('shows parser coverage and explicit unsupported signal messaging', async () => {
    renderSessionDetail()

    expect(await screen.findByText('Nightly data coverage')).toBeInTheDocument()
    expect(screen.getAllByText('ResMed cpap-parser').length).toBeGreaterThan(0)
    expect(screen.getByText('2 imported')).toBeInTheDocument()
    expect(screen.getByText('Detailed graph tracks unavailable')).toBeInTheDocument()
    expect(screen.getByText('Oximetry unavailable')).toBeInTheDocument()
    expect(screen.getByText(/does not yet claim SpO2 or pulse support/)).toBeInTheDocument()
    expect(screen.getByText('Full-night flow unavailable')).toBeInTheDocument()
    expect(screen.getByText(/Re-import this SD card to populate waveform data/)).toBeInTheDocument()
  })

  it('renders a coherent daily review workspace with mobile event controls', async () => {
    apiMock.getEvents.mockResolvedValue([{
      id: 7,
      event_type: 'Obstructive Apnea',
      onset_seconds: 300,
      duration_seconds: 12,
      event_datetime: '2026-06-02T04:05:00Z',
    }])
    renderSessionDetail()

    expect(await screen.findByRole('heading', { name: 'Daily Review Graphs' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Events' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Whole-night navigator' })).toBeInTheDocument()
    expect(screen.getByLabelText('Event picker')).toBeInTheDocument()
    expect(screen.getByText(/Pressure, leak, flow limitation, respiratory rate/)).toBeInTheDocument()
    expect(screen.getByText('Notes & tags')).toBeInTheDocument()
    expect(screen.getByText('Therapy score')).toBeInTheDocument()
  })

  it('keeps the review flow in clinical reading order', async () => {
    renderSessionDetail()

    const summary = await screen.findByTestId('night-summary')
    const score = screen.getByTestId('therapy-score-card')
    const metrics = screen.getByTestId('core-metrics')
    const aiInsights = screen.getByTestId('ai-insights')
    const graphReview = screen.getByTestId('graph-review')
    const notesTags = screen.getByTestId('notes-tags')
    const supportingContext = screen.getByTestId('supporting-context')
    const oximetry = screen.getByTestId('oximetry-card')

    expect(summary).toContainElement(score)
    expect(score.compareDocumentPosition(metrics) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(summary.compareDocumentPosition(aiInsights) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(aiInsights.compareDocumentPosition(graphReview) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(graphReview.compareDocumentPosition(notesTags) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(notesTags.compareDocumentPosition(supportingContext) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(supportingContext.compareDocumentPosition(oximetry) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByTestId('therapy-machine')).toBeInTheDocument()
    expect(screen.getByTestId('nightly-data-coverage')).toBeInTheDocument()
  })

  it('keeps therapy score compact and the desktop event rail vertical-only', async () => {
    const session = sessionDetail('America/New_York')
    session.therapy_score.components.ahi = { score: 40, max_score: 40, label: 'AHI', value: 1.2, unit: 'events/hr' }
    session.therapy_score.components.leak = { score: 15, max_score: 25, label: 'Leak', value: 10, unit: 'mL/s' }
    session.therapy_score.components.duration = { score: 4, max_score: 20, label: 'Duration', value: 1.3, unit: 'hours' }
    apiMock.getSessionByDate.mockResolvedValue(session)
    apiMock.getEvents.mockResolvedValue([{
      id: 7,
      event_type: 'Obstructive Apnea',
      onset_seconds: 300,
      duration_seconds: 12,
      event_datetime: '2026-06-02T04:05:00Z',
    }])
    renderSessionDetail()

    const score = await screen.findByTestId('therapy-score-card')
    const summary = screen.getByTestId('night-summary')
    const metrics = screen.getByTestId('core-metrics')
    const components = screen.getByTestId('therapy-score-components')
    const eventSelector = screen.getByTestId('desktop-event-selector')

    expect(summary.className).toContain('space-y-3')
    expect(metrics.className).toContain('xl:grid-cols-5')
    expect(components.className).toContain('space-y-2')
    expect(score.className).not.toContain('col-span')
    expect(score.className).not.toContain('w-screen')
    expect(score.className).toContain('overflow-visible')
    expect(score.className).toContain('z-20')
    expect(eventSelector.className).toContain('overflow-hidden')
    expect(eventSelector.querySelector('.overflow-y-auto')).toHaveClass('overflow-x-hidden')
    expect(within(score).queryByText('Pressure')).not.toBeInTheDocument()
    expect(within(score).queryByText('Not scored')).not.toBeInTheDocument()
    expect(within(score).getByText('SpO2')).toBeInTheDocument()
    expect(within(score).getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByTestId('therapy-component-ahi')).toHaveAttribute('data-score-tone', 'good')
    expect(screen.getByTestId('therapy-component-leak')).toHaveAttribute('data-score-tone', 'caution')
    expect(screen.getByTestId('therapy-component-duration')).toHaveAttribute('data-score-tone', 'poor')
    expect(screen.getByTestId('therapy-component-spo2')).toHaveAttribute('data-score-tone', 'unavailable')
  })

  it('selects an event from the event list without rendering duplicate inspector charts', async () => {
    apiMock.getEvents.mockResolvedValue([{
      id: 7,
      event_type: 'Obstructive Apnea',
      onset_seconds: 300,
      duration_seconds: 12,
      event_datetime: '2026-06-02T04:05:00Z',
    }])
    renderSessionDetail()

    fireEvent.click(await screen.findByRole('row', { name: /OA 04:05 AM 12s Obstructive Apnea/i }))

    expect(screen.getByText('Selected event')).toBeInTheDocument()
    expect(screen.queryByText('Event Inspector')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Clear selection' })).toBeInTheDocument()
  })

  it('renders stored full-night parser flow without claiming unsupported signals', async () => {
    const session = sessionDetail('America/New_York')
    session.data_availability.full_night_flow_available = true
    apiMock.getSessionByDate.mockResolvedValue(session)
    apiMock.getWaveform.mockResolvedValue({
      signal_name: 'flow_rate',
      unit: 'L/s',
      sample_rate_hz: 25,
      start_time: '2026-06-02T03:59:51Z',
      end_time: '2026-06-02T03:59:52Z',
      sample_count: 3,
      chunk_count: 1,
      encoding: 'float32-le-zlib-v1',
      returned_sample_count: 3,
      timestamps: [
        '2026-06-02T03:59:51Z',
        '2026-06-02T03:59:51.04Z',
        '2026-06-02T03:59:51.08Z',
      ],
      values: [-0.5, 0, 0.7],
    })

    renderSessionDetail()

    expect(await screen.findByText('Full-night flow rate')).toBeInTheDocument()
    expect(screen.getByText(/ResMed Flow.40ms at 25 Hz/)).toBeInTheDocument()
    expect(screen.queryByText('Full-night flow unavailable')).not.toBeInTheDocument()
  })

  it('centers the shared graph window on an event and shows a compact readout', async () => {
    const session = sessionDetail('America/New_York')
    session.data_availability.full_night_flow_available = true
    const event = {
      id: 7,
      event_type: 'Obstructive Apnea',
      onset_seconds: 300,
      duration_seconds: 12,
      event_datetime: '2026-06-02T04:05:00Z',
    }
    apiMock.getSessionByDate.mockResolvedValue(session)
    apiMock.getEvents.mockResolvedValue([event])
    apiMock.getWaveform.mockResolvedValue({
      signal_name: 'flow_rate',
      unit: 'L/s',
      sample_rate_hz: 25,
      start_time: '2026-06-02T04:00:00Z',
      end_time: '2026-06-02T09:00:00Z',
      sample_count: 450_000,
      chunk_count: 60,
      encoding: 'float32-le-zlib-v1',
      returned_sample_count: 3,
      timestamps: [
        '2026-06-02T04:00:00Z',
        '2026-06-02T06:30:00Z',
        '2026-06-02T09:00:00Z',
      ],
      values: [-0.5, 0, 0.7],
    })
    renderSessionDetail()

    fireEvent.click(await screen.findByRole('button', { name: /obstructive apnea at/i }))

    expect(await screen.findByText('Selected event')).toBeInTheDocument()
    expect(screen.getByLabelText('Selected event flow range')).toHaveTextContent('L/s')
    expect(screen.queryByText('Event Inspector')).not.toBeInTheDocument()
    await waitFor(() => {
      expect(apiMock.getWaveform).toHaveBeenCalledWith(
        session.id,
        'flow_rate',
        expect.objectContaining({
          start_time: '2026-06-02T04:02:30.000Z',
          end_time: '2026-06-02T04:07:30.000Z',
          max_points: 6000,
        }),
      )
    })
  })
})
