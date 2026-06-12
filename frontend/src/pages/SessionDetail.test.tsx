import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { setDisplayTz } from '../lib/displayTz'
import SessionDetail from './SessionDetail'

const apiMock = vi.hoisted(() => ({
  getSessionByDate: vi.fn(),
  getEvents: vi.fn(),
  getMetrics: vi.fn(),
  getSessions: vi.fn(),
  getInferredEquipment: vi.fn(),
  getWearableData: vi.fn(),
  getSessionSpo2: vi.fn(),
  getSessionTherapyContext: vi.fn(),
  getEventWindow: vi.fn(),
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

    expect(await screen.findByText(/03:59 AM/)).toBeInTheDocument()
    expect(screen.getByText(/09:31 AM/)).toBeInTheDocument()
  })

  it('shows leak P95 in L/min and never pressure units or pressure P95 on the leak card', async () => {
    // Regression: the leak card previously rendered `P95 {p95_pressure} cmH₂O`,
    // bleeding the pressure 95th percentile (and its unit) onto the leak stat.
    renderSessionDetail()

    const leakLabel = await screen.findByText('Avg leak')
    const leakCard = leakLabel.parentElement as HTMLElement
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
    expect(screen.getByText('Night graphs unavailable')).toBeInTheDocument()
    expect(screen.getByText('Oximetry unavailable')).toBeInTheDocument()
    expect(screen.getByText(/does not yet claim SpO2 or pulse support/)).toBeInTheDocument()
  })
})
